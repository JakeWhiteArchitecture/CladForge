"""
CladForge — export-quality trimming with Shapely.

Cuts battens, counter-battens, noggins and boards to the elevation outline
(openings become holes or notches) and removes them from splash zones.
Sheathing and insulation already follow the outline and run through the
splash zone, so they are left alone.

Runs in Pyodide for the live preview whenever params["trim"] is true, and
always before IFC/DXF export. A TopologyException inside WASM is a C++ abort that
kills the runtime: try/except never sees it. So the cutting ops look before they
cut (near_misses) and cut on a 0.01 mm grid, which always nodes, where shapes
nearly meet; the try/except is only for errors that do raise.
"""

import copy
import math

import shapely

from shapely.geometry import Polygon, box
from shapely.geometry.polygon import orient
from shapely.affinity import scale
from shapely.ops import unary_union
from shapely.prepared import prep

from cladding_primitives import clip_bounds, clip_bounds_v, splash_rings, openings, ROW_MIN
from cladding_edges import offset_region

TRIMMABLE = frozenset({"batten", "counter_batten", "cross_batten", "plank", "panel", "gasket"})
_MIN_AREA = 25.0   # mm² – slivers smaller than this are discarded
ROBUST_GRID = 0.01        # mm – an op on shapes that nearly meet runs on this grid
NEAR_MISS = 0.5           # mm – vertices or edges this close that do not meet make an op risky
MEETS = 1e-6              # mm – closer than this is meeting (float noise), not a near miss
NEAR_MISS_WORK = 400_000  # vertex x edge checks past which an op is treated as risky unchecked


def _vertices(geoms):
    out = []
    for g in geoms:
        if g is None or g.is_empty:
            continue
        if hasattr(g, "geoms"):
            out += _vertices(list(g.geoms))
        elif g.geom_type == "Polygon":
            out += [c[:2] for ring in [g.exterior, *g.interiors] for c in ring.coords]
        else:
            out += [c[:2] for c in g.coords]
    return out


def _segments(geoms):
    out = []
    for g in geoms:
        if g is None or g.is_empty:
            continue
        if hasattr(g, "geoms"):
            out += _segments(list(g.geoms))
            continue
        rings = [g.exterior, *g.interiors] if g.geom_type == "Polygon" else [g]
        for ring in rings:
            cs = [c[:2] for c in ring.coords]
            out += list(zip(cs, cs[1:]))
    return out


def _close(pts, segs, box_=None, meets=MEETS):
    """Does a point come within NEAR_MISS of another point or of an edge, not meeting it
    (closer than *meets* is meeting)?"""
    if box_ is not None:
        x0, y0, x1, y1 = box_[0] - NEAR_MISS, box_[1] - NEAR_MISS, box_[2] + NEAR_MISS, box_[3] + NEAR_MISS
        pts = [(x, y) for x, y in pts if x0 <= x <= x1 and y0 <= y <= y1]
        segs = [s for s in segs if max(s[0][0], s[1][0]) >= x0 and min(s[0][0], s[1][0]) <= x1
                and max(s[0][1], s[1][1]) >= y0 and min(s[0][1], s[1][1]) <= y1]
    if len(pts) * (len(segs) + 1) > NEAR_MISS_WORK:
        return True
    for (ax, ay), (bx, by) in segs:
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        for x, y in pts:
            if x < min(ax, bx) - NEAR_MISS or x > max(ax, bx) + NEAR_MISS or y < min(ay, by) - NEAR_MISS or y > max(ay, by) + NEAR_MISS:
                continue
            for qx, qy in ((ax, ay), (bx, by)):                  # near an end
                if meets < math.hypot(x - qx, y - qy) <= NEAR_MISS:
                    return True
            if L2 > 0.0:
                t = ((x - ax) * dx + (y - ay) * dy) / L2
                if 0.0 < t < 1.0 and meets < abs((x - ax) * dy - (y - ay) * dx) / math.sqrt(L2) <= NEAR_MISS:
                    return True
    return False


def near_misses(geoms, others=None):
    """Do any vertices, or a vertex and an edge, come within NEAR_MISS of each other without
    meeting? Edges that nearly coincide (two ends 0.02 mm apart, a corner a hair off a
    sloped line) are what GEOS's floating-point overlay fails to node. With *others*,
    only across the two sets, near where they overlap: a board against its outline. There
    nothing under NEAR_MISS counts as meeting but exactly: a batten's corner a billionth of
    a millimetre off a gable's slope is the case GEOS cannot node, and a cut snapped to the
    grid costs a board nothing. Within one set (the extractor's unions) float noise under
    MEETS is meeting, so a clean rotated face keeps its exact coordinates."""
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if others is None:
        return _close(_vertices(geoms), _segments(geoms))
    others = [g for g in others if g is not None and not g.is_empty]
    if not geoms or not others:
        return False
    a, b = shapely.total_bounds(geoms), shapely.total_bounds(others)
    near = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    if near[0] > near[2] + NEAR_MISS or near[1] > near[3] + NEAR_MISS:
        return False
    return (_close(_vertices(geoms), _segments(others), near, 0.0)
            or _close(_vertices(others), _segments(geoms), near, 0.0))


