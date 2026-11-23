"""CladForge — the ridge: two pitched faces meeting along a level line at their tops.

Each face of the pair carries a "ridge" record (fabric_extract.ridge_link, placed by the
app): the partner's name, the ridge line in this face's own v, the stretch of it the two
share in u, and k = tan(half the angle between the two normals), the slope of the plane
that bisects the ridge. One of the two is the master and carries the cap.

What is modelled, after VMZINC's standard detail "Ventilated ridge/hip G3"
(VMZ-SN-MA-NO-G3-01):

  * The cladding (trays, boards, panels) runs up to the plane that bisects the ridge and
    stops RIDGE_GAP / 2 short of it, square to the plane: 30 mm clear between the two
    slopes' trays, the vent slot. A standing seam tray is turned up RIDGE_UPSTAND there.
  * The deck and the battens under it are cut square, at least RIDGE_OVERSAIL behind the
    tray's end so the pan oversails the deck, and no nearer the plane than the cladding. Sheathing and insulation, the structure's side of
    the buildup, meet on the bisecting plane.
  * The ridge cap: one folded sheet over both slopes, RIDGE_LEG (the detail's maximum)
    down each from its apex, resting on the standing seams (or the cladding face), each
    edge hemmed back under.
  * Under the cap, between the seams of trays that run up the slope, the decompression
    strip on each slope: FILLER_W up the slope, FILLER_EDGE in from the cap's edge (80 mm
    for the ridge clip and 10 clear), and at least FILLER_GAP short of the trays'
    turned-up ends. On a steep roof that last rule moves it down the slope and the room
    left for the clip is reported.

The chain's top edge offset does not apply at a ridge. Lengths are real: each end is moved
in the part's own outline, so the cutting list has the tray as long as it is; only the
slope of a plumb cut is carried as a shift with depth ("vshift").

Not modelled: the ridge clips (fixed and moving parts), the insect mesh over the slot,
laps between lengths of cap, the lean of the trays' turned-up ends."""

import math

from shapely.geometry import LineString
from shapely.geometry.polygon import orient

from cladding_constants import _prism, _rect, frame_to_world, frame_v
from cladding_primitives import clip_bounds_v

RIDGE_GAP = 30.0      # mm – clear between the two slopes' trays, square to the bisecting plane
RIDGE_UPSTAND = 15.0  # mm – a tray's end turned up at the ridge
RIDGE_OVERSAIL = 10.0  # mm – the deck (and the battens under it) stop this far behind the tray's end
RIDGE_LEG = 185.0     # mm – the cap, down each slope from its apex (the detail's maximum)
RIDGE_HEM = 15.0      # mm – the hem turned back under each edge of the cap (scaled, not dimensioned)
RIDGE_REST = 2.0      # mm – the cap's underside above what it rests on
FILLER_W = 50.0       # mm – the decompression strip under the cap, measured up the slope
FILLER_EDGE = 90.0    # mm – its lower edge, up from the cap's edge (80 for the clip, 10 clear)
FILLER_GAP = 10.0     # mm – and its upper edge at least this far short of the trays' turned-up ends
FILLER_CLEAR = 12.0   # mm – a filler stops this far from a seam's line: the seam's own width
RIDGE_TOL = 30.0      # mm – an end this close under the top of the cladding is an end at the ridge
SHORT = 200.0         # mm – a batten no longer than this up the slope moves whole rather than stretch
MEET = ("sheathing", "insulation")                               # these meet on the ridge
CLAD = ("plank", "panel", "seam")                                # these are cut on the plane, the vent slot short of it
WHOLE = ("batten", "counter_batten", "cross_batten", "gasket", "closer")   # short ones of these move whole
OWN = ("ridge_cap", "ridge_filler")


def ridge_of(elev):
    """The face's ridge, or None: none recorded, or the cladding is cut off below it (a
    top level picked under the ridge), in which case the face ends as any other does."""
    r = (elev or {}).get("ridge") or (elev or {}).get("head_ridge")      # (a monopitch's head verge is cut as half a ridge: cladding_head)
    try:
        r = {"with": r.get("with"), "v": float(r["v"]), "u0": float(r["u0"]), "u1": float(r["u1"]),
             "k": float(r["k"]), "master": bool(r.get("master"))}
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    if r["k"] <= 0 or r["u1"] - r["u0"] < 1.0:
        return None
    top = clip_bounds_v(elev)[1]
    if top < min(float(elev["height"]), r["v"]) - RIDGE_TOL:
        return None
    r["top"] = top
    return r


