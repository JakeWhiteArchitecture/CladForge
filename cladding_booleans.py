"""
CladForge — export-quality trimming with Shapely.

Cuts battens, counter-battens, noggins and boards to the elevation outline
(openings become holes or notches) and removes them from splash zones.
Sheathing and insulation already follow the outline and run through the
splash zone, so they are left alone.

Runs in Pyodide for the live preview whenever params["trim"] is true, and
always before IFC/DXF export. Every GEOS call is wrapped: a TopologyException
raised inside WASM cannot be caught by the caller, so it is caught here.
"""

import copy

from shapely.geometry import Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely.prepared import prep

from cladding_primitives import clip_bounds, clip_bounds_v, splash_rings

TRIMMABLE = frozenset({"batten", "counter_batten", "cross_batten", "plank", "panel"})
_MIN_AREA = 25.0   # mm² – slivers smaller than this are discarded


def _safe(op, a, b):
    """Run a binary Shapely op, retrying on buffer(0)-repaired inputs."""
    try:
        return op(a, b)
    except Exception:
        pass
    try:
        return op(a.buffer(0), b.buffer(0))
    except Exception:
        return None


def _difference(a, b):
    return _safe(lambda x, y: x.difference(y), a, b)


def _intersection(a, b):
    return _safe(lambda x, y: x.intersection(y), a, b)


def region_polygon(elev):
    """Shapely geometry of the elevation outline with its openings as holes."""
    polys = []
    for poly in elev.get("polygons", []):
        try:
            pg = Polygon(poly["exterior"], poly.get("holes") or [])
            if not pg.is_valid:
                pg = pg.buffer(0)
            if not pg.is_empty:
                polys.append(pg)
        except Exception:
            continue
    if not polys:
        return Polygon()
    try:
        return unary_union(polys)
    except Exception:
        return polys[0]


def clip_elevation(elev):
    """The elevation with its polygons cut back to the clad part of the face: sideways
    where a wall runs past a corner, because the rest of it belongs to the other face,
    and vertically to the top and bottom levels picked for the chain."""
    lo, hi = clip_bounds(elev)
    v_lo, v_hi = clip_bounds_v(elev)
    if (lo <= 0.001 and hi >= float(elev["width"]) - 0.001
            and v_lo <= 0.001 and v_hi >= float(elev["height"]) - 0.001):
        return elev
    band = box(lo, v_lo, hi, v_hi)
    polygons = []
    for poly in elev.get("polygons", []):
        try:
            pg = Polygon(poly["exterior"], poly.get("holes") or [])
            cut = _intersection(pg if pg.is_valid else pg.buffer(0), band)
        except Exception:
            continue
        for part in iter_polygons(cut):
            if part.area <= 1.0:
                continue
            ext, holes = polygon_to_rings(part)
            polygons.append({"exterior": ext, "holes": holes})
    return dict(elev, polygons=polygons)


def strip_intervals(region, lo, hi, across=False, min_len=1.0):
    """Where a course actually runs, as (start, end) along its own direction. Boards are
    set out along these rather than the full face, so a run broken by a gable, a splash
    zone or an opening is not given seams it does not need. *across* False takes a
    horizontal band between the levels lo..hi; True takes a vertical column."""
    if region is None or region.is_empty:
        return []
    strip = _intersection(region, box(lo, -1e7, hi, 1e7) if across else box(-1e7, lo, 1e7, hi))
    spans = []
    for part in iter_polygons(strip):
        u0, v0, u1, v1 = part.bounds
        a, b = (v0, v1) if across else (u0, u1)
        if b - a >= min_len:
            spans.append((a, b))
    return _merge_spans(spans, min_len)


def _merge_spans(spans, tol):
    out = []
    for u0, u1 in sorted(spans):
        if out and u0 - out[-1][1] <= tol:
            out[-1] = (out[-1][0], max(out[-1][1], u1))
        else:
            out.append((u0, u1))
    return out


def clip_region(elev, splash):
    """Outline minus splash-zone bands: the area battens and cladding may occupy."""
    region = region_polygon(elev)
    bands = []
    for ring in splash_rings(elev, splash):
        try:
            pg = Polygon(ring)
            bands.append(pg if pg.is_valid else pg.buffer(0))
        except Exception:
            continue
    if bands and not region.is_empty:
        cut = _difference(region, unary_union(bands))
        if cut is not None:
            region = cut
    return region


def iter_polygons(geom):
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == "Polygon":
        yield geom
    elif hasattr(geom, "geoms"):
        for g in geom.geoms:
            for pg in iter_polygons(g):
                yield pg


def polygon_to_rings(pg):
    """(exterior, holes) as lists of [u, v] rounded to 0.01 mm, CCW outer / CW holes."""
    pg = orient(pg, 1.0)
    ext = [[round(x, 2), round(y, 2)] for x, y in list(pg.exterior.coords)[:-1]]
    holes = [[[round(x, 2), round(y, 2)] for x, y in list(r.coords)[:-1]] for r in pg.interiors]
    return ext, holes


def _prism_polygon(mesh):
    try:
        pg = Polygon(mesh["profile"], mesh.get("holes") or [])
        return pg if pg.is_valid else pg.buffer(0)
    except Exception:
        return None


def apply_boolean_ops(meshes, p):
    """Return a new mesh list with every trimmable prism clipped to its elevation.

    *p* is the parsed parameter dict (needs "elevations" and "splash").
    Elements that fall entirely inside an opening or splash zone are dropped;
    elements split by an opening become several prisms with a letter suffix.
    """
    regions, prepared = {}, {}
    for elev in p.get("elevations", []):
        region = clip_region(elev, p["splash"])
        regions[elev.get("name", "")] = region
        try:
            prepared[elev.get("name", "")] = prep(region)
        except Exception:
            prepared[elev.get("name", "")] = None

    out = []
    for mesh in meshes:
        if mesh.get("ifc_type") not in TRIMMABLE:
            out.append(mesh)
            continue
        region = regions.get(mesh.get("elevation"))
        if region is None or region.is_empty:
            continue
        poly = _prism_polygon(mesh)
        if poly is None or poly.is_empty:
            continue
        pre = prepared.get(mesh.get("elevation"))
        try:
            if pre is not None and pre.contains(poly):
                out.append(mesh)          # fast path: nothing to cut
                continue
        except Exception:
            pass
        clipped = _intersection(poly, region)
        parts = [pg for pg in iter_polygons(clipped) if pg.area > _MIN_AREA]
        if not parts:
            continue
        parts.sort(key=lambda pg: (pg.bounds[1], pg.bounds[0]))
        for i, pg in enumerate(parts):
            m = copy.copy(mesh)
            m["profile"], m["holes"] = polygon_to_rings(pg)
            if len(parts) > 1:
                m["name"] = "%s%s" % (mesh.get("name", ""), chr(97 + i) if i < 26 else str(i))
            out.append(m)
    return out
