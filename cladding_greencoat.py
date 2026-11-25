"""CladForge — coated galvanised steel standing seam: GreenCoat PLX's standard details, where
they differ from the zinc (G3) ones. Chosen per chain: "seam_metal" = "steel".

**Hip 1** (GreenCoat detail "Hip 1"). A timber batten runs up the hip line, sitting on the deck,
and the hip flashing (a T-cap, lapped and sealed end to end) is fixed over it.

  * The deck and every layer behind it meet on the plane that bisects the hip: no vent slot.
  * The trays stop HIP1_GAP clear of the batten's sides (cladding_hip.hip_ends, with this
    module's cut) and are turned up against it to just under its top, their seams run up
    full height to the turned-up end (never folded there: cladding_edges.finish_seams).
  * The batten: HIP1_W wide, its sides parallel to the bisecting plane, seated on both decks
    (its underside cut to their V), HIP1_H deep at its sides, its top square to the plane.
  * The flashing: a flat T-cap on the batten's top, out HIP1_CAP_OVER past the upstands each
    side, its edges turned down HIP1_CAP_DROP.
  * At the foot, at the eaves, each half (each slope's side of the hip) of the flashing is
    cut on a plumb plane along its own slope's eaves, HIP1_OVERSAIL past the front of its
    eaves bullnose, and its two chamfered ends closed in the metal, plumb, down to the trays'
    faces, over the end of the batten; the batten and the trays' upstands stop with the plywood. The flashing comes to a point on plan at the corner,
    as the zinc hip cap's legs do. Where two hips meet at the
    top of a hip end, or run up into a ridge, they stop square HIP1_LAP inside the ridge cap's
    end: the cap ends in a pyramid over the apex (cladding_hip.hip_parts).
  * **A hip under a gable** (a gablet: the hip runs up to the foot of the gable's rake): cut
    as the zinc hip cap is there, full height, batten, flashing and upstands alike, on the
    vertical plane of the seam on the rake's line, and its top chamfered: one face from the
    line square across the hip where that seam first meets it, falling to the cut, so the
    top comes down to the seam's top at the far corner. Past the seam both slopes' trays run
    to the hip's plane (hip1_lines, "past").

The dimensions are placeholders, the detail's proportions, until GreenCoat's are given."""

import math

import shapely

from cladding_constants import _prism, frame_to_world, SEAM_STANDOFF
from cladding_ridge import JOIN_TOL, CLAD, MEET  # noqa: F401 — MEET kept for symmetry with cladding_hip

GRID = 0.01          # mm – overlays of shapes that share edges are run on this grid

HIP1_W = 47.0          # mm – the batten's width (placeholder)
HIP1_H = 50.0          # mm – its depth at its sides, off the deck (placeholder)
HIP1_GAP = 5.0         # mm – the trays' turned-up ends clear of the batten's sides
HIP1_UNDER = 1.0       # mm – the trays' turned-up ends, under the batten's top
HIP1_CAP_OVER = 15.0   # mm – the flashing out past the upstands, each side (placeholder)
HIP1_CAP_DROP = 10.0   # mm – its edges turned down (placeholder)
HIP1_LAP = 100.0       # mm – a hip run up under the ridge cap's little hip: its end this far inside the cap's
HIP1_OVERSAIL = 50.0   # mm – the hip's foot past the front of the eaves bullnose, on plan


def steel(p):
    """The chain is coated galvanised steel standing seam: GreenCoat's details."""
    return bool(p.get("seam")) and p.get("seam_metal") == "steel"


def gablet_seams(p, infos):
    """Every hip that runs up under a gablet's gable, by its two faces: (a point on the
    rake's seam's plane, the level unit normal pointing past it, up to the hip's top), the
    plane Hip 1 is cut on there (cladding_hip._hip_tops)."""
    from cladding_hip import _hip_tops
    info_by = {i["elevation"]: i for i in infos}
    out = {}
    for e in p.get("elevations", []):
        info = info_by.get(e.get("name"))
        if info is None:
            continue
        for g, S, T, u_s in _hip_tops(e, p, info):
            U = [float(c) for c in e["frame"]["u"]]
            past = U if T[0] > u_s else [-c for c in U]
            out[frozenset((e.get("name"), g["with"]))] = (frame_to_world(e["frame"], S[0], S[1], 0.0), past)
    return out


