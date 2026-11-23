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

from shapely.geometry import Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.affinity import scale
from shapely.ops import unary_union
from shapely.prepared import prep

from cladding_primitives import clip_bounds, clip_bounds_v, splash_rings, openings, ROW_MIN
from cladding_edges import offset_region

TRIMMABLE = frozenset({"batten", "counter_batten", "cross_batten", "plank", "panel", "gasket", "seam"})
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


RIDGE_REACH = 400.0      # mm – a slope's top edge this far short of its ridge line is carried up to it (fabric_extract.CORNER_TOL)


def to_ridge(elev):
    """A slope's outline brought to its ridge line: what of it was modelled running on past
    the line is cut off, and a top edge modelled short of it,
    by up to RIDGE_REACH, is carried up to it, so both slopes' cladding meets under the
    cap. It acts along the level top edges the ridge lies on, each over its whole length
    (one straight top edge comes down to the line all along, not only where the other
    slope is), and leaves the rest of the face (a stepped top, another edge) as it is."""
    ridge = elev.get("ridge") or {}
    try:
        v, u0, u1 = float(ridge["v"]), float(ridge["u0"]), float(ridge["u1"])
    except (KeyError, TypeError, ValueError):
        return elev
    if not ridge.get("with") or u1 - u0 < 1.0:
        return elev
    lo_v, hi_v = v - RIDGE_REACH, v + 1000.0
    # the level top edges the ridge acts on: near the line and overlapping its stretch
    spans = []
    for pg in elev.get("polygons") or []:
        ring = pg["exterior"]
        for q, r in zip(ring, ring[1:] + ring[:1]):
            if abs(q[1] - r[1]) <= 5.0 and abs(q[0] - r[0]) > 50.0 and lo_v <= (q[1] + r[1]) / 2.0 <= hi_v:
                a0, a1 = sorted((q[0], r[0]))
                if a1 > u0 + 1.0 and a0 < u1 - 1.0:
                    spans.append((a0, a1))
    if not spans:
        return elev
    w0, w1 = min(min(x for x, _y in spans), u0), max(max(y for _x, y in spans), u1)
    try:
        shape = unary_union([Polygon(pg["exterior"], pg.get("holes") or []).buffer(0) for pg in elev.get("polygons") or []])
    except Exception:   # noqa: BLE001
        return elev
    cut = _difference(shape, box(w0 - 1.0, v, w1 + 1.0, v + 1e6))
    if cut is None or cut.is_empty:
        return elev
    polygons = []
    for part in iter_polygons(cut):
        if part.area <= 1.0:
            continue
        ext, holes = polygon_to_rings(part)
        # a top corner short of the line, nothing of the face above it: raised to the line
        raised = [[x, v] if (w0 - 1.0 <= x <= w1 + 1.0 and lo_v < y < v - 0.5 and not part.contains(Point(x, y + 2.0))) else [x, y]
                  for x, y in ext]
        pg = Polygon(raised, holes).buffer(0)
        for piece in iter_polygons(pg):
            if piece.area > 1.0:
                e2, h2 = polygon_to_rings(piece)
                polygons.append({"exterior": e2, "holes": h2})
    if not polygons:
        return elev
    top = max(q[1] for pg in polygons for q in pg["exterior"])
    return dict(elev, polygons=polygons, height=max(top, 1.0))


def clip_elevation(elev):
    """The elevation with its polygons cut back to the clad part of the face: sideways
    where a wall runs past a corner, because the rest of it belongs to the other face,
    and vertically to the top and bottom levels picked for the chain. A slope is first
    brought to its ridge line (to_ridge)."""
    elev = to_ridge(elev)
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


def _tidy(pg):
    """The polygon without the vertices a cut leaves along its straight edges. Where a
    board's edge lies on the outline's (a panel beside a window, its edge on the jamb) the
    cut hands the board every vertex the outline has on that line, the ends of the
    offsets at the sill and the head among them. They change nothing in the shape, but a
    viewer triangulates to them and draws lines across the board, and anything that reads
    an edge end to end (the mitre to a lining) sees several edges where there is one."""
    try:
        out = pg.simplify(0.01, preserve_topology=True)
        return out if out.geom_type == "Polygon" and not out.is_empty and abs(out.area - pg.area) < 1.0 else pg
    except Exception:   # noqa: BLE001 — GEOS trouble leaves it as it was
        return pg