def _areal(g):
    """Only the polygons of *g*: the grid overlay refuses a collection that mixes them with
    the lines or points a touching cut leaves (a C++ abort, like a TopologyException)."""
    if g is None or g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    parts = [q for q in getattr(g, "geoms", [g]) if q.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.union_all(parts, grid_size=ROBUST_GRID) if parts else Polygon()


def _safe(op, a, b):
    """A binary Shapely op that cannot kill the engine: on a 0.01 mm grid where the two
    shapes nearly meet (their polygons only), exactly otherwise; retried on buffer(0)-
    repaired inputs if it raises."""
    risky = near_misses([a], [b])
    try:
        return op(_areal(a), _areal(b), ROBUST_GRID) if risky else op(a, b, None)
    except Exception:
        pass
    try:
        return op(_areal(a.buffer(0)), _areal(b.buffer(0)), ROBUST_GRID)
    except Exception:
        return None


def _difference(a, b):
    return _safe(lambda x, y, g: shapely.difference(x, y, grid_size=g), a, b)


def _intersection(a, b):
    return _safe(lambda x, y, g: shapely.intersection(x, y, grid_size=g), a, b)


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
    # Windows and doors are found on the face as picked, before the clip turns one that
    # crosses the clad top or bottom into a bite out of the edge (cladding_primitives.openings).
    found = [list(r) for r in openings(dict(elev, clip_lo=0, clip_hi=None, clip_v_lo=0, clip_v_hi=None))]
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
    return dict(elev, polygons=polygons, openings=found)


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


SIDEWAYS_STRETCH = 1000.0   # how far a part under a band is stretched along u, so only its height counts


def clip_region(elev, splash):
    """Outline minus splash-zone bands: the area battens and cladding may occupy.

    Under each band, across its width, any part of the face thinner than the shortest
    row (ROW_MIN) is not clad either: between the zone and the roof or slab that sets it
    (its section taken out as a penetration) the face can leave a strip a few tens of mm
    high, or a wedge under a pitched line, and every board crossing it would be cut to
    a sliver. Found by opening (shrink, then grow back, square-cornered) the part of the
    region below the band: a strip at least a row high, the wall under a balcony and
    the cladding round a window all come back unchanged."""
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
        for band in bands:
            region = _drop_slivers_under(region, band)
    return region


def _drop_slivers_under(region, band):
    """*region* less its parts below *band*, within the band's width, too thin (top to
    bottom) to clad. Only height counts: a narrow face or a narrow pier between windows
    is not a sliver. So the opening is done with the part stretched SIDEWAYS_STRETCH times
    along u, where nothing is narrow, and squeezed back; heights are unchanged by it, so
    a strip under a pitched line is judged by its height too. Never fails a build: on
    anything unexpected the region comes back as it was."""
    try:
        if region is None or region.is_empty or band.is_empty:
            return region
        u0, _v0, u1, v1 = band.bounds
        rv0 = region.bounds[1]
        under = _intersection(region, box(u0, rv0 - 1.0, u1, v1))
        if under is None or under.is_empty:
            return region
        k, half = SIDEWAYS_STRETCH, ROW_MIN / 2.0 - 0.01
        wide = scale(under, xfact=k, yfact=1.0, origin=(0, 0))
        kept = scale(wide.buffer(-half, join_style=2).buffer(half, join_style=2), xfact=1.0 / k, yfact=1.0, origin=(0, 0))
        thin = _difference(under, kept)
        if thin is None or thin.is_empty or thin.area < 1.0:
            return region
        cut = _difference(region, thin)
        return region if cut is None else cut
    except Exception:   # noqa: BLE001 — a missed sliver is better than a failed build
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
        # stood back from the top, side and bottom edges by the chain's offsets
        region = offset_region(elev, clip_region(elev, p["splash"]))
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
        at = mesh.get("clip_v_at")
        if at:
            # A corner timber runs past its own outline on purpose: trim it in height only,
            # to where the face is clad just inside the corner.
            bands = strip_intervals(region, at[0], at[1], across=True)
            us = [q[0] for q in mesh["profile"]]
            keep = unary_union([box(min(us) - 1.0, v0, max(us) + 1.0, v1) for v0, v1 in bands]) if bands else None
            clipped = _intersection(poly, keep) if keep is not None else None
        else:
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
