"""CladForge — coated galvanised steel standing seam: GreenCoat PLX's standard details, where
they differ from the zinc (G3) ones. Chosen per chain: "seam_metal" = "steel".

**Hip 1** (GreenCoat detail "Hip 1"). A timber batten runs up the hip line, sitting on the deck,
and the hip flashing (a T-cap, lapped and sealed end to end) is fixed over it.

  * The deck and every layer behind it meet on the plane that bisects the hip: no vent slot.
  * The trays stop HIP1_GAP clear of the batten's sides and are turned up HIP1_UPSTAND
    against it (cladding_hip.hip_ends, with this module's cut).
  * The batten: HIP1_W wide, its sides parallel to the bisecting plane, seated on both decks
    (its underside cut to their V), HIP1_H deep at its sides, its top square to the plane.
  * The flashing: its top on the batten, its legs down each side outside the trays'
    upstands, their feet clear of the standing seams.
  * At the foot, at the eaves, both are cut on a vertical plane square to the hip on plan,
    HIP_PROJECT past the trays' ends (as the zinc hip cap is). Where two hips meet at the
    top of a hip end they are mitred on the vertical plane between them; the ridge cap,
    which then runs whole to the ridge's end, laps over the joint.
  * **A hip under a gable** (a gablet: the hip runs up to the foot of the gable's rake): the
    batten tapers to nothing at the seam on the rake's line, its top laid HIP1_FALL off level
    (falling down the hip, so it drains), and the flashing comes down with it to lie on the
    trays there. How far the taper runs depends on the hip's pitch: the batten's depth over
    the difference between the hip's rise and the fall.

The dimensions are placeholders, the detail's proportions, until GreenCoat's are given."""

import math

from cladding_constants import _prism, frame_to_world, SEAM_STANDOFF
from cladding_ridge import JOIN_TOL, CLAD, MEET  # noqa: F401 — MEET kept for symmetry with cladding_hip

HIP1_W = 47.0          # mm – the batten's width (placeholder)
HIP1_H = 50.0          # mm – its depth at its sides, off the deck (placeholder)
HIP1_GAP = 5.0         # mm – the trays' turned-up ends clear of the batten's sides
HIP1_UPSTAND = 35.0    # mm – the trays turned up against the batten, off their face
HIP1_CLEAR = 2.0       # mm – the flashing's legs off the trays' upstands
HIP1_SEAM_CLEAR = 5.0  # mm – the legs' feet above the standing seams' tops
HIP1_FALL = 3.0        # degrees – the batten's top off level where it tapers under a gable
TAPER_MAX = 1500.0     # mm – the longest taper, along the hip, however flat the hip


def steel(p):
    """The chain is coated galvanised steel standing seam: GreenCoat's details."""
    return bool(p.get("seam")) and p.get("seam_metal") == "steel"


def hip1_lines(hips):
    """The face's hips (cladding_hip.hips_of) with the trays stood back clear of the batten."""
    out = []
    for h in hips:
        back = (HIP1_W / 2.0 + HIP1_GAP) * math.sqrt(1.0 + h["k"] ** 2)
        out.append(dict(h, back=back, hip1=True, upstand=HIP1_UPSTAND))
    return out


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