def past_fill(meshes, p, main, end, h, S, T):
    """Under a gablet, past the rake's seam (where Hip 1 is cut, cladding_hip.gablet_hip_tops):
    a tray that crosses the seam's line keeps its end clear of the batten all along the hip,
    so on each slope a strip of the deck between the tray's end and the hip's plane, from the
    seam up to the hip's top, would be left open. It is filled with the trays' own sheet, laid
    as they are, clipped to the slope's outline and clear of the trays already there."""
    from shapely.geometry import Polygon
    from cladding_hip import hips_of
    from cladding_ridge import _same_frame
    from cladding_primitives import ring_at
    if end is None:
        return []
    out = []
    t = float(p["seam_t"])
    world_s = frame_to_world(main["frame"], S[0], S[1], 0.0)
    world_t = frame_to_world(main["frame"], T[0], T[1], 0.0)
    for face, other in ((main, end), (end, main)):
        f = face["frame"]
        O, U, V = f["origin"], [float(c) for c in f["u"]], [float(c) for c in (f.get("v") or (0, 0, 1))]
        local = lambda w: (_dot(_sub(w, O), U), _dot(_sub(w, O), V))        # noqa: E731
        line = next((x for x in hips_of(face) if x["with"] == other.get("name")), None)
        trays = [m for m in meshes if m.get("elevation") == face.get("name") and m.get("ifc_type") == "plank" and _same_frame(m["frame"], f)]
        if line is None or not trays:
            continue
        depth = min(float(m["depth"]) for m in trays)
        mid = depth + t / 2.0
        # from the hip's plane (beyond the hip's line on the face, as the plane leans over it) to the trays' ends
        near = -line["k"] * mid
        far = (HIP1_W / 2.0 + HIP1_GAP + t) * math.sqrt(1.0 + line["k"] ** 2) - line["k"] * mid
        (sx, sy), (tx, ty), (mx, my) = local(world_s), local(world_t), line["m"]
        strip = Polygon([(sx + near * mx, sy + near * my), (tx + near * mx, ty + near * my),
                         (tx + far * mx, ty + far * my), (sx + far * mx, sy + far * my)])
        try:
            # side by side, the trays share their edges: joined and cut on the grid, where GEOS
            # cannot fail (a TopologyException kills the engine in the browser)
            covered = shapely.union_all([Polygon(ring_at(m, 0, mid)).buffer(0) for m in trays], grid_size=GRID)
            fill = shapely.difference(strip.buffer(0), covered, grid_size=GRID)
        except (ValueError, TypeError):
            continue
        for k, g in enumerate(getattr(fill, "geoms", [fill])):
            if g.geom_type != "Polygon" or g.area < 25.0:
                continue
            ring = [[round(x, 3), round(y, 3)] for x, y in list(g.exterior.coords)[:-1]]
            piece = _prism(ring, depth, t, f, "seam", "%s Hip Fill %d" % (face.get("name"), k + 1), face.get("name"))
            piece.update(opacity=1.0, hip_part=True, hip1=True)
            out.append(piece)
    return out


def hip1_lines(hips, name=None, seams=None):
    """The face's hips (cladding_hip.hips_of) with the trays stood back clear of the batten.
    Where a hip runs up under a gablet's gable (*seams*: gablet_seams), Hip 1 is cut on the
    rake's seam's plane, so past it ("past": the plane) both slopes' cladding runs to the
    hip's plane, as plain slopes meet."""
    out = []
    for h in hips:
        back = (HIP1_W / 2.0 + HIP1_GAP) * math.sqrt(1.0 + h["k"] ** 2)
        line = dict(h, back=back, hip1=True)
        past = (seams or {}).get(frozenset((name, h["with"])))
        if past:
            line["past"] = past
        out.append(line)
    return out


def on_hip(q, hips, tol=80.0):
    """The point *q* of a face lies on (within *tol* of) one of its hips (hips_of)."""
    for h in hips:
        (x1, y1), (x2, y2) = h["p1"], h["p2"]
        dx, dy = (x2 - x1) / h["length"], (y2 - y1) / h["length"]
        along = (q[0] - x1) * dx + (q[1] - y1) * dy
        across = abs((q[0] - x1) * dy - (q[1] - y1) * dx)
        if across <= tol and -tol <= along <= h["length"] + tol:
            return True
    return False


def little_hip(elev, h, lines):
    """Where the ridge cap's little hip ends on this hip of the face: the hip's top runs into
    a ridge, or meets another hip, and the cap carries on over it, its legs down the hip to
    where the ridge cap's edge line (cladding_ridge.RIDGE_LEG down the slope from the top)
    crosses it. (that point, the unit direction down the hip) in the face's (u, v); None
    where nothing meets the hip's top (or the hip is too short for it)."""
    from cladding_ridge import RIDGE_LEG
    (x1, y1), (x2, y2) = h["p1"], h["p2"]
    up, low = ((x2, y2), (x1, y1)) if y2 > y1 else ((x1, y1), (x2, y2))
    same = lambda o: o.get("kind", "hip") == "hip" and o["p1"] == h["p1"] and o["p2"] == h["p2"]     # noqa: E731
    if not any(not same(o) and min(math.hypot(q[0] - up[0], q[1] - up[1]) for q in (o["p1"], o["p2"])) <= JOIN_TOL
               for o in lines):
        return None
    rise = up[1] - low[1]
    if rise < 1.0 or RIDGE_LEG >= 0.8 * rise:
        return None
    f = RIDGE_LEG / rise
    size = math.hypot(low[0] - up[0], low[1] - up[1])
    return (up[0] + f * (low[0] - up[0]), up[1] + f * (low[1] - up[1])), ((low[0] - up[0]) / size, (low[1] - up[1]) / size)


def little_rest(meshes, elev, t):
    """How far off a steel face the ridge cap's underside stands where it runs over Hip 1:
    on the flashing's top (the batten's depth off the deck, and the flashing), and clear of
    its edges, which stand higher off the slope as it falls away from the hip (by the hip's
    lean, cladding_valley.valleys_of "k", for each mm out from the batten's side)."""
    from cladding_hip import hips_of
    top = _deck_top(meshes, elev.get("name"), elev["frame"])
    if top is None:
        return None
    k = max([h["k"] for h in hips_of(elev)] or [0.0])
    return top + HIP1_H + t + 2.0 + k * (HIP1_GAP + t + HIP1_CAP_OVER)


def hip1_cut(h, kind, near, t, _face, _board, _elev, _p):
    """Where a part of this kind ends at a Hip 1 hip: the cladding clear of the batten, every
    layer behind it on the bisecting plane (cladding_hip._hip_cut's terms)."""
    k, mid = h["k"], near + t / 2.0
    if kind in CLAD:
        return h["back"] - k * mid, k
    return -k * mid, k


# ── vectors ──

def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _sub(a, b):
    return [x - y for x, y in zip(a, b)]


def _add(a, b, f=1.0):
    return [x + f * y for x, y in zip(a, b)]


