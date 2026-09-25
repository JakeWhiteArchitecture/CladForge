"""
CladForge — fabric extraction.

Turns picked wall-face triangles into a named elevation: a coplanar region in
its own (u, v) frame, with openings as interior holes (or notches where they
break the outline), penetrations subtracted, slab/roof abutments detected as
lines (level or pitched) for the splash zone, and the region cut back to the
patches the clicks landed on. Also decides whether two elevations meet at a
corner so the UI can chain them. Runs once per selection (Pyodide or Flask).

Payload (all coordinates IFC mm, Z-up):
  {"name": "Elevation A",
   "faces":   [[[x,y,z],[x,y,z],[x,y,z]], ...],        # picked triangles
   "outward": [nx,ny,nz],                              # hit normal facing the viewer
   "seeds":   [[x,y,z], ...],                          # where each pick was clicked
   "context": [{"type": "IfcSlab", "name": "...", "tris": [...]}, ...],
   "options": {"penetrations": true, "clad_depth": 150.0}}   # depth of the cladding zone

Result: {"ok", "name", "warnings", "frame", "polygons", "notches", "width",
         "height", "area", "n_holes", "n_faces",
         "abutments": [{"u0","u1","v","line":[[u,v],...],"source",...}]}
"""

import math

from shapely.geometry import Polygon, MultiPoint, LineString, Point
from shapely.ops import unary_union, polygonize
from shapely.affinity import translate

from cladding_booleans import iter_polygons, polygon_to_rings, _difference

PLANE_TOL = 25.0          # mm – off-plane distance still treated as on the face
VERTICAL_TOL = 0.087      # sin(5 deg) – flatten faces this close to vertical
MIN_HOLE_AREA = 2500.0    # mm² – ignore holes smaller than 50 x 50
EDGE_MARGIN = 50.0        # mm – abutments this close to the top/bottom are not abutments
CORNER_TOL = 400.0        # mm – a corner may sit this far past an elevation's end (wall thickness)
PITCH_TOL = 20.0          # mm – rise along an abutment line before it counts as pitched
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
    """Intersection of an element with the wall plane, in local (u, v).

    Returns (straddles, touches, section polygon or None, points). The polygon is
    assembled from the plane-crossing segments plus any triangles lying in the
    plane; the convex hull of the points is the fallback for open meshes."""
    pts, segs, inplane, smin, smax = [], [], [], float("inf"), float("-inf")
    for tri in tris:
        s = [_dot(v, n) - d for v in tri]
        smin, smax = min(smin, *s), max(smax, *s)
        if all(abs(x) <= PLANE_TOL for x in s):
            inplane.append(Polygon([_to_local(v, n, u) for v in tri]))
        cut = []
        for i in range(3):
            a, b = tri[i], tri[(i + 1) % 3]
            sa, sb = s[i], s[(i + 1) % 3]
            if abs(sa) <= PLANE_TOL:
                pts.append(_to_local(a, n, u))
            if (sa < -PLANE_TOL and sb > PLANE_TOL) or (sa > PLANE_TOL and sb < -PLANE_TOL):
                t = sa / (sa - sb)
                q = _to_local((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t), n, u)
                pts.append(q)
                cut.append(q)
        if len(cut) == 2 and cut[0] != cut[1]:
            segs.append(LineString(cut))
    if not pts:
        return False, False, None, []
    straddles = smin < -PLANE_TOL and smax > PLANE_TOL
    poly = None
    try:
        parts = [pg for pg in polygonize(unary_union(segs)) if pg.area > MIN_HOLE_AREA] if segs else []
        parts += [pg for pg in inplane if pg.is_valid and pg.area > 1.0]
        if parts:
            poly = unary_union(parts).buffer(0.5, join_style=2).buffer(-0.5, join_style=2)
        if poly is None or poly.is_empty or poly.area <= MIN_HOLE_AREA:
            hull = MultiPoint(pts).convex_hull
            poly = hull if hull.geom_type == "Polygon" and hull.area > MIN_HOLE_AREA else None
    except Exception:
        poly = None
    return straddles, True, poly, pts


def _clip_half(poly, lo):
    """Keep the part of a 3D polygon [(point, signed distance)] with distance >= lo."""
    out = []
    for i, (pa, sa) in enumerate(poly):
        pb, sb = poly[(i + 1) % len(poly)]
        if sa >= lo:
            out.append((pa, sa))
        if (sa >= lo) != (sb >= lo):
            t = (lo - sa) / (sb - sa)
            out.append((tuple(pa[k] + (pb[k] - pa[k]) * t for k in range(3)), lo))
    return out