def _sections(na, da, fa, nb, db, fb, sa, t, seam_h):
    """The batten's and the flashing's sections, each as (ring, the ring where it has
    tapered to nothing). *na*, *nb*: the faces' normals in the section; *da*, *db*: their
    decks' tops; *fa*, *fb*: their trays' faces; *sa*: which way along x face a lies."""
    sb = -sa
    q0 = _meet(na, da, nb, db)                                    # the decks' arris
    cx = q0[0]
    side = lambda x: (na, da, fa) if (x - cx) * sa > 0 else (nb, db, fb)       # noqa: E731
    deck = lambda x: _line_y(side(x)[0], side(x)[1], x)                         # noqa: E731
    face = lambda x: _line_y(side(x)[0], side(x)[2], x)                         # noqa: E731
    xa, xb = cx + sa * HIP1_W / 2.0, cx + sb * HIP1_W / 2.0
    top = max(deck(xa), deck(xb)) + HIP1_H
    batten = [(xa, deck(xa)), q0, (xb, deck(xb)), (xb, top), (xa, top)]
    flat = [(xa, deck(xa)), q0, (xb, deck(xb)), (xb, deck(xb) + 1.0), (cx, q0[1] + 1.0), (xa, deck(xa) + 1.0)]
    batten = batten[:4] + [(cx, top)] + batten[4:]                # a point over the arris, for the taper
    # the flashing: an inverted U over the batten and the upstands, its legs' feet clear of the seams
    a = HIP1_W / 2.0 + HIP1_GAP + t + HIP1_CLEAR
    fq = _meet(na, fa, nb, fb)                                    # the trays' faces' arris
    pts, ends = [], []
    for x, y, end in (
            (cx + sa * (a + t), face(cx + sa * (a + t)) + seam_h + HIP1_SEAM_CLEAR, (face(cx + sa * (a + t)) + 1.0)),
            (cx + sa * (a + t), top + t, face(cx + sa * (a + t)) + 1.0 + t),
            (cx, top + t, fq[1] + 1.0 + t),
            (cx + sb * (a + t), top + t, face(cx + sb * (a + t)) + 1.0 + t),
            (cx + sb * (a + t), face(cx + sb * (a + t)) + seam_h + HIP1_SEAM_CLEAR, face(cx + sb * (a + t)) + 1.0),
            (cx + sb * a, face(cx + sb * a) + seam_h + HIP1_SEAM_CLEAR, face(cx + sb * a) + 1.0),
            (cx + sb * a, top, face(cx + sb * a) + 1.0),
            (cx, top, fq[1] + 1.0),
            (cx + sa * a, top, face(cx + sa * a) + 1.0),
            (cx + sa * a, face(cx + sa * a) + seam_h + HIP1_SEAM_CLEAR, face(cx + sa * a) + 1.0)):
        pts.append((x, y))
        ends.append((x, end))
    b_ring, b_end = _ccw(batten, flat)
    c_ring, c_end = _ccw(pts, ends)
    xo_a, xo_b = cx + sa * (a + t), cx + sb * (a + t)               # the flashing's open end, down to the trays
    mouth = [(xo_a, face(xo_a)), (xo_a, top + t), (xo_b, top + t), (xo_b, face(xo_b)), (cx, fq[1])]
    return (b_ring, b_end), (c_ring, c_end), mouth


def _taper_shift(ring, end, s0, length):
    """vshift rows taking *ring* at depth s0 to *end* at s0 + length."""
    rows = []
    for (x, y), (xe, ye) in zip(ring, end):
        bu, bv = (xe - x) / length, (ye - y) / length
        rows.append([-bu * s0, bu, -bv * s0, bv])
    return [rows]


def _stop_end(mouth, frame, foot, phi, t, title, name):
    """The flashing's end at the eaves closed: a plate *t* thick lying in the foot's plane
    (vertical, square to the hip on plan), over the flashing's section seen along the hip."""
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


def _deck_top(meshes, name, frame):
    from cladding_ridge import _same_frame
    tops = [float(m["depth"]) + float(m["thickness"]) for m in meshes
            if m.get("elevation") == name and m.get("ifc_type") == "deck" and _same_frame(m["frame"], frame)]
    return max(tops) if tops else None