def _unit(a):
    size = math.sqrt(_dot(a, a))
    return [x / size for x in a] if size > 1e-9 else None


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _plan(a):
    return _unit([a[0], a[1], 0.0])


# ── the section ──

def _line_y(n, d, x):
    """y on the line n·(x, y) = d."""
    return (d - x * n[0]) / n[1]


def _meet(n1, d1, n2, d2):
    det = n1[0] * n2[1] - n1[1] * n2[0]
    return ((d1 * n2[1] - d2 * n1[1]) / det, (n1[0] * d2 - n2[0] * d1) / det)


def _ccw(ring, *rows):
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1]))
    if area >= 0:
        return (ring,) + rows
    return (ring[::-1],) + tuple(r[::-1] for r in rows)


def _sections(na, da, fa, nb, db, fb, sa, t):
    """The parts' sections across the hip, each (ring, the ring where it has tapered to
    nothing, kind, label, the side of the hip it is on or 0 for both), and the flashing's
    open end on each side. *na*, *nb*: the faces' normals in the section; *da*, *db*: their
    decks' tops; *fa*, *fb*: their trays' faces; *sa*: which way along x face a lies."""
    sb = -sa
    q0 = _meet(na, da, nb, db)                                    # the decks' arris
    fq = _meet(na, fa, nb, fb)                                    # the trays' faces' arris
    cx = q0[0]
    side = lambda x: (na, da, fa) if (x - cx) * sa > 0 else (nb, db, fb)       # noqa: E731
    deck = lambda x: _line_y(side(x)[0], side(x)[1], x)                         # noqa: E731
    face = lambda x: _line_y(side(x)[0], side(x)[2], x)                         # noqa: E731
    xa, xb = cx + sa * HIP1_W / 2.0, cx + sb * HIP1_W / 2.0
    top = max(deck(xa), deck(xb)) + HIP1_H
    out = []
    # the batten, seated on both decks, with a point over the arris for the taper
    batten = [(xa, deck(xa)), q0, (xb, deck(xb)), (xb, top), (cx, top), (xa, top)]
    flat = [(xa, deck(xa)), q0, (xb, deck(xb)), (xb, deck(xb) + 1.0), (cx, q0[1] + 1.0), (xa, deck(xa) + 1.0)]
    out.append(_ccw(batten, flat) + ("batten", "Hip Batten", 0))
    # each slope's trays turned up against it, to just under its top, parallel to its side
    for sd in (sa, sb):
        x0, x1 = cx + sd * (HIP1_W / 2.0 + HIP1_GAP), cx + sd * (HIP1_W / 2.0 + HIP1_GAP + t)
        up = [(x0, face(x0) - t), (x1, face(x1) - t), (x1, top - HIP1_UNDER), (x0, top - HIP1_UNDER)]
        down = [(x0, face(x0) - t), (x1, face(x1) - t), (x1, face(x1)), (x0, face(x0))]
        out.append(_ccw(up, down) + ("seam", "Hip Upstand" if sd == sa else "Hip Upstand Far", sd))
    # the flashing: a flat T-cap on the batten, out over the upstands, each edge turned down
    e = HIP1_W / 2.0 + HIP1_GAP + t + HIP1_CAP_OVER
    pts, ends = [], []
    for x, y, end in (
            (cx + sa * e, top - HIP1_CAP_DROP, face(cx + sa * e) + 1.0 + t / 2.0),
            (cx + sa * e, top + t, face(cx + sa * e) + 1.0 + t),
            (cx, top + t, fq[1] + 1.0 + t),
            (cx + sb * e, top + t, face(cx + sb * e) + 1.0 + t),
            (cx + sb * e, top - HIP1_CAP_DROP, face(cx + sb * e) + 1.0 + t / 2.0),
            (cx + sb * (e - t), top - HIP1_CAP_DROP, face(cx + sb * (e - t)) + 1.0 + t / 2.0),
            (cx + sb * (e - t), top, face(cx + sb * (e - t)) + 1.0),
            (cx, top, fq[1] + 1.0),
            (cx + sa * (e - t), top, face(cx + sa * (e - t)) + 1.0),
            (cx + sa * (e - t), top - HIP1_CAP_DROP, face(cx + sa * (e - t)) + 1.0 + t / 2.0)):
        pts.append((x, y))
        ends.append((x, end))
    out.append(_ccw(pts, ends) + ("ridge_cap", "Hip Flashing", 0))
    xo_a, xo_b = cx + sa * e, cx + sb * e      # the flashing's end at the eaves: closed down to the trays' faces
    mouth = {sa: [(cx, fq[1]), (xo_a, face(xo_a)), (xo_a, top + t), (cx, top + t)],
             sb: [(cx, fq[1]), (xo_b, face(xo_b)), (xo_b, top + t), (cx, top + t)]}
    return out, mouth, cx


def _half(ring, cx, side):
    """The part of a section on one side (*side*: +1 or -1 along x) of the line x = cx."""
    from shapely.geometry import Polygon, box
    from shapely.geometry.polygon import orient
    big = 1e5
    cut = shapely.intersection(Polygon(ring).buffer(0), box(cx, -big, cx + big, big) if side > 0 else box(cx - big, -big, cx, big), grid_size=GRID)
    if cut.is_empty or cut.geom_type != "Polygon" or cut.area < 1.0:
        return None
    return [[round(x, 4), round(y, 4)] for x, y in list(orient(cut, 1.0).exterior.coords)[:-1]]


