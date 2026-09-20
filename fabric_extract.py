"""
CladForge — fabric extraction.

Turns picked wall-face triangles into a named elevation: a coplanar region in
its own (u, v) frame, with openings as interior holes, penetrations subtracted
and slab/roof abutments detected for the splash zone. Runs once per selection
(in Pyodide or via Flask) and the result is cached by the caller.

Payload (all coordinates IFC mm, Z-up):
  {"name": "Elevation A",
   "faces":   [[[x,y,z],[x,y,z],[x,y,z]], ...],        # picked triangles
   "outward": [nx,ny,nz],                              # hit normal facing the viewer
   "context": [{"type": "IfcSlab", "name": "...", "tris": [...]}, ...],
   "options": {"penetrations": true}}

Result: {"ok", "name", "warnings", "frame", "polygons", "width", "height",
         "area", "abutments", "n_faces"}
"""

import math

from shapely.geometry import Polygon, MultiPoint
from shapely.ops import unary_union
from shapely.affinity import translate

from cladding_booleans import iter_polygons, polygon_to_rings, _difference

PLANE_TOL = 25.0          # mm – off-plane distance still treated as on the face
VERTICAL_TOL = 0.087      # sin(5 deg) – flatten faces this close to vertical
MIN_HOLE_AREA = 2500.0    # mm² – ignore holes smaller than 50 x 50
EDGE_MARGIN = 50.0        # mm – abutments this close to the top/bottom are not abutments
# Type names are compared upper-cased: web-ifc reports IFCSLAB, IfcOpenShell IfcSlab.
ABUTMENT_TYPES = frozenset({"IFCSLAB", "IFCSLABSTANDARDCASE", "IFCSLABELEMENTEDCASE", "IFCROOF"})
IGNORE_TYPES = frozenset({"IFCWALL", "IFCWALLSTANDARDCASE", "IFCWALLELEMENTEDCASE", "IFCSPACE",
                          "IFCSITE", "IFCOPENINGELEMENT", "IFCOPENINGSTANDARDCASE",
                          "IFCFURNISHINGELEMENT", "IFCCOVERING", "IFCRAILING", "IFCSTAIR",
                          "IFCSTAIRFLIGHT", "IFCVIRTUALELEMENT", "IFCGRID", "IFCANNOTATION",
                          "IFCCURTAINWALL", "IFCPLATE", "IFCMEMBER", "IFCBUILDINGELEMENTPROXY"})


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

