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
    vertical plane of the seam on the rake's line, and its top chamfered across its whole
    width down to that seam's top along the cut, so it meets the standing seam line. Past
    the seam both slopes' trays run to the hip's plane (hip1_lines, "past").

The dimensions are placeholders, the detail's proportions, until GreenCoat's are given."""

import math

from cladding_constants import _prism, frame_to_world, SEAM_STANDOFF
from cladding_ridge import JOIN_TOL, CLAD, MEET  # noqa: F401 — MEET kept for symmetry with cladding_hip

HIP1_W = 47.0          # mm – the batten's width (placeholder)
HIP1_H = 50.0          # mm – its depth at its sides, off the deck (placeholder)
HIP1_GAP = 5.0         # mm – the trays' turned-up ends clear of the batten's sides
HIP1_UNDER = 1.0       # mm – the trays' turned-up ends, under the batten's top
HIP1_CAP_OVER = 15.0   # mm – the flashing out past the upstands, each side (placeholder)
HIP1_CAP_DROP = 10.0   # mm – its edges turned down (placeholder)
HIP1_CHAMFER = 100.0   # mm – under a gablet: the chamfer at Hip 1's top, along the hip, back from the rake's seam
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
    cut = Polygon(ring).buffer(0).intersection(box(cx, -big, cx + big, big) if side > 0 else box(cx - big, -big, cx, big))
    if cut.is_empty or cut.geom_type != "Polygon" or cut.area < 1.0:
        return None
    return [[round(x, 4), round(y, 4)] for x, y in list(orient(cut, 1.0).exterior.coords)[:-1]]


def _chamfer(end, frame, chamfer):
    """The end of a length cut on a plane (cladding_verge._cut_end's sheared end piece), its
    top chamfered: every corner above *chamfer*[0] (the section's height) brought down by
    *chamfer*[1] at the plane, nothing at the piece's far end, so the top falls over the end
    to line through with the abutting seam's top."""
    above, drop = chamfer
    if drop <= 0.5:
        return
    f = end["frame"]
    Y = frame["v"]
    du, dv = -drop * _dot(Y, f["u"]), -drop * _dot(Y, f["v"])                # down, in the end piece's own (u, v)
    run = float(end["thickness"])
    rows = (end.get("vshift") or [[[0.0] * 4 for _q in end["profile"]]])[0]
    out = []
    for (x, y), (a, b, c, d) in zip(end["profile"], rows):
        w = _add(_add(_add(f["origin"], f["u"], x + a), f["v"], y + c), f["n"], 0.0)
        if _dot(_sub(w, frame["origin"]), Y) > above:                # at the plane (depth 0) all of it, none at the far end
            a, b, c, d = a + du, b - du / run, c + dv, d - dv / run
        out.append([a, b, c, d])
    end["vshift"] = [out]


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
                _main, gable = gable
                top_y = max(q[1] for q in sections[-1][0])                   # the flashing's top, in the section
                seam_top = _meet(na, da + stand + seam_h, nb, db + stand + seam_h)[1]
                chamfer = (top_y - HIP1_CAP_DROP - 1.0, top_y - seam_top)     # what drops at the seam, and by how much
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
                    # ended on the rake's seam's plane: the run cut on a plane parallel to it HIP1_CHAMFER back down
                    # the hip, and between the two one length chamfered across its whole width down to the seam's top
                    q, n = gable["Seam"]
                    run = _cut_end(pieces[0], [(None, _add(q, L, -HIP1_CHAMFER), n, "Seam")], at_start=False)
                    tip = _cut_end(pieces[0], [(None, q, n, "Chamfer")], at_start=False)
                    tip = [pc for pc in (tip or []) if pc.get("frame") is not frame]
                    if run and tip:
                        for pc in run + tip:
                            pc.pop("drip_end", None)
                        for pc in tip:
                            pc["thickness"] = round(HIP1_CHAMFER * abs(_dot(L, pc["frame"]["n"])), 4)    # back to the parallel plane
                            _chamfer(pc, frame, chamfer)
                        pieces = run + tip + pieces[1:]
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