def head_ridge_of(elev):
    """A face with both a ridge and a monopitch's head verge on another stretch of its top (a
    stepped top: a ridge on one part, a clerestory's head on the higher one): the head's
    record, cut as half a ridge as ridge_of gives it on a face with no ridge. None otherwise."""
    if not (elev or {}).get("ridge") or not (elev or {}).get("head_ridge"):
        return None
    return ridge_of(dict(elev, ridge=None))


JOIN_TOL = 40.0       # mm – a hip's end this close to an end of the ridge runs into it


def meets_hip(elev, r):
    """Does a hip of this face run into an end of its ridge *r*? Then the ridge cap is mitred
    to the hip caps there, and is drawn as its two legs (cladding_hip.cap_legs)."""
    for v in (elev or {}).get("valleys") or []:
        if v.get("kind") != "hip":
            continue
        for q in (v.get("p1"), v.get("p2")):
            try:
                if abs(float(q[1]) - r["v"]) <= JOIN_TOL and min(abs(float(q[0]) - r["u0"]), abs(float(q[0]) - r["u1"])) <= JOIN_TOL:
                    return True
            except (TypeError, ValueError, IndexError):
                continue
    return False


def _same_frame(a, b):
    return all(abs(x - y) < 1e-6 for key in ("origin", "n") for x, y in zip(a[key], b[key]))


def _board(p):
    return float(p["panel_t"] if p.get("cladding_type") == "panel" else p["plank_t"])


def _up_slope(p):
    """Standing seam trays running with the fall: what the detail is drawn for."""
    return bool(p.get("seam")) and p.get("plank_orient") == "vertical"


def _slot(r):
    """Where the cladding stops, as v at depth 0: it stops at _slot + k·s at depth s."""
    return r["v"] - RIDGE_GAP / 2.0 * math.sqrt(1.0 + r["k"] ** 2)


def ridge_ends(meshes, p, infos):
    """Bring every part's top end to the ridge (module docstring). The end is moved in the
    part's own outline, to where it is at the part's mid-depth; a plumb cut's slope is added
    as a shift per ring vertex ("vshift": v += c + d·s, s the depth) to any the part has.
    Returns the meshes with the trays' turned-up ends added."""
    # a face's ridge, and a head verge on another stretch of its top where it has both (head_ridge_of)
    faces = {e.get("name"): ([r for r in (ridge_of(e), head_ridge_of(e)) if r], e["frame"]) for e in p.get("elevations", [])}
    face_by = {i["elevation"]: float(i.get("total_depth") or 0.0) for i in infos}
    board, extra = _board(p), []
    for m in meshes:
        records, frame = faces.get(m.get("elevation")) or ([], None)
        for r in records:
            extra += _to_ridge_end(m, r, frame, p, face_by, board)
    return meshes + extra