TIMBERS = frozenset({"batten", "counter_batten", "cross_batten"})
SLIVER = 0.4        # a timber kept over less than this of its width, along some length of it, loses that length
SHAVE = 25.0        # mm – a timber shaved along its edge by no more than this (an edge offset meant for the
                    # boards, grazing it) is left whole there


def _no_slivers(clipped, profile, outline=None):
    """A batten (an upright or level rectangle) trimmed to the clad region is not left
    shaved along its edge: where the trim takes no more than SHAVE off its width along some
    length of it (the boards' edge offset at a notch in the outline grazing it), it is left
    whole there, its edge straight, and the board ends there keep their support; where the
    trim leaves less than SLIVER of its width, a strip no fixing could go into, that length
    is cut out. A slanted end (a gable's) is left as it is. *outline*: the face's own
    outline, openings and all, before any offset, grown 0.5 mm: a batten is only made whole
    within it, never into an opening or past the face's edge."""
    us, vs = [q[0] for q in profile], [q[1] for q in profile]
    if len(profile) != 4 or len({round(u, 3) for u in us}) != 2 or len({round(v, 3) for v in vs}) != 2:
        return clipped                                              # not a plain rectangle
    u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
    level = u1 - u0 >= v1 - v0
    width = (v1 - v0) if level else (u1 - u0)
    if width <= 1.0:
        return clipped
    stops = []
    for pg in iter_polygons(clipped):
        stops += [x if level else y for x, y in pg.exterior.coords]
    stops.sort()
    stops = [q for i, q in enumerate(stops) if i == 0 or q - stops[i - 1] >= 0.01]
    keep, dropped = [], False
    for a, b in zip(stops, stops[1:]):
        cell = _intersection(clipped, box(a, v0 - 1.0, b, v1 + 1.0) if level else box(u0 - 1.0, a, u1 + 1.0, b))
        pieces = [pg for pg in iter_polygons(cell) if not pg.is_empty] if cell is not None else []   # its area only: no stray edges
        if not pieces:
            continue
        area = sum(pg.area for pg in pieces)
        kept = area / (b - a)
        if kept < SLIVER * width:
            dropped = True
            continue
        cx0, cy0, cx1, cy1 = shapely.total_bounds(pieces)
        square = abs(area - (cx1 - cx0) * (cy1 - cy0)) < 0.5             # a plain strip, not a slanted end
        whole = box(a, v0, b, v1) if level else box(u0, a, u1, b)
        if square and width - SHAVE <= kept < width - 0.5 and (outline is None or outline.contains(whole)):
            dropped = True                                                # shaved: whole again over this length
            pieces = [whole]
        keep += pieces
    if not dropped:
        return clipped
    # the strips share their edges, a hair apart where a cut was snapped: joined on the grid,
    # where GEOS cannot fail (a TopologyException kills the engine in the browser)
    return shapely.union_all(keep, grid_size=ROBUST_GRID) if keep else None


def apply_boolean_ops(meshes, p):
    """Return a new mesh list with every trimmable prism clipped to its elevation.

    *p* is the parsed parameter dict (needs "elevations" and "splash").
    Elements that fall entirely inside an opening or splash zone are dropped;
    elements split by an opening become several prisms with a letter suffix.
    """
    regions, prepared, outlines = {}, {}, {}
    for elev in p.get("elevations", []):
        try:
            outlines[elev.get("name", "")] = region_polygon(elev).buffer(0.5)   # the face itself: no offsets, openings cut out
        except Exception:
            outlines[elev.get("name", "")] = None
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
        clear = mesh.get("keep_clear")      # boxes it stays out of: cut short of a cavity barrier (cladding_geometry)
        try:
            if pre is not None and not clear and pre.contains(poly):
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
        if clear and clipped is not None:
            clipped = _difference(clipped, shapely.union_all([box(*c) for c in clear], grid_size=ROBUST_GRID))
        if mesh.get("ifc_type") in TIMBERS and clipped is not None:
            clipped = _no_slivers(clipped, mesh["profile"], outlines.get(mesh.get("elevation")))
        parts = [pg for pg in iter_polygons(clipped) if pg.area > _MIN_AREA]
        if not parts:
            continue
        parts.sort(key=lambda pg: (pg.bounds[1], pg.bounds[0]))
        for i, pg in enumerate(parts):
            m = copy.copy(mesh)
            m["profile"], m["holes"] = polygon_to_rings(_tidy(pg))
            if len(parts) > 1:
                m["name"] = "%s%s" % (mesh.get("name", ""), chr(97 + i) if i < 26 else str(i))
            out.append(m)
    return out