def _clad_zone(tris, n, d, u, depth):
    """What the element occupies in the cladding zone, projected onto the wall plane.

    A plane section only sees what reaches the wall. A roof finish can stop short of it
    and still sit where the cladding goes, because the cladding stands off the face by
    the whole buildup — so the zone from the face out to *depth* is what has to be
    clear, not the face alone. Returns the footprint, or None."""
    polys = []
    for tri in tris:
        ring = _clip_half([(v, _dot(v, n) - d) for v in tri], -PLANE_TOL)
        ring = _clip_half([(q, -sd) for q, sd in ring], -depth)
        if len(ring) < 3:
            continue
        try:
            pg = Polygon([_to_local(q, n, u) for q, _sd in ring])
            if pg.is_valid and pg.area > 1.0:
                polys.append(pg)
        except Exception:   # noqa: BLE001
            continue
    if not polys:
        return None
    try:
        zone = unary_union(polys).buffer(0.5, join_style=2).buffer(-0.5, join_style=2)
        return None if zone.is_empty or zone.area <= MIN_HOLE_AREA else zone
    except Exception:   # noqa: BLE001
        return None


def _top_line(poly, u0, u1):
    """Upper envelope of a section polygon between u0 and u1, as a simplified polyline.
    Level for a flat roof or slab, sloped where a pitched roof meets a gable."""
    bu0, bv0, bu1, bv1 = poly.bounds
    lo, hi = max(u0, bu0), min(u1, bu1)
    if hi - lo < EDGE_MARGIN:
        return None
    us = sorted({lo, hi} | {round(x, 3) for pg in iter_polygons(poly) for x, _y in pg.exterior.coords if lo < x < hi})
    line = []
    for x in us:
        try:
            hit = LineString([(x, bv0 - 1), (x, bv1 + 1)]).intersection(poly)
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


def _extract(payload):
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
    clad_depth = float(options.get("clad_depth") or 0.0)
    abutments, cuts = [], []
    umin0, vmin0, umax0, vmax0 = region.bounds
    skipped = 0
    for elem in payload.get("context", []):
        etype = (elem.get("type") or "").upper()
        if etype in IGNORE_TYPES or not elem.get("tris"):
            continue
        try:   # one awkward element must not sink the whole extraction
            tris = [[tuple(float(c) for c in v) for v in tri] for tri in elem["tris"]]
            straddles, touches, poly, pts = _section(tris, n, d, u)
            # An abutment counts if it reaches the wall or merely stands in the cladding
            # zone in front of it; a penetration still has to cross the face to be a hole.
            zone = _clad_zone(tris, n, d, u, clad_depth) if etype in ABUTMENT_TYPES and clad_depth > 0 else None
            if (not touches or poly is None) and zone is None:
                continue
            if etype in ABUTMENT_TYPES:
                shape = poly if zone is None else (zone if poly is None else unary_union([poly, zone]))
                line = _top_line(shape, umin0, umax0)
                if line:
                    top = max(v for _u, v in line)
                    if vmin0 + EDGE_MARGIN < top < vmax0 - EDGE_MARGIN:
                        abutments.append({"line": line, "source": elem.get("type") or etype,
                                          "name": elem.get("name", "")})
                if straddles:
                    cuts.append(poly)
            elif straddles and options.get("penetrations", True):
                cuts.append(poly)
        except Exception:   # noqa: BLE001
            skipped += 1
    if skipped:
        result["warnings"].append("%d context element(s) could not be sectioned and were ignored" % skipped)
    for cut_poly in cuts:
        try:
            if cut_poly.intersection(region).area > MIN_HOLE_AREA:
                cut = _difference(region, cut_poly)
                if cut is not None and not cut.is_empty:
                    region = cut
        except Exception:
            continue

    region, dropped = _seeded(region, payload.get("seeds"), u)
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

    notches = _notches(polygons)
    result.update({"ok": True, "frame": make_frame(n, d, umin, vmin), "polygons": polygons,
                   "width": round(width, 2), "height": round(height, 2),
                   "area": round(region.area, 1), "notches": notches,
                   "abutments": _merge_abutments(abutments, umin, vmin, width),
                   "n_holes": sum(len(pg["holes"]) for pg in polygons) + len(notches)})
    if any(a.get("pitched") for a in result["abutments"]):
        result["warnings"].append("Pitched abutment detected — the splash zone follows the roof line; check it")
    return result


def _seeded(region, seeds, u, tol=100.0):
    """(region, dropped), keeping only the patches the clicks landed on. A slab cut
    through a face leaves it in pieces and the piece beyond that junction is another
    wall, so each pick's own point picks its patch: a magic wand, not everything
    coplanar. A point just off a patch takes the nearest; with none, nothing drops."""
    pts = [Point(_dot(s, u), float(s[2])) for s in (seeds or []) if s and len(s) >= 3]
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

def chain_link(a, b):
    """Do elevations a and b meet at a vertical corner? None if not, else
    {"end_a": "left"|"right", "end_b", "corner_u_a", "corner_u_b", "angle",
     "external", "k"}. Ends are judged in each elevation's own frame (u = 0 is
    the viewer's left). *k* is the mitre slope du/ddepth on the corner's
    bisector plane: positive wraps the buildup round an external corner,
    negative cuts it back into a re-entrant one."""
    fa, fb = a["frame"], b["frame"]
    na, nb = fa["n"], fb["n"]
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