def _to_ridge_end(m, r, frame, p, face_by, board):
    """One part's top end brought to the ridge (or head) *r*, along that record's stretch only;
    the tray's turned-up end, if it is one, returned."""
    extra = []
    kind = m.get("ifc_type")
    if kind in OWN or m.get("ridge_part") or not _same_frame(m["frame"], frame):
        return extra
    rings = [m["profile"]] + list(m.get("holes") or [])
    # the part's top at the ridge: the face's top, or the ridge's line where that is lower (a stepped top)
    top = min(r["top"], r["v"]) if r.get("with") else r["top"]
    at = [[q[1] >= top - RIDGE_TOL and r["u0"] - 1.0 <= q[0] <= r["u1"] + 1.0 for q in ring] for ring in rings]
    if not any(at[0]):
        return extra
    face = face_by.get(m.get("elevation"), 0.0)
    if kind in MEET:
        end, shear = r["v"], r["k"]
    elif kind in CLAD:
        end, shear = _slot(r), r["k"]
    else:
        # The deck, battens, closers: cut square, behind the cladding's end, and with the
        # corner nearest the ridge (the back one) no nearer than the cladding comes.
        end, shear = _slot(r) + min(r["k"] * (face - board) - RIDGE_OVERSAIL, r["k"] * float(m["depth"])), 0.0
    mid = float(m["depth"]) + float(m["thickness"]) / 2.0
    vs = [q[1] for q in rings[0]]
    whole = kind in WHOLE and max(vs) - min(vs) <= SHORT
    move = end + shear * mid - max(vs)      # a short batten goes with its top
    old = m.get("vshift") or []
    shifts = []
    for k, ring in enumerate(rings):
        was = old[k] if k < len(old) and old[k] else [[0.0] * 4] * len(ring)
        row = []
        for q, sh, on in zip(ring, was, at[k]):
            if on or whole:
                q[1] = round(q[1] + move if whole else end + shear * mid, 3)
                row.append([sh[0], sh[1], sh[2] - shear * mid, sh[3] + shear])
            else:
                row.append(list(sh))
        shifts.append(row)
    if shear or old:
        m["vshift"] = shifts
    if kind == "plank" and _up_slope(p):    # a tray: its end turned up, between its seams
        us = [q[0] for q, on in zip(rings[0], at[0]) if on]
        top = _slot(r) + r["k"] * (float(m["depth"]) + float(m["thickness"]))
        t = float(m["thickness"])
        if max(us) - min(us) > 20.0:
            up = _prism(_rect(min(us) + 3.0, top - t, max(us) - 3.0, top), float(m["depth"]) + t, RIDGE_UPSTAND,
                        m["frame"], "seam", "%s Ridge Upstand" % m.get("name", "Tray"), m.get("elevation"))
            up.update(opacity=1.0, ridge_part=True)
            extra.append(up)
    return extra


def _section(frame, r):
    """The face in the ridge's cross-section, a plane square to the ridge line with its
    origin on it: (down the slope, out of the face) as (level, up) pairs, the level axis
    pointing down this face's slope."""
    up = frame_v(frame)
    cos_t, sin_t = math.hypot(up[0], up[1]), up[2]
    return (cos_t, -sin_t), (sin_t, cos_t)


def _cap_ring(d_a, n_a, rest_a, d_b, n_b, rest_b, t):
    """The cap's section: its underside rest_a off face a and rest_b off face b, the two
    legs meeting at the apex and running RIDGE_LEG down each slope, each edge hemmed back
    under. Returns (ring, how far up face a's slope the apex stands from the ridge line,
    the same for b)."""
    # rest_a·n_a + ta·d_a = rest_b·n_b + tb·d_b
    det = d_a[0] * (-d_b[1]) - (-d_b[0]) * d_a[1]
    rx, ry = rest_b * n_b[0] - rest_a * n_a[0], rest_b * n_b[1] - rest_a * n_a[1]
    ta = (rx * (-d_b[1]) - (-d_b[0]) * ry) / det
    tb = (d_a[0] * ry - d_a[1] * rx) / det
    apex = (rest_a * n_a[0] + ta * d_a[0], rest_a * n_a[1] + ta * d_a[1])

    def leg(d, n):
        edge = (apex[0] + RIDGE_LEG * d[0], apex[1] + RIDGE_LEG * d[1])
        under = (edge[0] - 2.0 * t * n[0], edge[1] - 2.0 * t * n[1])
        return [edge, under, (under[0] - RIDGE_HEM * d[0], under[1] - RIDGE_HEM * d[1])]
    a, b = leg(d_a, n_a), leg(d_b, n_b)
    path = LineString(b[::-1] + [apex] + a)
    sheet = orient(path.buffer(t / 2.0, cap_style=2, join_style=2, mitre_limit=3.0), 1.0)
    return [[round(x, 3), round(y, 3)] for x, y in list(sheet.exterior.coords)[:-1]], -ta, -tb