def _chamfer(end, frame, chamfer, square, slope=None):
    """The end of a length cut on a plane (cladding_verge._cut_end's sheared end piece, its
    depth 0 on the plane), its top chamfered: one flat face falling from the length's square
    end (the plane square to the hip through *square*: where the cut plane first meets it)
    to the cut, so that at the corner furthest up the hip the top comes down to the abutting
    seam's top. *chamfer*: (the section's height above which a corner is the top, a function
    giving how far a world point's top must come down to reach the seam's top there). *slope*:
    the fall per mm back from the square end, where another part has set it (the flashing's,
    so the batten and the upstands stay under it). Returns the slope."""
    above, need = chamfer
    f = end["frame"]
    L, Y = frame["n"], frame["v"]
    rows = (end.get("vshift") or [[[0.0] * 4 for _q in end["profile"]]])[0]
    tops = []
    for k, ((x, y), (a, b, c, d)) in enumerate(zip(end["profile"], rows)):
        w = _add(_add(f["origin"], f["u"], x + a), f["v"], y + c)            # the corner, on the cut plane
        if _dot(_sub(w, frame["origin"]), Y) <= above:
            continue
        run = _add(_add([b * cu for cu in f["u"]], f["v"], d), f["n"])       # its line, per mm of the piece's depth
        along = _dot(run, L)
        back = _dot(_sub(square, w), L) / along if abs(along) > 1e-9 else 0.0  # depth to the square end
        tops.append((k, w, max(0.0, back)))
    if not tops:
        return slope
    if slope is None:
        far = max(tops, key=lambda t: t[2])
        if far[2] < 1.0:
            return None
        slope = max(0.0, need(far[1])) / far[2]                               # one face: the fall per mm back from the square end
    du, dv = -_dot(Y, f["u"]), -_dot(Y, f["v"])                                # down, in the piece's own (u, v)
    out = [list(r_) for r_ in rows]
    for k, _w, back in tops:
        drop = slope * back
        a, b, c, d = out[k]
        out[k] = [a + du * drop, b - du * slope, c + dv * drop, d - dv * slope]   # all of it at the cut, none at the square end
    end["vshift"] = [out]
    return slope


def _stop_end(mouth, frame, foot, phi, t, title, name):
    """Half the flashing's end at the eaves closed: a plate *t* thick lying in the plane
    through *foot* square to *phi* (plumb, pointing back up the hip), over that half of the
    flashing's section seen along the hip."""
    O, X, Y, L = frame["origin"], frame["u"], frame["v"], frame["n"]
    out = [-c for c in phi]
    U = _unit(_cross([0.0, 0.0, 1.0], out))
    V = _cross(out, U)
    ring = []
    for x, y in mouth:
        q = _add(_add(O, X, x), Y, y)
        s = _dot(_sub(foot, q), phi) / _dot(L, phi)                # along the hip to the plane
        q = _add(q, L, s)
        ring.append((_dot(_sub(q, foot), U), _dot(_sub(q, foot), V)))
    ring = _ccw(ring)[0]
    plate = _prism(ring, 0.0, t, {"origin": list(foot), "u": U, "v": V, "n": out}, "ridge_cap", title, name)
    plate.update(hip_part=True, hip1=True, stop_end=True, tray_sheet=True)
    return plate


def _bullnose_front(meshes, elev, corner, V, t):
    """(The outermost point of a slope's eaves bullnose, level with its middle; the plywood's
    end, at its underside) in the world (cladding_eaves.eaves_paths: the trays' sheet rolled
    round the plywood's end), beside the eaves *corner* (a point on its eaves edge, on its face)."""
    from cladding_eaves import apron_drop, EAVES_SUPPORT_T
    from cladding_ridge import _same_frame
    frame = elev["frame"]
    mine = [m for m in meshes if m.get("elevation") == elev.get("name") and _same_frame(m["frame"], frame)]
    trays = [float(m["depth"]) for m in mine if m.get("ifc_type") == "plank"]
    decks = [float(m["depth"]) for m in mine if m.get("ifc_type") == "deck"]
    back = min(trays) if trays else 0.0
    soffit = min(decks) if decks else back
    pitch = min(float(elev.get("pitch") or 0.0), 80.0)
    sn, cs = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    nose, out, _below = apron_drop(soffit, pitch)
    yt, yb = back + t / 2.0, soffit - EAVES_SUPPORT_T - t
    end = (out - soffit * sn) / cs + nose                             # down the slope from the edge to the plywood's end
    down = end + (yt - yb) / 2.0                                      # and to the roll's front
    N = [float(c) for c in frame["n"]]
    return _add(_add(corner, V, -down), N, (yt + yb) / 2.0), _add(_add(corner, V, -end), N, soffit)


def _deck_top(meshes, name, frame):
    from cladding_ridge import _same_frame
    tops = [float(m["depth"]) + float(m["thickness"]) for m in meshes
            if m.get("elevation") == name and m.get("ifc_type") == "deck" and _same_frame(m["frame"], frame)]
    return max(tops) if tops else None


