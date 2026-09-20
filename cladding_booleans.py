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

from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely.prepared import prep

from cladding_primitives import splash_rings

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
