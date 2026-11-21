"""
CladForge — fabric extraction.

Turns picked face triangles into a named elevation: a coplanar region in its own
(u, v) frame, with openings as interior holes (or notches where they
break the outline), penetrations subtracted, slab/roof abutments detected as
lines (level or pitched) for the splash zone, and the region cut back to the
patches the clicks landed on. Also decides whether two elevations meet at a
corner so the UI can chain them. Runs once per selection (Pyodide or Flask).

A face within a degree of vertical is a wall and is stood up: u runs along it and v is
world Z. Any other face keeps its own plane (a leaning wall, a roof slope, a flat roof,
a soffit): u is still level and v runs up the slope, so everything measured "up the
wall" is measured up the slope instead (face_axis, _to_local).

Payload (all coordinates IFC mm, Z-up):
  {"name": "Elevation A",
   "faces":   [[[x,y,z],[x,y,z],[x,y,z]], ...],        # picked triangles
   "outward": [nx,ny,nz],                              # hit normal facing the viewer
   "seeds":   [[x,y,z], ...],                          # where each pick was clicked
   "context": [{"type": "IfcSlab", "name": "...", "tris": [...]}, ...],
   "options": {"penetrations": true, "clad_depth": 150.0}}   # depth of the cladding zone

Result: {"ok", "name", "warnings", "frame", "polygons", "notches", "width",
         "height", "area", "n_holes", "n_faces", "pitch", "faces_down",
         "abutments": [{"u0","u1","v","line":[[u,v],...],"source",...}]}
"""

import math
import time
from functools import lru_cache

from shapely.geometry import Polygon, MultiPoint, LineString, Point
from shapely import union_all
from shapely.ops import unary_union, polygonize
from shapely.affinity import translate

from cladding_booleans import iter_polygons, polygon_to_rings, _difference, _intersection, near_misses, ROBUST_GRID

PLANE_TOL = 25.0          # mm – off-plane distance still treated as on the face
VERTICAL_TOL = math.sin(math.radians(1.0))   # a face this close to vertical is a wall, and is stood up
LEVEL_TOL = 0.005         # sin(0.3 deg) – flatter than this a face has no slope to set u out from
MIN_HOLE_AREA = 2500.0    # mm² – ignore holes smaller than 50 x 50
EDGE_MARGIN = 50.0        # mm – abutments this close to the top/bottom are not abutments
CORNER_TOL = 400.0        # mm – a corner may sit this far past an elevation's end (wall thickness)
PITCH_TOL = 20.0          # mm – rise along an abutment line before it counts as pitched
DEFAULT_BUDGET_S = 20.0   # s – extraction gives up on context past this rather than hang
# Type names are compared upper-cased: web-ifc reports IFCSLAB, IfcOpenShell IfcSlab.
ABUTMENT_TYPES = frozenset({"IFCSLAB", "IFCSLABSTANDARDCASE", "IFCSLABELEMENTEDCASE", "IFCROOF"})
# A designer's battens: columns or members standing on the face (_post_of). Anything else
# shaped and placed like one (a generic object, a part, an upright beam) is taken too when
# its name says batten, and otherwise reported, so it is plain why it was passed over.
POST_TYPES = frozenset({"IFCCOLUMN", "IFCCOLUMNSTANDARDCASE", "IFCMEMBER", "IFCMEMBERSTANDARDCASE"})
POST_LIKE_TYPES = frozenset({"IFCBUILDINGELEMENTPROXY", "IFCBUILDINGELEMENTPART", "IFCBEAM", "IFCBEAMSTANDARDCASE",
                             "IFCPLATE", "IFCELEMENTASSEMBLY"})
POST_NAMES = ("batten", "lath", "furring", "strapping")
POST_WIDTH = (15.0, 150.0)  # mm – how wide a modelled batten can be, across the wall
POST_MIN_HEIGHT = 600.0     # mm – and how tall at least: anything shorter is not a batten
POST_DEPTH = 150.0          # mm – and how deep at most, out from the wall: more is a column
POST_REACH = 150.0          # mm – its back within this of the face (it stands on the sarking)
POST_INSIDE = 0.5           # at least this much of its section in front of the face, where its back is sunk in
# Windows and doors: where a reveal lining runs back to (cladding_edges.opening_frames).
FRAME_TYPES = frozenset({"IFCWINDOW", "IFCWINDOWSTANDARDCASE", "IFCDOOR", "IFCDOORSTANDARDCASE"})
IGNORE_TYPES = frozenset({"IFCWALL", "IFCWALLSTANDARDCASE", "IFCWALLELEMENTEDCASE", "IFCSPACE",
                          "IFCSITE", "IFCOPENINGELEMENT", "IFCOPENINGSTANDARDCASE",
                          "IFCFURNISHINGELEMENT", "IFCCOVERING", "IFCRAILING", "IFCSTAIR",
                          "IFCSTAIRFLIGHT", "IFCVIRTUALELEMENT", "IFCGRID", "IFCANNOTATION",
                          "IFCCURTAINWALL", "IFCPLATE", "IFCMEMBER", "IFCBUILDINGELEMENTPROXY"})
# Of those, the ones that still cut a healed strip they pass right through (_extract).
HEAL_CUT_TYPES = frozenset({"IFCMEMBER", "IFCPLATE", "IFCBUILDINGELEMENTPROXY", "IFCCOVERING"})


# ── vector helpers ────────────────────────────────────────────────────────

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    length = math.sqrt(_dot(a, a))
    return (a[0] / length, a[1] / length, a[2] / length) if length > 0 else (0.0, 0.0, 0.0)


def _dedupe(tris, quant=10.0):
    """Drop duplicate/inverted-twin triangles (double-sided IFC meshes)."""
    seen, out = set(), []
    for tri in tris:
        key = tuple(sorted(tuple(round(c * quant) for c in v) for v in tri))
        if key in seen:
            continue
        seen.add(key)
        out.append(tri)
    return out


# ── plane and frame ───────────────────────────────────────────────────────

ROOF_TOL = math.cos(math.radians(5.0))   # a roof face's normal is within 5 deg of world Z