def ridge_parts(p, meshes, infos):
    """The cap over each ridge and the vented fillers under it, built once per pair by its
    master. Records what was built on both faces' info ("ridge")."""
    by = {e.get("name"): e for e in p.get("elevations", [])}
    info_by = {i["elevation"]: i for i in infos}
    out = []
    for elev in p.get("elevations", []):
        r = ridge_of(elev)
        other = by.get(r["with"]) if r and r["master"] else None
        ro = ridge_of(other) if other else None
        if not ro:
            continue
        name, frame = elev.get("name"), elev["frame"]
        up_slope = _up_slope(p)                                                # trays and their seams run with the fall
        rest = [float(info_by.get(e.get("name"), {}).get("total_depth") or 0.0)
                + (float(p["seam_h"]) if up_slope else 0.0) + RIDGE_REST for e in (elev, other)]
        t = float(p["seam_t"]) if p.get("seam") else 0.7
        (da, na), (db, nb) = _section(frame, r), _section(other["frame"], ro)
        db, nb = (-db[0], db[1]), (-nb[0], nb[1])                 # the other face falls the other way
        ring, apex_a, apex_b = _cap_ring(da, na, rest[0], db, nb, rest[1], t)
        # The section's level axis points down this face's slope; the cap runs along the ridge.
        up, U = frame_v(frame), frame["u"]
        h = math.hypot(up[0], up[1])
        if h < 1e-6:
            continue                                                          # a level face has no ridge
        level = [-up[0] / h, -up[1] / h, 0.0]
        along = [level[1], -level[0], 0.0]                                    # level × Z
        length = r["u1"] - r["u0"]
        start = r["u0"] if sum(a * b for a, b in zip(along, U)) > 0 else r["u1"]
        angle = round(math.degrees(2.0 * math.atan(r["k"])), 1)
        cap_info = {"length": round(length, 1), "leg": RIDGE_LEG, "hem": RIDGE_HEM, "thickness": t,
                    "gap": RIDGE_GAP, "angle": angle, "between": [name, other.get("name")],
                    "legs": meets_hip(elev, r) or meets_hip(other, ro)}
        if not cap_info["legs"]:        # one folded sheet; mitred to hip caps it is two legs, one a slope
            cap = _prism(ring, 0.0, length, {"origin": frame_to_world(frame, start, r["v"], 0.0), "u": level,
                                             "v": [0.0, 0.0, 1.0], "n": along},
                         "ridge_cap", "%s Ridge Cap" % name, name)
            cap["ridge_info"] = cap_info
            out.append(cap)
        for e, rr, apex in ((elev, r, apex_a), (other, ro, apex_b)):
            edge = rr["v"] + apex - RIDGE_LEG                     # the cap's edge, in this face's v
            face = float(info_by.get(e.get("name"), {}).get("total_depth") or 0.0)
            # The strip sits FILLER_EDGE in from the cap's edge unless that brings it within
            # FILLER_GAP of the trays' turned-up ends: then it moves down, into the clip's room.
            upstand = _slot(rr) + rr["k"] * face
            foot = min(edge + FILLER_EDGE, upstand - FILLER_GAP - FILLER_W)
            info = info_by.get(e.get("name"))
            if info is not None:
                info["ridge"] = dict(cap_info, master=e is elev)
                if up_slope:
                    info["ridge"]["clip_room"] = round(foot - edge - 10.0, 1)      # 80 wanted
                info["ridge_strip"] = _rect(rr["u0"], edge, rr["u1"], max(edge + 1.0, min(float(e["height"]), rr["v"])))
            if up_slope:
                out += _fillers(p, e, rr, foot, meshes, face)
    return out


def _fillers(p, elev, r, foot, meshes, face):
    """The decompression strips on one slope: FILLER_W up the slope from *foot*, from the
    tray to the cap, one between each pair of seams."""
    name = elev.get("name")
    lines = sorted({round(float(m["seam_line"]), 1) for m in meshes
                    if m.get("elevation") == name and m.get("ifc_type") == "seam" and m.get("seam_v")
                    and r["u0"] < float(m["seam_line"]) < r["u1"]})
    stops = [r["u0"] - FILLER_CLEAR] + lines + [r["u1"] + FILLER_CLEAR]
    out = []
    for i, (a, b) in enumerate(zip(stops, stops[1:])):
        a, b = a + FILLER_CLEAR, b - FILLER_CLEAR
        if b - a > 20.0:
            out.append(_prism(_rect(a, foot, b, foot + FILLER_W), face, float(p["seam_h"]) + RIDGE_REST,
                              elev["frame"], "ridge_filler", "%s Ridge Filler %d" % (name, i + 1), name))
    return out