def hip1_parts(meshes, p, infos):
    """The batten and the flashing over every hip of a steel chain, built once per hip by its
    master face, along the hip in a frame of its own (u across, v out of the hip, n up it).
    Records each face's hips on its info as cladding_hip.hip_parts does."""
    from cladding_hip import hips_of, _hip_tops, HIP_PROJECT
    from cladding_eaves import EAVES_TRAY, has_eaves
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
            for h, S, _T, _u in _hip_tops(e, p, info):
                tops[frozenset((e.get("name"), h["with"]))] = frame_to_world(e["frame"], S[0], S[1], 0.0)
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
            (b_ring, b_end), (c_ring, c_end), mouth = _sections(na, da, da + stand, nb, db, db + stand, sa, t, seam_h)
            # the foot: past the trays' ends at the eaves, on a vertical plane square to the hip on plan
            phi = _plan(L)
            foot, eaves = lo, False
            if has_eaves(elev, p) or has_eaves(other, p):
                (x1, y1), (x2, y2) = h["p1"], h["p2"]
                dx, dy = (plo[0] - (x1 + x2 - plo[0])) / h["length"], (plo[1] - (y1 + y2 - plo[1])) / h["length"]
                if dy < -0.1:
                    e_more = (EAVES_TRAY + HIP_PROJECT) / -dy
                    foot = frame_to_world(fa, plo[0] + dx * e_more, plo[1] + dy * e_more, 0.0)
                    eaves = True
            s_foot = _dot(_sub(foot, lo), L)
            s_top = _dot(_sub(hi, lo), L)
            # the top: tapered under a gable, or mitred to the hip it meets
            S = tops.get(frozenset((name, h["with"])))
            taper = None
            top_cut = None
            if S is not None:
                s_s = _dot(_sub(S, lo), L)
                rise = L[2] - math.hypot(L[0], L[1]) * math.tan(math.radians(HIP1_FALL))
                length = TAPER_MAX if rise <= HIP1_H / TAPER_MAX else HIP1_H / rise
                length = min(length, s_s - s_foot - 50.0)
                if length > 10.0:
                    taper = (s_s - length, length)
                    s_top = s_s
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
            start = s_foot - 300.0 if phi else s_foot
            end = s_top + (300.0 if top_cut else 0.0)
            short = lambda n: str(n or "").replace("Elevation ", "")              # noqa: E731
            for ring, flat, kind, label in ((b_ring, b_end, "batten", "Hip Batten"), (c_ring, c_end, "ridge_cap", "Hip Flashing")):
                title = "%s %s %s" % (name, label, short(h["with"]))
                run_end = taper[0] if taper else end
                piece = _prism(ring, start, run_end - start, frame, kind, title, name)
                piece.update(hip_part=True, hip1=True)
                pieces = [piece]
                if phi and s_foot > start:
                    cut = _cut_end(piece, [(None, foot, phi, "Foot")], at_start=True)
                    if cut:
                        pieces = cut
                if top_cut:
                    last = pieces[0]
                    cut = _cut_end(last, [(None, top_cut[0], top_cut[1], "Mitre")], at_start=False)
                    if cut:
                        pieces = cut + pieces[1:]
                if taper:
                    tp = _prism(ring, taper[0], taper[1], frame, kind, title + " Taper", name)
                    tp["vshift"] = _taper_shift(ring, flat, taper[0], taper[1])
                    tp.update(hip_part=True, hip1=True, of=title)
                    pieces.append(tp)
                for k, pc in enumerate(pieces):
                    if k and not pc.get("of"):
                        pc["of"] = title
                    pc["name"] = pc.get("name") or title
                    pc["hip1"] = True
                if kind == "ridge_cap":
                    pieces[0]["hip_info"] = {"length": round(s_top - s_foot, 1), "detail": "Hip 1", "thickness": t,
                                             "between": [name, h["with"]], "batten": [HIP1_W, HIP1_H],
                                             "taper": round(taper[1], 1) if taper else None}
                extra += pieces
            if eaves and phi:
                extra.append(_stop_end(mouth, frame, foot, phi, t, "%s Hip Stop End %s" % (name, short(h["with"])), name))
            for e, hh in ((elev, h), (other, None)):
                info = info_by.get(e.get("name"))
                if info is None:
                    continue
                info.setdefault("hips", []).append({
                    "with": h["with"] if hh else name, "master": hh is not None, "length": round(h["length"], 1),
                    "detail": "Hip 1", "batten": [HIP1_W, HIP1_H], "gap": HIP1_GAP, "upstand": HIP1_UPSTAND,
                    "taper": round(taper[1], 1) if taper else None, "angle": round(math.degrees(2.0 * math.atan(h["k"])), 1)})
    return meshes + extra