def hip1_parts(meshes, p, infos):
    """The batten and the flashing over every hip of a steel chain, built once per hip by its
    master face, along the hip in a frame of its own (u across, v out of the hip, n up it).
    Records each face's hips on its info as cladding_hip.hip_parts does."""
    from cladding_hip import hips_of, _hip_tops, _cap_lines
    from cladding_eaves import has_eaves
    from cladding_ridge import _up_slope
    from cladding_verge import _cut_end
    by = {e.get("name"): e for e in p.get("elevations", [])}
    info_by = {i["elevation"]: i for i in infos}
    t = float(p["seam_t"])
    seam_h = float(p["seam_h"]) if _up_slope(p) else 0.0
    # every hip's ends in the world, to mitre hips meeting at the top of a hip end
    segs, tops = [], {}
    for e in p.get("elevations", []):
        for h in hips_of(e):
            if h["master"]:
                segs.append((e.get("name"), h["with"], frame_to_world(e["frame"], *h["p1"], 0.0), frame_to_world(e["frame"], *h["p2"], 0.0)))
        info = info_by.get(e.get("name"))
        if info is not None:
            for h, S, T, u_s in _hip_tops(e, p, info):          # cut as the zinc cap is there (cladding_hip.gablet_hip_tops)
                U, V = [float(c) for c in e["frame"]["u"]], [float(c) for c in (e["frame"].get("v") or (0, 0, 1))]
                out_dir = [-c for c in U] if T[0] > u_s else U
                tops[frozenset((e.get("name"), h["with"]))] = e.get("name"), [
                    (frame_to_world(e["frame"], S[0], S[1], 0.0), [-c for c in out_dir], "Seam"),     # the rake's seam's plane
                    (frame_to_world(e["frame"], T[0], T[1], 0.0), _plan(V), "Gable")]                 # square across at the hip's top
    extra = []
    for elev in p.get("elevations", []):
        name = elev.get("name")
        for h in hips_of(elev):
            other = by.get(h["with"])
            if not h["master"] or other is None:
                continue
            fa, fb = elev["frame"], other["frame"]
            da, db = _deck_top(meshes, name, fa), _deck_top(meshes, other.get("name"), fb)
            if da is None or db is None:
                continue
            w1, w2 = frame_to_world(fa, *h["p1"], 0.0), frame_to_world(fa, *h["p2"], 0.0)
            lo, hi, plo = (w1, w2, h["p1"]) if w1[2] <= w2[2] else (w2, w1, h["p2"])
            L = _unit(_sub(hi, lo))
            NA, NB = [float(c) for c in fa["n"]], [float(c) for c in fb["n"]]
            Y = _unit(_add(NA, NB))
            if L is None or Y is None:
                continue
            X = _cross(Y, L)
            na, nb = (_dot(NA, X), _dot(NA, Y)), (_dot(NB, X), _dot(NB, Y))
            into = _add([c * h["m"][0] for c in fa["u"]], [c * h["m"][1] for c in (fa.get("v") or (0, 0, 1))])
            sa = 1.0 if _dot(into, X) > 0 else -1.0
            stand = SEAM_STANDOFF + t
            sections, mouth, cx = _sections(na, da, da + stand, nb, db, db + stand, sa, t)
            # the foot: each half cut on a plumb plane along its own slope's eaves, past the trays'
            # ends, so the two come to a point on plan; square to the hip where a slope has no eaves
            phi = _plan(L)
            feet, timber = {}, {}                                        # side: (a point of the plane, its normal up the hip)
            for e, side in ((elev, sa), (other, -sa)):
                V = [float(c) for c in (e["frame"].get("v") or (0, 0, 1))]
                level = _plan(V)
                if has_eaves(e, p) and level and _dot(level, phi or level) > 0.05:
                    front, ply = _bullnose_front(meshes, e, lo, V, t)
                    feet[side] = (_add(front, level, -HIP1_OVERSAIL), level)
                    timber[side] = (ply, level)                          # the batten stops with the plywood
                elif phi:
                    feet[side] = timber[side] = (lo, phi)
            eaves = any(has_eaves(e, p) for e in (elev, other))
            s_feet = [_dot(_sub(q, lo), n) / _dot(L, n) for q, n in feet.values()]
            s_foot = min(s_feet) if s_feet else 0.0
            s_top = _dot(_sub(hi, lo), L)
            # the top: under a gablet's gable, cut on the rake seam's plane and across at the hip's top;
            # under the ridge cap's pyramid, square; or mitred to the hip it meets
            gable = tops.get(frozenset((name, h["with"])))
            little = little_hip(elev, h, _cap_lines(elev, by)) if gable is None else None
            top_cut = None
            if gable is not None:
                main, gable = gable
                top_y = max(q[1] for q in sections[-1][0])                   # the flashing's top, in the section
                mf, md = (fa, da) if main == name else (fb, db)               # the main slope, and its deck's top
                MN = [float(c) for c in mf["n"]]
                seam_level = md + stand + seam_h                                # the standing seams' tops, off its face

                def need(w, MN=MN, mo=mf["origin"], level=seam_level, Y=Y):
                    return (_dot(_sub(w, mo), MN) - level) / max(0.2, _dot(Y, MN))    # down to the seam's top, along the section's up
                chamfer = (top_y - HIP1_CAP_DROP - 1.0, need)
                gable = {lab: (q, n) for q, n, lab in gable if n and _dot(L, n) > 0.05}       # planes that stop it going up the hip
                if len(gable) < 2:
                    gable = None
                else:
                    s_top = max(_dot(_sub(q, lo), n) / _dot(L, n) for q, n in gable.values())
            elif little is not None:
                q = frame_to_world(fa, little[0][0], little[0][1], 0.0)
                top_cut = (_add(q, L, HIP1_LAP), phi or L)              # square across, inside the cap's end
            else:
                for nm, wt, q1, q2 in segs:
                    if {nm, wt} == {name, h["with"]}:
                        continue
                    for q, r in ((q1, q2), (q2, q1)):
                        if math.dist(q, hi) <= JOIN_TOL * 2:
                            a_dir, b_dir = _plan(_sub(lo, hi)), _plan(_sub(r, q))
                            if a_dir and b_dir:
                                normal = _unit(_sub(a_dir, b_dir))
                                if normal:
                                    top_cut = (hi, normal)
                    if top_cut:
                        break
            origin = lo
            frame = {"origin": origin, "u": X, "v": Y, "n": L}
            start = s_foot - 300.0 if feet else s_foot
            end = s_top + (300.0 if top_cut or gable else 0.0)
            short = lambda n: str(n or "").replace("Elevation ", "")              # noqa: E731
            tips = []                                                    # the cut ends to chamfer, all on the flashing's slope
            for ring, flat, kind, label, part_side in sections:
                title = "%s %s %s" % (name, label, short(h["with"]))
                if part_side:                                            # an upstand: the tray of its own slope
                    title = "%s Hip Upstand %s" % ((name, short(h["with"])) if part_side == sa else (h["with"], short(name)))
                piece = _prism(ring, start, end - start, frame, kind, title, name)
                piece.update(hip_part=True, hip1=True, opacity=1.0)
                if part_side:
                    piece["elevation"] = name if part_side == sa else h["with"]
                pieces = [piece]
                if feet:
                    planes = feet if kind == "ridge_cap" else timber      # the batten and the trays' upstands stop at the eaves
                    halves = [(_half(ring, cx, side) if not part_side else (None if side == part_side else False), q, n,
                               "Foot %s" % ("L" if side > 0 else "R")) for side, (q, n) in planes.items()]
                    halves = [c for c in halves if c[0] is not False and (c[0] or part_side)]
                    cut = _cut_end(piece, halves, at_start=True)
                    if cut:
                        for pc in cut:
                            pc.pop("drip_end", None)
                        pieces = cut
                if top_cut:
                    last = pieces[0]
                    cut = _cut_end(last, [(None, top_cut[0], top_cut[1], "Mitre")], at_start=False)
                    if cut:
                        pieces = cut + pieces[1:]
                if gable:
                    # ended on the rake's seam's plane: the length cut square where the seam first meets it, and the
                    # end piece from there to the plane chamfered, from nothing on the square line down to the seam's top
                    cut = _cut_end(pieces[0], [(None, *gable["Seam"], "Seam")], at_start=False)
                    if cut:
                        keep = next((pc for pc in cut if pc.get("frame") is frame), None)
                        square = _add(frame["origin"], L, float(keep["depth"]) + float(keep["thickness"])) if keep else None
                        for pc in cut:
                            pc.pop("drip_end", None)
                            if pc is not keep and square is not None:
                                tips.append((kind, pc, square))
                        pieces = cut + pieces[1:]
                for k, pc in enumerate(pieces):
                    if k and not pc.get("of"):
                        pc["of"] = title
                    pc["name"] = pc.get("name") or title
                    pc["hip1"] = True
                if kind == "ridge_cap":
                    pieces[0]["hip_info"] = {"length": round(s_top - s_foot, 1), "detail": "Hip 1", "thickness": t,
                                             "between": [name, h["with"]], "batten": [HIP1_W, HIP1_H],
                                             "gablet": bool(gable)}
                extra += pieces
            slope = None
            for kind, pc, square in sorted(tips, key=lambda t: t[0] != "ridge_cap"):
                slope = _chamfer(pc, frame, chamfer, square, slope)
            if eaves:
                for side, (q, n) in feet.items():
                    extra.append(_stop_end(mouth[side], frame, q, n, t, "%s Hip Stop End %s %s" % (name, short(h["with"]), "L" if side > 0 else "R"), name))
            for e, hh in ((elev, h), (other, None)):
                info = info_by.get(e.get("name"))
                if info is None:
                    continue
                info.setdefault("hips", []).append({
                    "with": h["with"] if hh else name, "master": hh is not None, "length": round(h["length"], 1),
                    "detail": "Hip 1", "batten": [HIP1_W, HIP1_H], "gap": HIP1_GAP,
                    "gablet": bool(gable), "angle": round(math.degrees(2.0 * math.atan(h["k"])), 1)})
    return meshes + extra