def fit_plane(faces, outward=None):
    """Area-weighted plane through the picked triangles.
    Returns (n, d, warnings) with n horizontal and pointing outward, n·p = d on the plane."""
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
    if abs(n[2]) > VERTICAL_TOL:
        warnings.append("Face is %.1f deg off vertical; treated as vertical" % math.degrees(math.asin(min(1, abs(n[2])))))
    n = _norm((n[0], n[1], 0.0))
    if n == (0.0, 0.0, 0.0):
        return None, 0.0, ["Selection is horizontal — pick a wall face"]
    tot, dsum = 0.0, 0.0
    for a, b, c in faces:
        area = math.sqrt(_dot(_cross(_sub(b, a), _sub(c, a)), _cross(_sub(b, a), _sub(c, a)))) / 2
        cen = ((a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3, (a[2] + b[2] + c[2]) / 3)
        dsum += _dot(n, cen) * area
        tot += area
    return n, (dsum / tot if tot else 0.0), warnings


def make_frame(n, d, umin, vmin):
    """Frame dict: origin at local (0, 0), u along the wall (viewer's right), n outward."""
    u = (-n[1], n[0], 0.0)                       # z × n
    origin = (n[0] * d + u[0] * umin, n[1] * d + u[1] * umin, vmin)
    return {"origin": [round(c, 3) for c in origin], "u": [round(c, 6) for c in u],
            "n": [round(c, 6) for c in n]}


def _to_local(p, n, u):
    return (_dot(p, u), p[2])


# ── region ────────────────────────────────────────────────────────────────

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


def _section(tris, n, d, u):
    """Intersection of an element with the wall plane.
    Returns (straddles, touches, hull polygon or None, v_top, v_bot, u0, u1) in local coords."""
    pts, smin, smax = [], float("inf"), float("-inf")
    for tri in tris:
        s = [_dot(v, n) - d for v in tri]
        smin, smax = min(smin, *s), max(smax, *s)
        for i in range(3):
            a, b = tri[i], tri[(i + 1) % 3]
            sa, sb = s[i], s[(i + 1) % 3]
            if abs(sa) <= PLANE_TOL:
                pts.append(_to_local(a, n, u))
            if (sa < -PLANE_TOL and sb > PLANE_TOL) or (sa > PLANE_TOL and sb < -PLANE_TOL):
                t = sa / (sa - sb)
                pts.append(_to_local((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t,
                                      a[2] + (b[2] - a[2]) * t), n, u))
    if not pts:
        return False, False, None, 0, 0, 0, 0
    straddles = smin < -PLANE_TOL and smax > PLANE_TOL
    touches = any(True for _ in pts)
    hull = None
    try:
        h = MultiPoint(pts).convex_hull
        if h.geom_type == "Polygon" and h.area > MIN_HOLE_AREA:
            hull = h
    except Exception:
        pass
    us = [q[0] for q in pts]
    vs = [q[1] for q in pts]
    return straddles, touches, hull, max(vs), min(vs), min(us), max(us)


def extract_elevation(payload):
    """Main entry point. See module docstring for the payload format."""
    name = payload.get("name") or "Elevation"
    faces = _dedupe([[tuple(float(c) for c in v) for v in tri] for tri in payload.get("faces", [])])
    result = {"ok": False, "name": name, "warnings": [], "n_faces": len(faces)}
    if not faces:
        result["warnings"].append("No faces picked")
        return result
    n, d, warnings = fit_plane(faces, payload.get("outward"))
    result["warnings"] += warnings
    if n is None:
        return result
    u = (-n[1], n[0], 0.0)
    region = _union_faces(faces, n, u)
    if region is None or region.is_empty:
        result["warnings"].append("Picked faces have no area")
        return result

    # Context: abutments from slabs/roofs, penetrations from everything else.
    options = payload.get("options") or {}
    abutments, cuts = [], []
    umin0, vmin0, umax0, vmax0 = region.bounds
    for elem in payload.get("context", []):
        etype = (elem.get("type") or "").upper()
        tris = [[tuple(float(c) for c in v) for v in tri] for tri in elem.get("tris", [])]
        if not tris or etype in IGNORE_TYPES:
            continue
        straddles, touches, hull, v_top, v_bot, eu0, eu1 = _section(tris, n, d, u)
        if not touches:
            continue
        if etype in ABUTMENT_TYPES:
            if min(eu1, umax0) - max(eu0, umin0) < EDGE_MARGIN:   # only grazes the region
                continue
            if vmin0 + EDGE_MARGIN < v_top < vmax0 - EDGE_MARGIN:
                abutments.append({"u0": max(eu0, umin0), "u1": min(eu1, umax0), "v": v_top,
                                  "source": elem.get("type") or etype, "name": elem.get("name", ""),
                                  "pitched": (v_top - v_bot) > 300.0 and etype == "IFCROOF"})
            if straddles and hull is not None:
                cuts.append(hull)
        elif straddles and hull is not None and options.get("penetrations", True):
            cuts.append(hull)
    for hull in cuts:
        try:
            if hull.intersection(region).area > MIN_HOLE_AREA:
                cut = _difference(region, hull)
                if cut is not None and not cut.is_empty:
                    region = cut
        except Exception:
            continue

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

    result.update({"ok": True, "frame": make_frame(n, d, umin, vmin), "polygons": polygons,
                   "width": round(width, 2), "height": round(height, 2),
                   "area": round(region.area, 1),
                   "abutments": _merge_abutments(abutments, umin, vmin, width),
                   "n_holes": sum(len(pg["holes"]) for pg in polygons)})
    if any(a.get("pitched") for a in result["abutments"]):
        result["warnings"].append("Pitched roof abutment detected — check the level or set it manually")
    return result


def _merge_abutments(found, umin, vmin, width):
    """Shift to local coords, merge near-identical levels, prepend the base (ground) line."""
    out = [{"u0": 0.0, "u1": round(width, 2), "v": 0.0, "source": "base", "enabled": True,
            "name": "Elevation base"}]
    for ab in sorted(found, key=lambda a: a["v"]):
        v = round(ab["v"] - vmin, 2)
        u0, u1 = round(ab["u0"] - umin, 2), round(ab["u1"] - umin, 2)
        merged = False
        for ex in out[1:]:
            if abs(ex["v"] - v) <= 10.0 and u0 <= ex["u1"] + 10 and u1 >= ex["u0"] - 10:
                ex["u0"], ex["u1"] = min(ex["u0"], u0), max(ex["u1"], u1)
                merged = True
                break
        if not merged:
            out.append({"u0": u0, "u1": u1, "v": v, "source": ab["source"], "enabled": True,
                        "name": ab.get("name") or ab["source"], "pitched": ab.get("pitched", False)})
    return out