def fit_plane(faces, outward=None, mode="wall"):
    """Area-weighted plane through the picked triangles. Returns (n, d, warnings) with
    n·p = d on the plane.

    "wall" points n outward. Within VERTICAL_TOL of vertical the face is a wall and n is
    flattened to horizontal; any other face (leaning, pitched, level) keeps its own n.
    "roof" needs n within 5 deg of vertical and facing up, and snaps it to world Z, so
    the roof datum is a level plane at the picked face's mean height."""
    if mode == "roof":
        return _fit_roof_plane(faces, outward)
    warnings = []
    acc = [0.0, 0.0, 0.0]
    ref = None
    for a, b, c in faces:
        n = _cross(_sub(b, a), _sub(c, a))      # length = 2 x area
        if ref is None:
            ref = outward or n
        if _dot(n, ref) < 0:
            n = (-n[0], -n[1], -n[2])            # inverted twin — flip to agree
        acc[0] += n[0]; acc[1] += n[1]; acc[2] += n[2]
    n = _norm(acc)
    if outward and _dot(n, outward) < 0:
        n = (-n[0], -n[1], -n[2])
    if n == (0.0, 0.0, 0.0):
        return None, 0.0, ["Picked faces have no area"]
    if abs(n[2]) <= VERTICAL_TOL:
        n = _norm((n[0], n[1], 0.0))
    tot, dsum = 0.0, 0.0
    for a, b, c in faces:
        area = math.sqrt(_dot(_cross(_sub(b, a), _sub(c, a)), _cross(_sub(b, a), _sub(c, a)))) / 2
        cen = ((a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3, (a[2] + b[2] + c[2]) / 3)
        dsum += _dot(n, cen) * area
        tot += area
    return n, (dsum / tot if tot else 0.0), warnings


def _fit_roof_plane(faces, outward=None):
    acc = [0.0, 0.0, 0.0]
    for a, b, c in faces:
        n = _cross(_sub(b, a), _sub(c, a))      # length = 2 x area
        if n[2] < 0:
            n = (-n[0], -n[1], -n[2])            # inverted twin — flip to face up
        acc[0] += n[0]; acc[1] += n[1]; acc[2] += n[2]
    n = _norm(acc)
    if outward and outward[2] < 0:
        return None, 0.0, ["That face points down — pick the top of the roof structure"]
    if n[2] < ROOF_TOL:
        return None, 0.0, ["Face is %.1f deg off level — pick the top of the roof structure (a level face)"
                           % math.degrees(math.acos(max(-1.0, min(1.0, n[2]))))]
    warnings = []
    if n[2] < math.cos(math.radians(0.5)):
        warnings.append("Face is %.1f deg off level; treated as level" % math.degrees(math.acos(n[2])))
    tot, zsum = 0.0, 0.0
    for a, b, c in faces:
        cr = _cross(_sub(b, a), _sub(c, a))
        area = math.sqrt(_dot(cr, cr)) / 2
        zsum += (a[2] + b[2] + c[2]) / 3 * area
        tot += area
    return (0.0, 0.0, 1.0), (zsum / tot if tot else 0.0), warnings


def face_axis(n, faces=None):
    """The face's u: level, along the face, to the viewer's right (z × n). A level face has
    no such direction, so its u is the long side of the picked faces' outline in plan."""
    if n[2] == 0.0:
        return (-n[1], n[0], 0.0)
    h = math.hypot(n[0], n[1])
    if h >= LEVEL_TOL:
        return (-n[1] / h, n[0] / h, 0.0)
    try:
        box_ = unary_union([Polygon([(q[0], q[1]) for q in tri]).buffer(0) for tri in faces or []]).minimum_rotated_rectangle
        c = list(box_.exterior.coords)
        a, b = (c[1][0] - c[0][0], c[1][1] - c[0][1]), (c[2][0] - c[1][0], c[2][1] - c[1][1])
        ax = a if math.hypot(*a) >= math.hypot(*b) else b
        ax = (ax[0] / math.hypot(*ax), ax[1] / math.hypot(*ax))
    except Exception:   # noqa: BLE001 — no outline to read: world X is as good as any
        ax = (1.0, 0.0)
    if abs(ax[1]) < 1e-9:
        ax = (math.copysign(1.0, ax[0]), 0.0)
    if ax[0] < -1e-9 or (abs(ax[0]) <= 1e-9 and ax[1] < 0):
        ax = (-ax[0], -ax[1])                    # one answer for a rectangle, whichever way it was drawn
    return (ax[0], ax[1], 0.0)


@lru_cache(maxsize=64)
def _up(n, u):
    """The face's v: up the wall (world Z) or up the slope, n × u."""
    return (0.0, 0.0, 1.0) if n[2] == 0.0 else _cross(n, u)


def face_pitch(n):
    """The face's angle from level, in degrees: 90 a wall, 0 a flat roof or a soffit."""
    return math.degrees(math.acos(max(-1.0, min(1.0, abs(n[2])))))


def make_frame(n, d, umin, vmin, mode="wall", u=None):
    """Frame dict {"origin", "u", "v", "n"} with origin at local (0, 0) and v = n × u.
    "wall": u level along the face (viewer's right), n outward, v world Z for a wall and
            up the slope for a face that leans or is pitched (*u* given for a level one).
    "roof": n is world Z, u the given horizontal axis (FallWright's roof_axis), v = n × u."""
    if mode == "roof":
        u = u or (1.0, 0.0, 0.0)
        v = (-u[1], u[0], 0.0)                   # z × u
        origin = (u[0] * umin + v[0] * vmin, u[1] * umin + v[1] * vmin, d)
        return {"origin": [round(c, 3) for c in origin], "u": [round(c, 9) for c in u],
                "v": [round(c, 9) for c in v], "n": [0.0, 0.0, 1.0]}
    if n[2] != 0.0:
        u = tuple(u) if u else face_axis(n)
        v = _up(tuple(n), u)
        origin = tuple(n[i] * d + u[i] * umin + v[i] * vmin for i in range(3))
        return {"origin": [round(c, 3) for c in origin], "u": [round(c, 9) for c in u],
                "v": [round(c, 9) for c in v], "n": [round(c, 9) for c in n]}
    u = (-n[1], n[0], 0.0)                       # z × n
    origin = (n[0] * d + u[0] * umin, n[1] * d + u[1] * umin, vmin)
    return {"origin": [round(c, 3) for c in origin], "u": [round(c, 6) for c in u],
            "v": [0.0, 0.0, 1.0], "n": [round(c, 6) for c in n]}


def _to_local(p, n, u, mode="wall"):
    """World point → (u, v) in the frame's plane: up a wall is world Z, up a slope and
    across a roof it is n × u."""
    if mode == "roof":
        return (_dot(p, u), -p[0] * u[1] + p[1] * u[0])
    if n[2] == 0.0:
        return (_dot(p, u), p[2])
    return (_dot(p, u), _dot(p, _up(tuple(n), tuple(u))))


# ── region ────────────────────────────────────────────────────────────────

def _union(geoms):
    """unary_union that cannot kill the engine. Model edges from different elements can
    nearly coincide (two ends 0.02 mm apart), and GEOS's floating-point union can then
    fail to node them: a TopologyException that in the browser is a C++ abort, taking the
    runtime down where try/except never sees it. So look first: where anything nearly
    meets (cladding_booleans.near_misses), union snapped to a 0.01 mm grid, which always nodes; otherwise
    exactly, as before, so clean geometry keeps its exact coordinates."""
    geoms = list(geoms)
    if len(geoms) > 1 and near_misses(geoms):
        return union_all(geoms, grid_size=ROBUST_GRID)
    return unary_union(geoms)


def _union_faces(faces, n, u):
    polys = []
    for tri in faces:
        pts = [_to_local(v, n, u) for v in tri]
        try:
            pg = Polygon(pts)
            if pg.area > 1.0:
                polys.append(pg if pg.is_valid else pg.buffer(0))
        except Exception:
            continue
    if not polys:
        return None
    try:
        region = unary_union(polys)
        # Weld hairline gaps between triangles; mitre joins keep corners sharp.
        region = region.buffer(0.5, join_style=2).buffer(-0.5, join_style=2)
        return region.simplify(0.05, preserve_topology=True)
    except Exception:
        return unary_union([pg.buffer(0) for pg in polys])


def _section(tris, n, d, u, mode="wall"):
    """Intersection of an element with the face plane, in local (u, v).

    Returns (straddles, touches, section polygon or None, points). The polygon is
    assembled from the plane-crossing segments plus any triangles lying in the
    plane; the convex hull of the points is the fallback for open meshes."""
    pts, segs, inplane, smin, smax = [], [], [], float("inf"), float("-inf")
    for tri in tris:
        s = [_dot(v, n) - d for v in tri]
        smin, smax = min(smin, *s), max(smax, *s)
        if all(abs(x) <= PLANE_TOL for x in s):
            inplane.append(Polygon([_to_local(v, n, u, mode) for v in tri]))
        cut = []
        for i in range(3):
            a, b = tri[i], tri[(i + 1) % 3]
            sa, sb = s[i], s[(i + 1) % 3]
            if abs(sa) <= PLANE_TOL:
                pts.append(_to_local(a, n, u, mode))
            if (sa < -PLANE_TOL and sb > PLANE_TOL) or (sa > PLANE_TOL and sb < -PLANE_TOL):
                t = sa / (sa - sb)
                q = _to_local((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t), n, u, mode)
                pts.append(q)
                cut.append(q)
        if len(cut) == 2 and cut[0] != cut[1]:
            segs.append(LineString(cut))
    if not pts:
        return False, False, None, []
    straddles = smin < -PLANE_TOL and smax > PLANE_TOL
    poly = None
    try:
        parts = [pg for pg in polygonize(_union(segs)) if pg.area > MIN_HOLE_AREA] if segs else []
        parts += [pg for pg in inplane if pg.is_valid and pg.area > 1.0]
        if parts:
            poly = _union(parts).buffer(0.5, join_style=2).buffer(-0.5, join_style=2)
        if poly is None or poly.is_empty or poly.area <= MIN_HOLE_AREA:
            hull = MultiPoint(pts).convex_hull
            poly = hull if hull.geom_type == "Polygon" and hull.area > MIN_HOLE_AREA else None
    except Exception:
        poly = None
    return straddles, True, poly, pts


ZONE_PLANES = (0.5, 1.0)   # fractions of the cladding depth, beyond the face, to section at


def _meets_foot(zone, top, vmin, umin, umax, depth):
    """Does a slab or roof meet the face along its foot, as a roof does under a dormer?

    The wall section alone cannot tell that from the ground slab a wall stands on: both
    top out at the foot. What differs is in front. The roof carries on out under the
    cladding and catches the water off it, so it needs the splash zone; a slab whose edge
    stops at the wall has nothing in the cladding zone and the wizard's answer about the
    foot stands. The roof falls away from the face, so the tolerance grows with the depth."""
    if zone is None or abs(top - vmin) > EDGE_MARGIN:
        return False
    line = _top_line(zone, umin, umax)
    return bool(line) and max(v for _u, v in line) >= vmin - EDGE_MARGIN - depth


def _zone_section(tris, n, d, u, depth):
    """What stands in the cladding zone in front of the face.

    A plane section at the face only sees what reaches the wall, but the cladding stands
    off it by the whole buildup, so a roof finish that stops short still sits where the
    boards go. Sections are cut at planes across that zone with the same plane cut used
    at the face — the operation that has been reliable on real models all along.

    This replaced a projection of every triangle in the zone onto the wall. That unioned
    one sliver per triangle, and it arrived in the same change as extractions that never
    came back on a real model; a plane cut gives a handful of well-formed polygons."""
    parts = []
    for f in ZONE_PLANES:
        try:
            _straddles, touches, poly, _pts = _section(tris, n, d + f * depth, u)
        except Exception:   # noqa: BLE001
            continue
        if touches and poly is not None and not poly.is_empty:
            parts.append(poly)
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    try:
        return _union(parts)
    except Exception:   # noqa: BLE001
        return max(parts, key=lambda pg: pg.bounds[3])


MAX_TOP_SAMPLES = 240   # probes across one abutment: each is a ray cast, so this is the cost
END_INSET = 0.05        # mm – the first and last probe are taken this far inside the section


def _top_line(poly, u0, u1):
    """Upper envelope of a section polygon between u0 and u1, as a simplified polyline.
    Level for a flat roof or slab, sloped where a pitched roof meets a gable.

    Every sample is a ray cast against the whole polygon, so sampling one per vertex is
    quadratic in the section's complexity — a faceted roof with a few thousand vertices
    takes tens of seconds and reads as a hang. The probes are capped: vertex positions
    are kept where there are few enough to matter, and thinned evenly beyond that."""
    bu0, bv0, bu1, bv1 = poly.bounds
    lo, hi = max(u0, bu0), min(u1, bu1)
    if hi - lo < EDGE_MARGIN:
        return None
    xs = sorted({round(x, 3) for pg in iter_polygons(poly) for x, _y in pg.exterior.coords if lo < x < hi})
    if len(xs) > MAX_TOP_SAMPLES:
        step = len(xs) / float(MAX_TOP_SAMPLES)
        xs = [xs[min(len(xs) - 1, int(i * step))] for i in range(MAX_TOP_SAMPLES)]
    us = sorted({lo, hi} | set(xs))
    line = []
    for x in us:
        # The end probes run along the section's own side. On a wall off the axes, or a
        # pitched one, that side is not at the exact figure, and a probe laid along it reads
        # scraps of the outline: so the ends are read a hair inside.
        at = lo + END_INSET if x == lo else hi - END_INSET if x == hi else x
        try:
            hit = LineString([(at, bv0 - 1), (at, bv1 + 1)]).intersection(poly)
        except Exception:
            continue
        if hit.is_empty:
            continue
        line.append((x, hit.bounds[3]))
    if len(line) < 2:
        return None
    return [list(p) for p in LineString(line).simplify(2.0).coords]


def extract_elevation(payload):
    """Main entry point. See module docstring for the payload format.
    Never raises: a failure comes back as ok=False with the reason in warnings."""
    try:
        return _extract(payload)
    except Exception as exc:   # noqa: BLE001 — surfaced to the UI, never silent
        return {"ok": False, "name": payload.get("name") or "Elevation", "n_faces": 0,
                "warnings": ["Extraction failed: %s: %s" % (type(exc).__name__, exc)]}


def _say(on, msg):
    """Print a stage as it starts. Pyodide blocks the page while it runs, so a slow or
    hung extraction paints nothing — the console trail is the only way to see where it
    stopped. The last line printed names the stage that did not finish."""
    if on:
        print("[extract] %s" % msg, flush=True)


def _extract(payload):
    name = payload.get("name") or "Elevation"
    # Strips healed onto the face (heal_link, the app's tick): each a quad in the world from
    # the face's own edge (its first two corners) to the line it was healed to, with the
    # elements it was healed to ("through", ids or names). A strip is part of the face, and
    # what the model cuts out of the face it cuts out of the strip, with these differences.
    #
    # Two faces brought together side by side (a corner, a ridge, a hip): the elements a
    # strip was healed to do not cut it. A wall carried on to a corner passes through the
    # other wall's own thickness, which would otherwise be cut out of it again.
    #
    # A face healed up *under* one that runs on over it ("under": a wall up to a roof that
    # oversails it) stops under what it was healed to: the cladding ends below the underside
    # of the plywood, it does not run up through it to the roof's top. How far below is the
    # chain's top offset (the ventilation gap), which stands every top edge back.
    #
    # Anything that cuts a strip cuts it right through to the line: no sliver of cladding is
    # left between a rafter and the roof it carries. That includes the members, plates and
    # generic objects that never cut a face as picked (HEAL_CUT_TYPES): a rafter is most
    # often a member, and its tail does pass through a wall healed up to the roof.
    #
    # And it cuts **level through the build-up**. A soffit or a rafter that slopes stands
    # lower at the outside of the cladding than on the wall's face, so the cut is taken
    # where the element is across the whole cladding zone (_zone_shadow), which is its
    # height at the cladding's outside surface: every layer behind is cut back to match,
    # the top under the plywood and each notch round a rafter's foot alike, and nothing
    # further out runs into it.
    heals = [h if isinstance(h, dict) else {"quad": h} for h in payload.get("heals") or []]
    heals = [h for h in heals if len(h.get("quad") or []) == 4]
    healed = [[tuple(float(c) for c in h["quad"][i]) for i in idx] for h in heals for idx in ((0, 1, 2), (0, 2, 3))]
    faces = _dedupe([[tuple(float(c) for c in v) for v in tri] for tri in payload.get("faces", [])] + healed)
    result = {"ok": False, "name": name, "warnings": [], "n_faces": len(faces)}
    if not faces:
        result["warnings"].append("No faces picked")
        return result
    n, d, warnings = fit_plane(faces, payload.get("outward"))
    result["warnings"] += warnings
    if n is None:
        return result
    u = face_axis(n, faces)
    result["pitch"], result["faces_down"] = round(face_pitch(n), 1), n[2] < 0
    region = _union_faces(faces, n, u)
    # What was trimmed off the face (heal_link's "trim", the app's tick): outlines in the
    # world, each the part of the face that ran on past the line it meets another on.
    for ring in payload.get("trims") or []:
        try:
            cut = Polygon([_to_local(tuple(float(c) for c in q), n, u) for q in ring])
            cut = cut if cut.is_valid else cut.buffer(0)
            if region is not None and not cut.is_empty:
                region = _difference(region, cut)
                if region is not None and not region.is_empty:          # in a hair and out again: no sliver is left on the line
                    region = region.buffer(-0.02, join_style=2, mitre_limit=5.0).buffer(0.02, join_style=2, mitre_limit=5.0)
        except Exception:   # noqa: BLE001 — a trim that will not build leaves the face as picked
            continue
    if region is None or region.is_empty:
        result["warnings"].append("Picked faces have no area")
        return result

    # Context: abutments from slabs/roofs, penetrations from everything else.
    options = payload.get("options") or {}
    dbg = bool(options.get("debug", True))
    # Extraction runs on the page's main thread, so anything slow freezes the browser with
    # no way out. Every stage is checked against a deadline: past it the context loop stops
    # and the elevation comes back with what it has and a warning naming where it ran out,
    # which is recoverable and diagnosable in a way that grinding for twenty minutes is not.
    budget = options.get("budget_s")    # 0 is a real value, so do not fall back on falsiness
    deadline = time.perf_counter() + (DEFAULT_BUDGET_S if budget is None else float(budget))
    clock = {}
    _say(dbg, "%s: %d faces, region %.0f x %.0f" % (name, len(faces), region.bounds[2] - region.bounds[0],
                                                    region.bounds[3] - region.bounds[1]))
    clad_depth = float(options.get("clad_depth") or 0.0)
    abutments, cuts, frames, posts = [], [], [], []
    passed = []          # batten-shaped elements passed over, and why (reported below)
    umin0, vmin0, umax0, vmax0 = region.bounds
    skipped = 0
    strips = _heal_strips(heals, n, u)
    strip_area = _union([s[0] for s in strips]) if strips else None
    for elem in payload.get("context", []):
        etype = (elem.get("type") or "").upper()
        # Members, plates and generic objects are otherwise left alone (they are not read as
        # abutments or penetrations), but one may be a batten: those are still looked at.
        ignored = etype in IGNORE_TYPES
        if (ignored and etype not in POST_TYPES and etype not in POST_LIKE_TYPES) or not elem.get("tris"):
            continue
        if time.perf_counter() > deadline:
            timed_out = len(payload.get("context", [])) - len(clock)
            result["warnings"].append(
                "Ran out of time after %d of %d nearby elements — %d skipped. The elevation is "
                "built from what was read; pick a smaller patch or switch off penetrations."
                % (len(clock), len(payload.get("context", [])), timed_out))
            _say(dbg, "  DEADLINE reached — skipping %d remaining element(s)" % timed_out)
            break
        _say(dbg, "  %s (%s, %d tris)" % (elem.get("name") or "?", etype, len(elem["tris"])))
        _t0 = time.perf_counter()
        try:   # one awkward element must not sink the whole extraction
            tris = [[tuple(float(c) for c in v) for v in tri] for tri in elem["tris"]]
            if etype in POST_TYPES or etype in POST_LIKE_TYPES:
                post, why = _post_of(tris, n, d, u)
                named = any(k in (elem.get("name") or "").lower() for k in POST_NAMES)
                if post and (etype in POST_TYPES or named):
                    posts.append(dict(post, name=elem.get("name", "")))
                    clock[elem.get("name") or etype] = round((time.perf_counter() - _t0) * 1000)
                    continue
                if post:
                    # Shaped and placed like a batten, but neither a column or member nor
                    # called one: offered switched off (the 2D view turns it on), and it
                    # goes on to be read as whatever else it is.
                    posts.append(dict(post, name=elem.get("name", ""), default="off"))
                    passed.append(("not a column or member (%s): shown switched off in the 2D view, click one to use it"
                                   % (elem.get("type") or etype), elem.get("name", "")))
                elif why and (etype in POST_TYPES or named):
                    passed.append((why, elem.get("name", "")))
            if ignored:
                # Left alone on the face as picked; but one that passes right through a healed
                # strip (a rafter's tail through a wall healed up to the roof) cuts that strip.
                if strips and etype in HEAL_CUT_TYPES:
                    shadow = _zone_shadow(tris, n, d, u, clad_depth, strip_area)
                    if shadow is not None:
                        cuts.append((None, elem, shadow))
                clock[elem.get("name") or etype] = round((time.perf_counter() - _t0) * 1000)
                continue
            if etype in FRAME_TYPES:
                fr = _frame_of(tris, n, d, u)
                if fr:
                    frames.append(dict(fr, name=elem.get("name", ""), type=elem.get("type") or etype))
            straddles, touches, poly, pts = _section(tris, n, d, u)
            shadow = _zone_shadow(tris, n, d, u, clad_depth, strip_area) if strips else None
            # An abutment counts if it reaches the wall or merely stands in the cladding
            # zone in front of it; a penetration still has to cross the face to be a hole.
            zone = _zone_section(tris, n, d, u, clad_depth) if etype in ABUTMENT_TYPES and clad_depth > 0 else None
            if (not touches or poly is None) and zone is None:
                if shadow is not None:
                    cuts.append((None, elem, shadow))
                continue
            if etype in ABUTMENT_TYPES:
                shape = poly if zone is None else (zone if poly is None else _union([poly, zone]))
                line = _top_line(shape, umin0, umax0)
                if line:
                    top = max(v for _u, v in line)
                    if vmin0 + EDGE_MARGIN < top < vmax0 - EDGE_MARGIN or \
                            _meets_foot(zone, top, vmin0, umin0, umax0, clad_depth):
                        abutments.append({"line": line, "source": elem.get("type") or etype,
                                          "name": elem.get("name", "")})
                if straddles or shadow is not None:
                    cuts.append((poly if straddles else None, elem, shadow))
            elif (straddles and options.get("penetrations", True)) or shadow is not None:
                cuts.append((poly if straddles and options.get("penetrations", True) else None, elem, shadow))
        except Exception:   # noqa: BLE001
            skipped += 1
        clock[elem.get("name") or etype] = round((time.perf_counter() - _t0) * 1000)
    _say(dbg, "  context done, cutting %d penetration(s)" % len(cuts))
    if skipped:
        result["warnings"].append("%d context element(s) could not be sectioned and were ignored" % skipped)
    result["posts_passed"] = _passed_report(passed)
    for line in result["posts_passed"]:
        result["warnings"].append(line)
    for cut_poly, elem, shadow in cuts:
        try:
            cut_poly = _cut_round_heals(cut_poly, elem, strips, shadow)
            if cut_poly is None or cut_poly.is_empty:
                continue
            if cut_poly.intersection(region).area > MIN_HOLE_AREA:
                cut = _difference(region, cut_poly)
                if cut is not None and not cut.is_empty:
                    region = cut
        except Exception:
            continue

    if strips:
        try:
            tidy = region.simplify(0.01, preserve_topology=True)      # the seams between strip and face leave no vertices
            region = tidy if tidy.is_valid and not tidy.is_empty else region
        except Exception:   # noqa: BLE001
            pass
    region, dropped = _seeded(region, payload.get("seeds"), u, n=n)
    if dropped:
        result["warnings"].append("%d patch(es) beyond the junction you clicked were left out" % dropped)

    # Local origin at the region's bottom-left; everything shifts accordingly.
    umin, vmin, umax, vmax = region.bounds
    region = translate(region, -umin, -vmin)
    width, height = umax - umin, vmax - vmin
    polygons = []
    for pg in iter_polygons(region):
        if pg.area < 1.0:
            continue
        ext, holes = polygon_to_rings(pg)
        holes = [h for h in holes if abs(Polygon(h).area) >= MIN_HOLE_AREA]
        polygons.append({"exterior": ext, "holes": holes})
    if len(polygons) > 1:
        result["warnings"].append("%d separate patches merged into one elevation" % len(polygons))

    _say(dbg, "  seeding and notches")
    notches = _notches(polygons)
    # the lines it was healed to: it stands at them though its cladding may stop short (heal_link)
    result["healed"] = [[[round(float(c), 2) for c in h["quad"][3]], [round(float(c), 2) for c in h["quad"][2]]] for h in heals]
    result.update({"ok": True, "frame": make_frame(n, d, umin, vmin, u=u), "polygons": polygons,
                   "width": round(width, 2), "height": round(height, 2),
                   "area": round(region.area, 1), "notches": notches,
                   "abutments": _merge_abutments(abutments, umin, vmin, width),
                   "frames": [dict(f, rect=[round(f["rect"][0] - umin, 1), round(f["rect"][1] - umin, 1),
                                            round(f["rect"][2] - vmin, 1), round(f["rect"][3] - vmin, 1)])
                              for f in frames],
                   "wall_depth": float(options.get("wall_depth") or 0.0),
                   "posts": _merge_posts(posts, umin, vmin, width),
                   "n_holes": sum(len(pg["holes"]) for pg in polygons) + len(notches)})
    result["timings_ms"] = {k: v for k, v in sorted(clock.items(), key=lambda kv: -kv[1])[:8] if v >= 20}
    _say(dbg, "%s: done — %s" % (name, result["timings_ms"] or "nothing slow"))
    if any(a.get("pitched") for a in result["abutments"]):
        result["warnings"].append("Pitched abutment detected — the splash zone follows the roof line; check it")
    return result


def _heal_strips(heals, n, u):
    """[(the strip in the face's plane, the step from the face's edge to the line, what it
    was healed to, whether it was healed up under that)] for the heals of a payload."""
    out = []
    for h in heals:
        try:
            pts = [_to_local(tuple(float(c) for c in p), n, u) for p in h["quad"]]
            shape = Polygon(pts)
            shape = shape if shape.is_valid else shape.buffer(0)
            if shape.is_empty or shape.area < 1.0:
                continue
            step = ((pts[3][0] - pts[0][0] + pts[2][0] - pts[1][0]) / 2.0, (pts[3][1] - pts[0][1] + pts[2][1] - pts[1][1]) / 2.0)
            out.append((shape, step, {str(x) for x in h.get("through") or []}, bool(h.get("under"))))
        except Exception:   # noqa: BLE001 — a strip that will not build is left to be cut like the face
            continue
    return out


SHADOW_PLANES = (0.0, 0.5, 1.0)   # fractions of the cladding depth, from the face out, an element is sectioned at
CONVEX = 1.05                     # a section no bigger than this against its own hull is a plain bar or sheet


def _zone_shadow(tris, n, d, u, depth, near):
    """Where an element stands across the whole cladding zone, seen square on to the face:
    its sections on the face and out through the build-up to the cladding's outside
    surface, joined up. Only the pieces that reach *near* (the healed strips); None if
    none does.

    A rafter or a sheet of plywood that slopes is lower at the outside of the cladding
    than on the wall, by the build-up's depth times its slope: cut at the face alone, the
    boards further out would run into it. Between two sections of a plain bar or sheet
    (each one convex) the element fills their hull, so the hull is taken; anything of a
    more awkward shape is left as its sections, which is what it is known to be."""
    if near is None or depth <= 0:
        return None
    parts = []
    for f in SHADOW_PLANES:
        try:
            _straddles, touches, poly, _pts = _section(tris, n, d + f * depth, u)
        except Exception:   # noqa: BLE001
            continue
        if touches and poly is not None and not poly.is_empty:
            parts.append(poly)
    if not parts:
        return None
    try:
        plain = all(pg.geom_type == "Polygon" and pg.convex_hull.area <= CONVEX * pg.area for pg in parts)
        whole = _union(parts).convex_hull if plain else _union(parts)
        pieces = [pg for pg in iter_polygons(whole) if pg.area > 1.0 and pg.intersects(near)]
        return _union(pieces) if pieces else None
    except Exception:   # noqa: BLE001
        return None


def _cut_round_heals(cut, elem, strips, shadow=None):
    """What the element *elem* takes out of a face with healed strips (see _extract). *cut*:
    its section on the face, or None where it does not cut the face as picked; *shadow*:
    where it stands across the cladding zone by the strips (_zone_shadow). Nothing of a
    strip healed side by side to that element; of any other strip it reaches, itself, as
    far out as the cladding goes, and everything from there on to the line."""
    keys = {str(elem.get("id")), str(elem.get("name"))} - {"None", ""}
    for shape, step, through, under in strips:
        try:
            if keys & through and not under:
                if cut is not None and cut.intersects(shape):
                    cut = _difference(cut, shape)
                    if cut is None or cut.is_empty:
                        cut = None
                continue
            whole = [g for g in (cut, shadow) if g is not None and not g.is_empty and g.intersects(shape)]
            if not whole:
                continue
            whole = _union(whole + ([cut] if cut is not None and not cut.is_empty else []))
            part = whole.intersection(shape)
            if part.is_empty or part.area < 1.0:
                continue
            on = _union([part, translate(part, step[0], step[1])]).convex_hull.intersection(shape)
            cut = whole if on.is_empty else _union([whole, on])
        except Exception:   # noqa: BLE001 — left as the plain cut
            continue
    return cut


def _post_of(tris, n, d, u):
    """A batten standing on the face: (post, None), or (None, why not). A batten is narrow
    across the wall (POST_WIDTH), at least POST_MIN_HEIGHT tall and several times taller
    than wide, no deeper than POST_DEPTH, and in the cavity: its back on the face (within
    PLANE_TOL behind it) or up to POST_REACH in front, or, sunk further into the wall than
    that, with at least POST_INSIDE of its section still in front of the face. *why* is
    given only for something batten-shaped that stands in the wrong place; a structural
    column, a beam laid across the wall or a short block is simply not one (None, None).
    The post is its centreline and extent before the local shift."""
    pts = [_to_local(p, n, u) + (_dot(p, n) - d,) for tri in tris for p in tri]
    if not pts:
        return None, None
    us, vs, ss = [q[0] for q in pts], [q[1] for q in pts], [q[2] for q in pts]
    w, h = max(us) - min(us), max(vs) - min(vs)
    back, front = min(ss), max(ss)
    if not (POST_WIDTH[0] <= w <= POST_WIDTH[1]) or h < max(POST_MIN_HEIGHT, 5.0 * w) or front - back > POST_DEPTH:
        return None, None
    if back > POST_REACH:
        return None, "stands %.0f mm in front of the face (over %.0f)" % (back, POST_REACH)
    if back < -PLANE_TOL:
        if front <= PLANE_TOL:
            return None, None                         # in the wall, a stud or a post: not a batten, not news
        inside = front / (front - back)
        if inside < POST_INSIDE:
            return None, "is sunk %.0f mm into the face, only %.0f%% of it in front" % (-back, 100.0 * inside)
    return {"u": (min(us) + max(us)) / 2.0, "w": w, "v0": min(vs), "v1": max(vs)}, None


def _passed_report(passed):
    """One line per reason for the batten-shaped elements passed over: how many, a name,
    and why, so a face that found none of the model's battens says what it saw."""
    groups = {}
    for why, name in passed:
        groups.setdefault(why, []).append(name)
    out = []
    for why, names in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        eg = next((x for x in names if x), "")
        out.append("%d batten-shaped element%s not used%s: %s" % (
            len(names), "" if len(names) == 1 else "s", (' (e.g. "%s")' % eg) if eg else "", why))
    return out


def _merge_posts(posts, umin, vmin, width):
    """The columns on the face, in local u: one per centreline (a batten modelled a storey
    at a time is one batten), sorted, those beyond the face left out."""
    out = []
    for q in sorted(posts, key=lambda q: q["u"]):
        if out and abs(q["u"] - out[-1]["u"]) <= 5.0:
            out[-1].update(v0=min(out[-1]["v0"], q["v0"]), v1=max(out[-1]["v1"], q["v1"]), w=max(out[-1]["w"], q["w"]))
            if q.get("default") != "off":
                out[-1].pop("default", None)          # a column or member on the line: it is used
        else:
            out.append(dict(q))
    return [dict({"u": round(q["u"] - umin, 1), "w": round(q["w"], 1), "v0": round(q["v0"] - vmin, 1),
                  "v1": round(q["v1"] - vmin, 1), "name": q.get("name", "")},
                 **({"default": "off"} if q.get("default") == "off" else {}))
            for q in out if -q["w"] <= q["u"] - umin <= width + q["w"]]


def _frame_of(tris, n, d, u):
    """A window or door as the reveal sees it: its footprint on the face, (u0, u1, v0, v1)
    before the local shift, and the depth of its outer face from the wall face (out is
    positive). The bottom tenth is left out of the depth, so a projecting sill or
    threshold is not taken for the frame."""
    pts = [_to_local(p, n, u) + (_dot(p, n) - d,) for tri in tris for p in tri]
    if not pts:
        return None
    us, vs = [q[0] for q in pts], [q[1] for q in pts]
    v0, v1 = min(vs), max(vs)
    above = [q[2] for q in pts if q[1] >= v0 + 0.1 * (v1 - v0)] or [q[2] for q in pts]
    return {"rect": [min(us), max(us), v0, v1], "face": round(max(above), 1)}


def _seeded(region, seeds, u, tol=100.0, n=None):
    """(region, dropped), keeping only the patches the clicks landed on. A slab cut
    through a face leaves it in pieces and the piece beyond that junction is another
    wall, so each pick's own point picks its patch: a magic wand, not everything
    coplanar. A point just off a patch takes the nearest; with none, nothing drops."""
    flat = n is None or n[2] == 0.0             # a wall: up it is world Z
    pts = [Point(_dot(s, u), float(s[2])) if flat else Point(*_to_local([float(c) for c in s[:3]], n, u))
           for s in (seeds or []) if s and len(s) >= 3]
    parts = list(iter_polygons(region))
    if not pts or len(parts) < 2:
        return region, 0
    keep = []
    for q in pts:
        hit = next((pg for pg in parts if pg.intersects(q)), None)
        if hit is None:
            hit = min(parts, key=lambda pg: pg.distance(q))
            if hit.distance(q) > tol:
                continue
        if not any(hit.equals(k) for k in keep):
            keep.append(hit)
    return (unary_union(keep), len(parts) - len(keep)) if keep else (region, 0)


def _notches(polygons, limit=300.0):
    """Openings that break the outline, as (u0, u1, v0, v1). A door runs to the foot of
    the wall, so the void is a bite out of the exterior ring rather than an interior hole,
    and everything keyed off holes (closers, reveal linings, panel set-out) misses it.
    Each bite is a patch subtracted from its own bounding box; the ones that count are
    rectangular and open on one side, which tells a door from a gable (triangular) or a
    stepped wall (open on two)."""
    out = []
    for pg in polygons:
        try:
            shell = Polygon(pg["exterior"])
            if not shell.is_valid:
                shell = shell.buffer(0)
            rest = _difference(shell.envelope, shell)
            if rest is None or rest.is_empty:
                continue
            umin, vmin, umax, vmax = shell.bounds
            for piece in iter_polygons(rest):
                u0, v0, u1, v1 = piece.bounds
                if piece.area < max(MIN_HOLE_AREA, 0.9 * (u1 - u0) * (v1 - v0)):
                    continue                      # too small, or not a rectangular bite
                open_sides = [abs(u0 - umin) < 1.0, abs(u1 - umax) < 1.0,
                              abs(v0 - vmin) < 1.0, abs(v1 - vmax) < 1.0]
                if u1 - u0 < limit or v1 - v0 < limit or sum(open_sides) != 1:
                    continue
                out.append([round(u0, 2), round(u1, 2), round(v0, 2), round(v1, 2)])
        except Exception:   # noqa: BLE001 — a malformed ring just yields no notches
            continue
    return sorted(out)


def _merge_abutments(found, umin, vmin, width):
    """Shift to local coords, merge near-identical level lines, prepend the base (ground) line."""
    out = [{"u0": 0.0, "u1": round(width, 2), "v": 0.0, "line": [[0.0, 0.0], [round(width, 2), 0.0]],
            "source": "base", "enabled": True, "name": "Elevation base", "pitched": False}]
    for ab in found:
        line = [[round(x - umin, 2), round(y - vmin, 2)] for x, y in ab["line"]]
        vs = [p[1] for p in line]
        rec = {"u0": line[0][0], "u1": line[-1][0], "v": round(max(vs), 2), "v_min": round(min(vs), 2),
               "line": line, "source": ab["source"], "enabled": True, "name": ab.get("name") or ab["source"],
               "pitched": (max(vs) - min(vs)) > PITCH_TOL}
        merged = False
        for ex in out[1:]:
            if not ex["pitched"] and not rec["pitched"] and abs(ex["v"] - rec["v"]) <= 10.0 \
                    and rec["u0"] <= ex["u1"] + 10 and rec["u1"] >= ex["u0"] - 10:
                ex["u0"], ex["u1"] = min(ex["u0"], rec["u0"]), max(ex["u1"], rec["u1"])
                ex["line"] = [[ex["u0"], ex["v"]], [ex["u1"], ex["v"]]]
                merged = True
                break
        if not merged:
            out.append(rec)
    return sorted(out, key=lambda a: (a["v"], a["u0"]))


# ── corners ──────────────────────────────────────────────────────────────

RIDGE_MIN = 300.0         # mm – two slopes share at least this much ridge, or they are not a pair


def _top_run(elev):
    """(u0, u1) of the face's top edge: the stretch of its outline within 5 mm of its top.
    A hipped slope is a trapezium, so its ridge is shorter than the face is wide."""
    top = float(elev["height"])
    us = [q[0] for pg in elev.get("polygons") or [] for q in pg["exterior"] if q[1] >= top - 5.0]
    return (min(us), max(us)) if len(us) >= 2 else (0.0, float(elev["width"]))


def ridge_link(a, b):
    """Do pitched elevations a and b meet at a ridge? None if not, else {"kind": "ridge",
    "v_a", "v_b": the ridge line in each face's own v, "u_a": (u0, u1) and "u_b": the
    stretch of it they share, in each face's u, "u_b0": b's u where a's is 0 (b's u runs
    against a's, so u_b = u_b0 - u_a), "k": tan of half the angle between the normals,
    the slope dv/ddepth of the plane that bisects the ridge, "angle"}.

    A ridge is level, lies along the top of both faces, and the roof falls away from it on
    both sides. Planes that meet on a sloping line (a hip, a valley), a change of pitch
    (the top of one slope meeting the foot of the next) and a roof meeting a wall are not
    ridges and are not joined."""
    fa, fb = a["frame"], b["frame"]
    na, nb = fa["n"], fb["n"]
    va, vb = fa.get("v") or (0.0, 0.0, 1.0), fb.get("v") or (0.0, 0.0, 1.0)
    if abs(na[2]) < 1e-6 or abs(nb[2]) < 1e-6 or na[2] < 0 or nb[2] < 0:
        return None                                   # a wall, or a face that looks down
    if _dot(fa["u"], fb["u"]) > -0.9998:
        return None                                   # not back to back along one level line
    rise_a, rise_b = _dot(nb, va), _dot(na, vb)
    if rise_a < 1e-3 or rise_b < 1e-3:
        return None                                   # parallel, or the two fall towards each other (a valley)
    v_a = _dot(nb, _sub(fb["origin"], fa["origin"])) / rise_a
    v_b = _dot(na, _sub(fa["origin"], fb["origin"])) / rise_b
    if abs(v_a - float(a["height"])) > CORNER_TOL or abs(v_b - float(b["height"])) > CORNER_TOL:
        return None                                   # the line is not along the top of both
    at = [fa["origin"][i] + va[i] * v_a for i in range(3)]          # on the ridge, at a's u = 0
    u_b0 = _dot(fb["u"], _sub(at, fb["origin"]))
    (a0, a1), (b0, b1) = _top_run(a), _top_run(b)
    lo, hi = max(a0, u_b0 - b1), min(a1, u_b0 - b0)
    if hi - lo < RIDGE_MIN:
        return None
    beta = math.acos(max(-1.0, min(1.0, _dot(na, nb))))
    return {"kind": "ridge", "v_a": round(v_a, 1), "v_b": round(v_b, 1), "u_b0": round(u_b0, 1),
            "u_a": [round(lo, 1), round(hi, 1)], "u_b": [round(u_b0 - hi, 1), round(u_b0 - lo, 1)],
            "k": round(math.tan(beta / 2.0), 4), "angle": round(math.degrees(beta), 1), "external": True}


VALLEY_ON = 25.0          # mm – an outline edge this close to the other slope's plane lies along the valley
VALLEY_MIN = 300.0        # mm – two slopes share at least this much valley, or it is not one


def _centre(elev):
    """A point well inside the face, in its own (u, v): the centroid of its largest patch."""
    best = max((Polygon(pg["exterior"]) for pg in elev.get("polygons") or []), key=lambda g: g.area, default=None)
    return (best.centroid.x, best.centroid.y) if best is not None else (float(elev["width"]) / 2, float(elev["height"]) / 2)


def _at(frame, u, v):
    U, V, o = frame["u"], frame.get("v") or (0.0, 0.0, 1.0), frame["origin"]
    return tuple(o[i] + U[i] * u + V[i] * v for i in range(3))


def _along_plane(elev, other, line, base):
    """The stretch of *elev*'s outline that lies in *other*'s plane, as (t0, t1) along
    *line* from *base*; None where no edge of it does."""
    fo = other["frame"]
    ts = []
    for pg in elev.get("polygons") or []:
        ring = pg["exterior"]
        for q, r in zip(ring, ring[1:] + ring[:1]):
            P, Q = _at(elev["frame"], q[0], q[1]), _at(elev["frame"], r[0], r[1])
            if abs(_dot(fo["n"], _sub(P, fo["origin"]))) <= VALLEY_ON and abs(_dot(fo["n"], _sub(Q, fo["origin"]))) <= VALLEY_ON:
                ts += [_dot(_sub(P, base), line), _dot(_sub(Q, base), line)]
    return (min(ts), max(ts)) if ts else None


RAKE_LEAN = 0.5           # a wall may lean this far (its normal's z: 30° off plumb) and still turn a corner
HIP_SLOPE = 0.02          # a hip's line rises at least this much (its direction's z): level, it is a ridge


def valley_link(a, b):
    """Do pitched elevations a and b meet at a valley? None if not, else {"kind": "valley",
    "a": {"kind", "p1", "p2": the valley line in a's own (u, v), "inside": a point inside
    a, "other_up": the direction into b square to the line (up its slope), "other_n": b's
    normal}, "b": the same for b, "k": tan of half the angle between the normals, "angle",
    "length"}.

    A valley is where two slopes fall towards each other: each face lies in front of the
    other's plane, and each has an edge of its outline along the line the planes meet on,
    level or sloping. The faces have to be modelled meeting on that line: one roof run
    through another has no such edge and is not joined."""
    return _roof_link(a, b, "valley")


def hip_link(a, b):
    """Do pitched elevations a and b meet at a hip? None if not, else what valley_link
    gives, its kind "hip".

    A hip is where two slopes fall away from each other on a sloping line: each face lies
    behind the other's plane, and each has an edge of its outline along the line the planes
    meet on. (On a level line that is a ridge: ridge_link, which puts the two in one
    chain.) "other_up" points into the other face: down its slope, away from the hip."""
    return _roof_link(a, b, "hip")


def rake_link(a, b):
    """Do a pitched face and another (a wall, or a face pitched another way) meet at a
    corner on a raking line? None if not, else what valley_link gives, its kind "rake" and
    "external" True where the corner turns outward. *k* is tan of half the angle between
    the normals, negative at an internal corner (as chain_link's is).

    This is the corner two walls make when one or both of them lean, or a leaning wall
    makes with an upright one: the planes meet on a line that is not plumb, so on at least
    one of the faces the corner is a raking edge. Two upright walls are chain_link's, and
    two roof slopes that both look up meet at a ridge, a hip or a valley. A wall may lean up
    to 30° off plumb (RAKE_LEAN) and still turn a corner: flatter than that it is a roof,
    and where a roof meets a wall is an eaves or an abutment, not a corner."""
    return _roof_link(a, b, "rake")


def _roof_link(a, b, kind):
    fa, fb = a["frame"], b["frame"]
    na, nb = tuple(fa["n"]), tuple(fb["n"])
    if kind == "rake":
        if (abs(na[2]) < 1e-6 and abs(nb[2]) < 1e-6) or (na[2] > 1e-6 and nb[2] > 1e-6):
            return None                               # two walls: a chain corner; two slopes: a roof junction
        if max(abs(na[2]), abs(nb[2])) > RAKE_LEAN:
            return None                               # one of them is a roof, not a leaning wall: an eaves, not a corner
    elif kind in ("eaves", "head"):
        wall, roof = sorted((na, nb), key=lambda v: abs(v[2]))
        if roof[2] <= RAKE_LEAN:
            return None                               # the upper face is no roof slope
        if abs(wall[2]) > EAVES_PLUMB and not (kind == "eaves" and wall[2] > 0 and _pitch_of(wall) - _pitch_of(roof) >= MANSARD_STEP):
            return None                               # neither an upright wall nor (at an eaves) a steeper slope, a mansard's, under the roof
    elif na[2] < 1e-6 or nb[2] < 1e-6:
        return None                                   # a wall, or a face that looks down
    line = _cross(na, nb)
    size = math.sqrt(_dot(line, line))
    if size < 0.05:
        return None                                   # parallel
    line = tuple(c / size for c in line)
    ca, cb = _at(fa, *_centre(a)), _at(fb, *_centre(b))
    front_a, front_b = _dot(nb, _sub(ca, fb["origin"])), _dot(na, _sub(cb, fa["origin"]))
    if kind == "valley" and (front_a <= 0 or front_b <= 0):
        return None                                   # one lies behind the other: a ridge or a hip
    if kind == "hip" and (front_a >= 0 or front_b >= 0 or abs(line[2]) < HIP_SLOPE):
        return None                                   # a valley; or level, a ridge
    if kind == "rake" and front_a * front_b <= 0:
        return None                                   # one in front of the other, one behind: they do not turn a corner
    if kind in ("eaves", "head"):
        if front_a >= 0 or front_b >= 0 or abs(line[2]) > HIP_SLOPE:
            return None                               # on a level line, turning outward
    external = front_a < 0
    # A point on both planes: from a's centre, straight down a's own slope to b's plane.
    down = _cross(na, line)
    step = _dot(nb, down)
    if abs(step) < 1e-9:
        return None
    base = tuple(ca[i] - down[i] * _dot(nb, _sub(ca, fb["origin"])) / step for i in range(3))
    ra, rb = _along_plane(a, b, line, base), _along_plane(b, a, line, base)
    if not ra or not rb:
        return None
    t0, t1 = max(ra[0], rb[0]), min(ra[1], rb[1])
    if t1 - t0 < VALLEY_MIN:
        return None
    ends = [tuple(base[i] + line[i] * t for i in range(3)) for t in (t0, t1)]
    beta = math.acos(max(-1.0, min(1.0, _dot(na, nb))))

    def side(e, centre, other, n_other):
        f = e["frame"]
        local = lambda P: [round(_dot(_sub(P, f["origin"]), f["u"]), 2),                       # noqa: E731
                           round(_dot(_sub(P, f["origin"]), f.get("v") or (0.0, 0.0, 1.0)), 2)]
        up = _cross(tuple(other["frame"]["n"]), line)                 # in the other face, square to the line
        if _dot(up, _sub(centre, base)) < 0:
            up = tuple(-c for c in up)
        return {"kind": kind, "p1": local(ends[0]), "p2": local(ends[1]), "inside": [round(c, 1) for c in _centre(e)],
                "other_up": [round(c, 9) for c in up], "other_n": [round(c, 9) for c in n_other]}
    k = math.tan(beta / 2.0)
    out = {"kind": kind, "a": side(a, cb, b, nb), "b": side(b, ca, a, na),
           "k": round(k if kind != "rake" or external else -k, 4), "angle": round(math.degrees(beta), 1), "length": round(t1 - t0, 1)}
    if kind == "rake":
        out["external"] = external
    if kind in ("eaves", "head"):
        out["a"]["roof"], out["b"]["roof"] = abs(na[2]) > abs(nb[2]), abs(nb[2]) > abs(na[2])
        lower = min((na, nb), key=lambda v: abs(v[2]))
        out["mansard"] = kind == "eaves" and abs(lower[2]) > EAVES_PLUMB      # the face below leans back: a mansard's knuckle
        roof = cb if out["b"]["roof"] else ca
        # the roof rises from the head of the wall: its eaves. It falls away behind it: a monopitch's head.
        if (roof[2] > base[2] + 1.0) != (kind == "eaves") or abs(roof[2] - base[2]) <= 1.0:
            return None
    return out


EAVES_PLUMB = 0.035       # a wall under an eaves is upright: its normal's z no more than this (2 degrees)
MANSARD_STEP = 10.0       # deg – or it is a slope at least this much steeper than the roof: a mansard


def _pitch_of(n):
    """A face's pitch from level, in degrees, from its normal."""
    return math.degrees(math.acos(max(-1.0, min(1.0, abs(n[2])))))


def eaves_link(a, b):
    """Does a roof slope come down to the head of a wall, with no overhang? None if not,
    else what valley_link gives, its kind "eaves", and on each side "roof": is this face the
    roof.

    The wall is upright and the roof a slope (flatter than a leaning wall: steeper, the two
    turn a raking corner). Or the face below is itself a slope at least MANSARD_STEP steeper
    than the roof, looking up: a mansard's knuckle, where the upper, flatter slope runs on
    over the lower one as it would over a wall ("mansard": True). Their planes meet on a level line, the
    wall's top edge and the roof's bottom edge both lie on it, and the corner turns
    outward. A roof that sails on over the wall has no edge on the line and is not this:
    it has an eaves of its own out at its edge, and the wall is healed up under it."""
    return _roof_link(a, b, "eaves")


def head_link(a, b):
    """Is the top edge of a monopitch roof slope the head of a wall it falls away behind?
    None if not, else what eaves_link gives, its kind "head". The same corner as an eaves
    with no overhang, seen from the roof's high side: the wall is upright, the two meet on
    a level line that is the wall's top edge and the roof's top edge, and the corner turns
    outward (cladding_head: the G3 head verge)."""
    return _roof_link(a, b, "head")


def valley_links(a, others):
    """For each of *others*, in order: how it meets a, at a valley (valley_link), a hip
    (hip_link), a raking corner (rake_link) or an eaves on a wall (eaves_link); None where
    it does none of them."""
    return [valley_link(a, b) or hip_link(a, b) or rake_link(a, b) or eaves_link(a, b) or head_link(a, b) for b in others]


HEAL_MAX = 500.0          # mm – an edge this far or less from the line two faces would meet on nearly touches it
HEAL_ON = 2.0             # mm – and this close, it is on it already
HEAL_MIN = 300.0          # mm – the two have to run along at least this much of the line together
HEAL_TRIM_MAX = 1000.0    # mm – a face that runs on past the line by this much or less overshoots it: it can be trimmed back
HEAL_TRIM_SHARE = 0.5     # and what is past the line is under this share of what is short of it: the overshoot, not the face


def _joined(a, b):
    """Are the two joined as they stand: at a corner or a ridge, a valley or a hip?"""
    return bool(chain_link(a, b) or valley_link(a, b) or hip_link(a, b) or rake_link(a, b) or eaves_link(a, b) or head_link(a, b))


def _reach(elev, other, line, base):
    """How *elev* stands to the line its plane meets *other*'s on. (strips, (t0, t1)):
    for each edge of its outline that runs along the line within HEAL_MAX of it, short of
    it, the quad between that edge and the line, in the world; and the stretch of the line
    it covers, edges already on the line included. (None, None) where it has no such edge.
    A face that runs on through the line (a roof oversailing a wall) has nothing to heal
    but is there to be met: ("through", the stretch of the line that lies within it)."""
    f, fo = elev["frame"], other["frame"]
    n, no = tuple(f["n"]), tuple(fo["n"])
    w = _cross(n, line)                               # in the face, square to the line
    rate = _dot(no, w)
    if abs(rate) < 1e-6:
        return None, None
    off = lambda P: -_dot(no, _sub(P, fo["origin"])) / rate          # noqa: E731 — how far along w to the line
    strips, ts, reaches = [], [], False
    for pg in elev.get("polygons") or []:
        ring = pg["exterior"]
        try:
            shape = Polygon(ring)
        except Exception:   # noqa: BLE001
            continue
        m = len(ring)
        wu, wv = _dot(w, f["u"]), _dot(w, f.get("v") or (0.0, 0.0, 1.0))

        def carried(at, before, gap):
            """The corner *at* carried on to the line along the edge that comes into it from
            *before* (so an eaves or a top edge runs straight on), or square to the line
            where that edge runs too nearly along it."""
            du, dv = at[0] - before[0], at[1] - before[1]
            toward = du * wu + dv * wv
            if toward * gap > 1e-9:
                k = gap / toward
                if math.hypot(du, dv) * k <= 4.0 * abs(gap):
                    return _at(f, at[0] + du * k, at[1] + dv * k)
            return _at(f, at[0] + wu * gap, at[1] + wv * gap)
        for i, (q, r) in enumerate(zip(ring, ring[1:] + ring[:1])):
            P, Q = _at(f, q[0], q[1]), _at(f, r[0], r[1])
            a, b = off(P), off(Q)
            length = math.dist(P, Q)
            if length < 50.0 or max(abs(a), abs(b)) > HEAL_MAX:
                continue
            on = max(abs(a), abs(b)) <= HEAL_ON
            reaches = reaches or on
            if not on:
                if abs(a - b) > max(10.0, 0.15 * length) or a * b < 0:
                    continue                          # not along the line, or across it
                # the strip has to lie outside the face: a step from the edge towards the line leaves it
                mid = ((q[0] + r[0]) / 2.0, (q[1] + r[1]) / 2.0)
                step = 1.0 if a + b > 0 else -1.0
                if shape.contains(Point(mid[0] + step * wu * 2.0, mid[1] + step * wv * 2.0)):
                    continue
                P2, Q2 = carried(q, ring[i - 1], a), carried(r, ring[(i + 2) % m], b)
                strips.append({"quad": [list(P), list(Q), list(Q2), list(P2)], "gap": max(abs(a), abs(b))})
            ts += [_dot(_sub(P, base), line), _dot(_sub(Q, base), line)]
        # the face on both sides of the line: it runs on through the other plane
        vs = [off(_at(f, q[0], q[1])) for q in ring]
        if min(vs) < -HEAL_ON and max(vs) > HEAL_ON:
            lu, lv = _dot(line, f["u"]), _dot(line, f.get("v") or (0.0, 0.0, 1.0))
            at = (ring[0][0] + wu * vs[0], ring[0][1] + wv * vs[0])             # a point of the line, in the face
            t_at = _dot(_sub(_at(f, at[0], at[1]), base), line)
            reach = 1e6
            try:
                cut = shape.intersection(LineString([(at[0] - lu * reach, at[1] - lv * reach), (at[0] + lu * reach, at[1] + lv * reach)]))
            except Exception:   # noqa: BLE001
                return None, None
            if cut.is_empty or cut.length < 1.0:
                return None, None
            along = [(x - at[0]) * lu + (y - at[1]) * lv for g in getattr(cut, "geoms", [cut]) for x, y in g.coords]
            return "through", (t_at + min(along), t_at + max(along))
    # A face healed to the line stands at it, though its cladding stops under what sails over.
    for P, Q in elev.get("healed") or []:
        P, Q = tuple(float(c) for c in P), tuple(float(c) for c in Q)
        if max(abs(off(P)), abs(off(Q))) <= HEAL_ON and math.dist(P, Q) >= 50.0:
            reaches = True
            ts += [_dot(_sub(P, base), line), _dot(_sub(Q, base), line)]
    # A face with an edge on the line reaches it: what else of it stands back (the foot of a
    # notch cut round a rafter) is not a near miss.
    return ([] if reaches else strips, (min(ts), max(ts))) if ts else (None, None)


def _overshoot(elev, other, line):
    """Where *elev* runs on through the line its plane meets *other*'s on: the part of it
    past the line, as {"rings": its outlines in the world, "show": the same as triangles
    (each a quad whose last corner is repeated, as the app draws strips), "over": how far
    past the line it runs}. None unless it is an overshoot: the smaller side of the line by
    far (HEAL_TRIM_SHARE) and no more than HEAL_TRIM_MAX deep."""
    from shapely.ops import triangulate
    f, fo = elev["frame"], other["frame"]
    n, no = tuple(f["n"]), tuple(fo["n"])
    w = _cross(n, line)
    rate = _dot(no, w)
    if abs(rate) < 1e-6:
        return None
    V = f.get("v") or (0.0, 0.0, 1.0)
    wu, wv, lu, lv = _dot(w, f["u"]), _dot(w, V), _dot(line, f["u"]), _dot(line, V)
    off = lambda q: -_dot(no, _sub(_at(f, q[0], q[1]), fo["origin"])) / rate     # noqa: E731 — along w from the point to the line
    try:
        shape = _union([Polygon(pg["exterior"], pg.get("holes") or []).buffer(0) for pg in elev.get("polygons") or []])
        q0 = (elev["polygons"][0]["exterior"][0][0], elev["polygons"][0]["exterior"][0][1])
        d0 = off(q0)
        at = (q0[0] + wu * d0, q0[1] + wv * d0)                # a point of the line, in the face
        R = 1e6
        ends = [(at[0] - lu * R, at[1] - lv * R), (at[0] + lu * R, at[1] + lv * R)]
        # each side of the line, by an operation that cannot kill the engine (cladding_booleans._safe)
        sides = [_intersection(shape, Polygon(ends + [(ends[1][0] + s * wu * R, ends[1][1] + s * wv * R),
                                                      (ends[0][0] + s * wu * R, ends[0][1] + s * wv * R)])) for s in (1.0, -1.0)]
    except Exception:   # noqa: BLE001 — a face that will not split is left alone
        return None
    if any(g is None for g in sides):
        return None
    small, big = sorted(sides, key=lambda g: g.area)
    if small.is_empty or small.area < 100.0 or small.area > HEAL_TRIM_SHARE * big.area:
        return None
    parts = [g for g in getattr(small, "geoms", [small]) if g.geom_type == "Polygon" and g.area >= 100.0]
    over = max((abs(off(q)) for g in parts for q in g.exterior.coords), default=0.0)
    if not parts or over <= HEAL_ON or over > HEAL_TRIM_MAX + 0.5:
        return None
    world = lambda q: [round(c, 2) for c in _at(f, q[0], q[1])]                  # noqa: E731
    show = []
    for g in parts:
        for t in triangulate(g):
            if g.contains(t.representative_point()):
                c = [world(q) for q in list(t.exterior.coords)[:3]]
                show.append(c + [c[2]])
    return {"rings": [[world(q) for q in list(g.exterior.coords)[:-1]] for g in parts], "show": show, "over": round(over, 1)}


def heal_link(a, b):
    """Do faces a and b nearly meet, so that extending one or both to the line their planes
    meet on would join them? None if not (they are joined already, are parallel, are too
    far apart, or each runs through the other), else {"a": the quads to add to a, in the world (none where it is
    on the line already), "b": the same for b, "gap": the widest strip, "length": the
    stretch of the line both would cover, "kind": what they would meet at once healed:
    "corner" (two walls), "ridge", "hip" or "valley" (two roof slopes), "rake" (a raking
    corner: a leaning wall and another wall), "edge" (a roof slope and a wall: brought
    together, no junction detailed between them), or "abutment": one of the two runs on through
    the line (a roof oversailing a wall, its plywood carried over the wall's head), so only
    the other is extended, up to it and through whatever it is made of; the one that runs
    on is left as it is}.

    **Where a face runs on a little past the line** (HEAL_TRIM_MAX: a wall drawn through a
    corner, a roof's edge past the wall it lands on) there is also, under "trim", the other
    way to join them (two upright walls up to CORNER_TOL past their corner are a corner as
    they stand, and are not offered): {"kind": what they meet at once trimmed, "a", "b": the outlines to cut
    off each, in the world, "show": those as triangles for the app to draw, "over": how far
    past the line the deepest runs, "add": {"a", "b"} the strips that bring a face that is
    short up to the line}. With nothing to extend (each runs through the other, or the
    other is on the line already) "a" and "b" are empty and "trim" is the only offer."""
    fa, fb = a["frame"], b["frame"]
    na, nb = tuple(fa["n"]), tuple(fb["n"])
    line = _cross(na, nb)
    size = math.sqrt(_dot(line, line))
    if size < 0.05 or _joined(a, b):
        return None
    line = tuple(c / size for c in line)
    ca = _at(fa, *_centre(a))
    down = _cross(na, line)
    step = _dot(nb, down)
    if abs(step) < 1e-9:
        return None
    base = tuple(ca[i] - down[i] * _dot(nb, _sub(ca, fb["origin"])) / step for i in range(3))
    (sa, ra), (sb, rb) = _reach(a, b, line, base), _reach(b, a, line, base)
    past_a, past_b = sa == "through", sb == "through"
    through = past_a or past_b
    sa, sb = ([] if past_a else sa), ([] if past_b else sb)
    if ra is None or rb is None:
        return None
    t0, t1 = max(ra[0], rb[0]), min(ra[1], rb[1])
    if t1 - t0 < HEAL_MIN:
        return None
    wall_a, wall_b = abs(na[2]) < 1e-6, abs(nb[2]) < 1e-6
    if wall_a and wall_b:
        met = "corner"
    elif wall_a or wall_b or na[2] < 0 or nb[2] < 0:
        # a raking corner (a leaning wall and another wall); or a roof and a wall: brought together, no junction detailed
        met = "rake" if max(abs(na[2]), abs(nb[2])) <= RAKE_LEAN else "edge"
    else:
        cb = _at(fb, *_centre(b))
        front = _dot(nb, _sub(ca, fb["origin"])) > 0 and _dot(na, _sub(cb, fa["origin"])) > 0
        met = "valley" if front else ("ridge" if abs(line[2]) < HIP_SLOPE else "hip")
    quads = lambda strips: [[[round(c, 2) for c in p] for p in s["quad"]] for s in strips or []]      # noqa: E731
    # Where a face runs on a little past the line, the other way to bring them together:
    # trim what is past it (and extend the other to the line, where that one is short).
    trim = None
    if through:
        oa = _overshoot(a, b, line) if past_a else None
        ob = _overshoot(b, a, line) if past_b else None
        if (oa or not past_a) and (ob or not past_b):
            trim = {"kind": met, "a": oa["rings"] if oa else [], "b": ob["rings"] if ob else [],
                    "show": (oa["show"] if oa else []) + (ob["show"] if ob else []),
                    "over": max(o["over"] for o in (oa, ob) if o), "add": {"a": quads(sa), "b": quads(sb)}}
    extend = bool(sa or sb) and not (past_a and past_b)       # each runs through the other: nothing nearly meets
    if not extend and not trim:
        return None
    out = {"kind": "abutment" if through else met,            # one runs up to the other, which carries on past
           "a": quads(sa) if extend else [], "b": quads(sb) if extend else [], "length": round(t1 - t0, 1),
           "gap": round(max(s["gap"] for s in (sa or []) + (sb or [])), 1) if extend else 0.0}
    if trim:
        out["trim"] = trim
    return out


def heal_links(a, others):
    """heal_link(a, each of *others*), in order: None where the two do not nearly meet."""
    return [heal_link(a, b) for b in others]


def chain_link(a, b):
    """Do elevations a and b meet at a vertical corner? None if not, else
    {"end_a": "left"|"right", "end_b", "corner_u_a", "corner_u_b", "angle",
     "external", "k"}. Ends are judged in each elevation's own frame (u = 0 is
    the viewer's left). *k* is the mitre slope du/ddepth on the corner's
    bisector plane: positive wraps the buildup round an external corner,
    negative cuts it back into a re-entrant one."""
    fa, fb = a["frame"], b["frame"]
    na, nb = fa["n"], fb["n"]
    if abs(na[2]) > 1e-6 or abs(nb[2]) > 1e-6:
        return ridge_link(a, b)                       # pitched faces meet at a ridge, or not at all
    cross = na[0] * nb[1] - na[1] * nb[0]
    if abs(cross) < 0.05:
        return None                                   # parallel or coplanar
    da = na[0] * fa["origin"][0] + na[1] * fa["origin"][1]
    db = nb[0] * fb["origin"][0] + nb[1] * fb["origin"][1]
    px = (da * nb[1] - db * na[1]) / cross            # plan point on both planes
    py = (na[0] * db - nb[0] * da) / cross
    ends = []
    for e in (a, b):
        f = e["frame"]
        uu = f["u"][0] * (px - f["origin"][0]) + f["u"][1] * (py - f["origin"][1])
        W = float(e["width"])
        if abs(uu) <= CORNER_TOL:
            ends.append(("left", uu))
        elif abs(uu - W) <= CORNER_TOL:
            ends.append(("right", uu))
        else:
            return None
    za0, za1 = fa["origin"][2], fa["origin"][2] + float(a["height"])
    zb0, zb1 = fb["origin"][2], fb["origin"][2] + float(b["height"])
    if min(za1, zb1) - max(za0, zb0) < 300.0:
        return None                                   # no vertical overlap
    # External where b's outward normal points the way a was running into the corner
    # (round the outside of the building); re-entrant where it points back.
    step = 1.0 if ends[0][0] == "right" else -1.0
    ua = fa["u"]
    external = (nb[0] * ua[0] + nb[1] * ua[1]) * step > 0
    beta = math.acos(max(-1.0, min(1.0, na[0] * nb[0] + na[1] * nb[1])))
    k = math.tan(min(beta, math.radians(170.0)) / 2.0)
    return {"end_a": ends[0][0], "end_b": ends[1][0], "corner_u_a": round(ends[0][1], 1),
            "corner_u_b": round(ends[1][1], 1), "external": external,
            "k": round(k if external else -k, 4),
            "angle": round(math.degrees(math.atan2(cross, na[0] * nb[0] + na[1] * nb[1])), 1)}