# ── the cladding drip (GreenCoat "Cladding drip") ──

DRIP_UP = 30.0       # mm – the drip flashing's back leg up the plywood's face, behind the trays (placeholder)
DRIP_DOWN = 25.0     # mm – its drip below the plywood's foot, hemmed (placeholder)
DRIP_TURN = 20.0     # mm – its return under the plywood, turned up the plywood's back (placeholder)
MESH_T = 1.0         # mm – the insect mesh across the cavity under the plywood
FOLD_AIR = 2.0       # the air round the drip's leg inside the tray's fold, in sheet thicknesses
FOLD_HOOK = 15.0     # mm – the tray's fold turned back up behind the drip's leg (placeholder)


def _wall(frame):
    v = frame.get("v") or (0.0, 0.0, 1.0)
    return abs(float(v[2])) > 0.999                                      # stood up: a wall, not a slope


def _drip_ring(xb, xf, v0, t, step=0.0):
    """The drip flashing in section, (out of the face, up): fixed DRIP_UP up the plywood's face
    *xf* and on down past its foot *v0* as the drip's leg, DRIP_DOWN below it; and from that
    corner in under the plywood's foot to its back *xb* and DRIP_TURN up it. The tray hooks
    over the leg (_fold_ring): the fold is the tray's own, not the flashing's. *step*: the leg
    stepped out that far at the plywood's foot (falling DRIP_STEP_FALL), then down."""
    from shapely.geometry import LineString
    from shapely.geometry.polygon import orient
    f = xf + t / 2.0
    if step:
        leg = LineString([(f, v0 + DRIP_UP), (f, v0), (f + step, v0 - DRIP_STEP_FALL), (f + step, v0 - DRIP_STEP_FALL - DRIP_DOWN)])
    else:
        leg = LineString([(f, v0 + DRIP_UP), (f, v0 - DRIP_DOWN)])
    under = LineString([(f, v0 - t / 2.0), (xb - t / 2.0, v0 - t / 2.0), (xb - t / 2.0, v0 + DRIP_TURN)])
    sheet = shapely.union_all([g.buffer(t / 2.0, cap_style=2, join_style=2, mitre_limit=3.0) for g in (leg, under)], grid_size=GRID)
    sheet = orient(sheet if sheet.geom_type == "Polygon" else max(sheet.geoms, key=lambda g: g.area), 1.0)
    return [[round(x, 3), round(y, 3)] for x, y in list(sheet.exterior.coords)[:-1]]


def _fold_ring(xt, xf, v0, t, step=0.0):
    """The tray's own foot hooked over the drip's leg, in section: from the tray's flat (its
    back at *xt*) round under the leg's end, FOLD_AIR clear of it, and FOLD_HOOK back up behind
    it. *step*: over a stepped drip, from the plywood's foot out along the step and down the
    stepped leg first. Returns (ring, the level the tray's flat comes down to)."""
    from shapely.geometry import LineString
    from shapely.geometry.polygon import orient
    air = FOLD_AIR * t
    fall = DRIP_STEP_FALL if step else 0.0
    bottom = v0 - fall - DRIP_DOWN - air - t / 2.0                     # the fold's sheet, centred, under the leg's end
    back = xf + step - air - t / 2.0                                   # its return, behind the leg (the leg is xf..xf+t)
    x = xt + t / 2.0
    if step:
        rise = x - (xf + t / 2.0)                                      # the tray stands this far off the leg
        path = [(x, v0 + 2.0 * t), (x, v0 + rise * DRIP_STEP_FALL / step), (x + step, v0 - fall + rise * DRIP_STEP_FALL / step),
                (x + step, bottom), (back, bottom), (back, bottom + FOLD_HOOK)]
        sheet = orient(LineString(path).buffer(t / 2.0, cap_style=2, join_style=2, mitre_limit=3.0), 1.0)
        return [[round(a, 3), round(b, 3)] for a, b in list(sheet.exterior.coords)[:-1]], v0 + 2.0 * t
    path = [(x, bottom + 2.0 * t), (x, bottom), (back, bottom), (back, bottom + FOLD_HOOK)]
    sheet = orient(LineString(path).buffer(t / 2.0, cap_style=2, join_style=2, mitre_limit=3.0), 1.0)
    return [[round(x, 3), round(y, 3)] for x, y in list(sheet.exterior.coords)[:-1]], bottom - t / 2.0


DRIP_NOTCH = 60.0    # mm – a long level edge this near the outline's lowest point is still its foot
DRIP_STEP = 25.0     # mm – a stepped drip: its leg brought this far out past the plywood's face, over cladding below
DRIP_STEP_FALL = 5.0 # mm – the step's fall outward
DRIP_CLEAR = 5.0     # mm – cladding below whose face comes within this of the drip's leg, or out past it, steps it


def drip_steps(params):
    """Before anything is built: each standing seam wall with a drip at its foot is told where
    the cladding on a wall below, in its plane, stands out to the drip's leg or past it (boards
    on battens and counter-battens under a skirt): "drip_steps", [(u0, u1)] in its own u. There
    the drip is stepped out DRIP_STEP (cladding_drips), so it laps over that cladding."""
    from cladding_constants import _parse, chain_params
    from cladding_primitives import buildup_depth
    elevs = [e for e in params.get("elevations") or [] if e.get("ok", True) and e.get("polygons") and e.get("frame")]
    dot = lambda a, b: sum(x * y for x, y in zip(a, b))                     # noqa: E731
    for e in elevs:
        e.pop("drip_steps", None)
    parsed = {}
    for e in elevs:
        try:
            parsed[e.get("name")] = _parse(dict(chain_params(params, e), elevations=[]))
        except Exception:   # noqa: BLE001
            continue
    for e in elevs:
        pe = parsed.get(e.get("name"))
        f = e["frame"]
        if not pe or not pe.get("seam") or not (steel(pe) or pe.get("base_drip")) or not _wall(f):
            continue
        leg = buildup_depth(pe) - float(pe["plank_t"]) - 2.0              # the drip's leg: on the plywood's face
        V = f.get("v") or (0.0, 0.0, 1.0)
        pts = [q for pg in e["polygons"] for q in pg["exterior"]]
        foot = min(q[1] for q in pts)
        z_foot = frame_to_world(f, 0.0, foot, 0.0)[2]
        found = []
        for w in elevs:
            pw, fw = parsed.get(w.get("name")), w["frame"]
            if w is e or not pw or not _wall(fw) or dot(fw["n"], f["n"]) < 0.98:
                continue
            off = dot([a - b for a, b in zip(fw["origin"], f["origin"])], f["n"])    # its wall face, out from this one's
            if abs(off) > 150.0:
                continue
            out = off + buildup_depth(pw) + (float(pw["seam_h"]) if pw.get("seam") else 0.0)
            if out < leg - DRIP_CLEAR:
                continue
            wpts = [frame_to_world(fw, q[0], q[1], 0.0) for pg in w["polygons"] for q in pg["exterior"]]
            if abs(max(p[2] for p in wpts) - z_foot) > 100.0:
                continue                                                      # not under this one's foot
            us = [dot([a - b for a, b in zip(p, f["origin"])], f["u"]) for p in wpts]
            u0, u1 = max(min(us), min(q[0] for q in pts)), min(max(us), max(q[0] for q in pts))
            if u1 - u0 > 100.0:
                found.append((round(u0, 1), round(u1, 1)))
        if found:
            e["drip_steps"] = sorted(found)


def cladding_drips(meshes, p, infos):
    """GreenCoat's cladding drip at the foot of a steel standing seam wall. Along every level
    bottom edge of the wall's plywood: the drip flashing (_drip_ring), fixed to the plywood's
    face behind the trays, its drip DRIP_DOWN below the plywood's foot and its return under the
    foot turned up the plywood's back; an insect mesh across the cavity under the plywood, from
    the back of the battens to that return. The trays and their seams are carried down over the
    drip, and each tray's foot is turned round its leg's end and back up behind it: the tray's
    own fold (_fold_ring, FOLD_AIR of air round the leg), one element with the tray. The seams
    run down with the tray to the fold, their bottom ends folded at 45° or square as the chain
    sets them (cladding_edges.finish_seams). Built on steel, and on any metal where the chain
    asks for it ("base_drip": a zinc skirt's foot over a wall below)."""
    from cladding_ridge import _same_frame
    if not (steel(p) or p.get("base_drip")):
        return meshes
    info_by = {i["elevation"]: i for i in infos}
    t = float(p["seam_t"])
    extra = []
    for elev in p.get("elevations", []):
        name, frame = elev.get("name"), elev["frame"]
        if not _wall(frame):
            continue
        mine = [m for m in meshes if m.get("elevation") == name and _same_frame(m["frame"], frame)]
        decks = [m for m in mine if m["ifc_type"] == "deck"]
        if not decks:
            continue
        timbers = [m for m in mine if m["ifc_type"] in ("batten", "counter_batten")]
        back_of_cavity = min([float(m["depth"]) for m in timbers] or [float(decks[0]["depth"])])
        runs = []
        for d in decks:
            ring = d["profile"]
            # the foot: the lowest long level edge (a corner's notch a few mm below it, a stray
            # vertex, is not the foot)
            level = [min(a[1], b[1]) for a, b in zip(ring, ring[1:] + ring[:1]) if abs(a[1] - b[1]) < 0.5 and abs(b[0] - a[0]) > 50.0]
            low = min(q[1] for q in ring)
            v0 = min((v for v in level if v - low <= DRIP_NOTCH), default=low)
            for a, b in zip(ring, ring[1:] + ring[:1]):
                if abs(a[1] - v0) < 0.5 and abs(b[1] - v0) < 0.5 and abs(b[0] - a[0]) > 50.0:
                    runs.append((min(a[0], b[0]), max(a[0], b[0]), v0, float(d["depth"]), float(d["depth"]) + float(d["thickness"])))
        if not runs:
            continue
        U, V, N = [float(c) for c in frame["u"]], [float(c) for c in frame.get("v") or (0, 0, 1)], [float(c) for c in frame["n"]]
        steps = elev.get("drip_steps") or []
        split = []
        for (u0, u1, v0, xb, xf) in runs:
            cuts = sorted({u0, u1} | {x for a, b in steps for x in (a, b) if u0 < x < u1})
            for a, b in zip(cuts, cuts[1:]):
                stepped = any(s0 - 1.0 <= (a + b) / 2.0 <= s1 + 1.0 for s0, s1 in steps)
                split.append((a, b, v0, xb, xf, DRIP_STEP if stepped else 0.0))
        for k, (u0, u1, v0, xb, xf, step) in enumerate(split):
            # the section in (out of the face, up), run along the wall from u1 back to u0 (out × up = -along)
            sec = {"origin": frame_to_world(frame, u1, 0.0, 0.0), "u": N, "v": V, "n": [-c for c in U]}
            drip = _prism(_drip_ring(xb, xf, v0, t, step), 0.0, u1 - u0, sec, "eaves_strip", "%s Cladding Drip %d" % (name, k + 1), name)
            drip.update(opacity=1.0, cladding_drip=True, drip_step=step)
            extra.append(drip)
            mesh = _prism([[back_of_cavity, v0 - t - MESH_T], [xb - t, v0 - t - MESH_T], [xb - t, v0 - t], [back_of_cavity, v0 - t]],
                          0.0, u1 - u0, sec, "dpm", "%s Insect Mesh %d" % (name, k + 1), name)
            mesh.update(color="#3a3f44", insect_mesh=True)
            extra.append(mesh)
            # the trays and their seams carried down over the drip, each tray's foot turned round
            # the drip's leg as its own fold
            for m in mine:
                if m["ifc_type"] not in ("plank", "seam") or float(m["depth"]) < xf - 0.5:
                    continue
                ring = m["profile"]
                at = [q for q in ring if abs(q[1] - v0) < 0.5 and u0 - 0.5 <= q[0] <= u1 + 0.5]
                if not at:
                    continue
                fold, low = _fold_ring(float(m["depth"]), xf, v0, t, step)
                m["profile"] = [[q[0], low] if abs(q[1] - v0) < 0.5 and u0 - 0.5 <= q[0] <= u1 + 0.5 else q for q in ring]
                if m["ifc_type"] == "plank" and len(at) >= 2:
                    a, b = min(q[0] for q in at), max(q[0] for q in at)
                    if b - a > 5.0:
                        tray_sec = dict(sec, origin=frame_to_world(frame, b, 0.0, 0.0))
                        piece = _prism(fold, 0.0, b - a, tray_sec, "seam", "%s Drip Fold" % m.get("name", "Tray"), name)
                        piece.update(opacity=1.0, of=m.get("of") or m.get("name"), drip_fold=True)
                        extra.append(piece)
        info = info_by.get(name)
        if info is not None:
            info["cladding_drip"] = {"runs": len(split), "length": round(sum(r[1] - r[0] for r in split), 1),
                                     "stepped": round(sum(r[1] - r[0] for r in split if r[5]), 1), "step": DRIP_STEP,
                                     "down": DRIP_DOWN, "up": DRIP_UP, "turn": DRIP_TURN, "fold_air": round(FOLD_AIR * t, 2)}
    return meshes + extra
