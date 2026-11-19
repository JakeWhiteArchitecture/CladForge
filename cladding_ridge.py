"""CladForge — the ridge: two pitched faces meeting along a level line at their tops.

Each face of the pair carries a "ridge" record (fabric_extract.ridge_link, placed by the
app): the partner's name, the ridge line in this face's own v, the stretch of it the two
share in u, and k = tan(half the angle between the two normals), the slope of the plane
that bisects the ridge. One of the two is the master and carries the cap.

What is modelled, after a ventilated standing seam ridge:

  * Every layer runs up to the bisecting plane and is cut on it (a plumb cut on a roof
    with two equal pitches), so the two slopes' buildups meet above the ridge line rather
    than leaving the open wedge two square ends would. The chain's top edge offset does
    not apply at a ridge. Sheathing and insulation meet;
    battens, deck and cladding stop RIDGE_GAP / 2 short of the plane, measured square to
    it, which leaves the vent slot between the two decks.
  * The ridge cap: one folded sheet over both slopes, RIDGE_LEG down each from its apex,
    resting on the standing seams (or the cladding face), each edge hemmed back under.
  * Under the cap, between the seams of trays that run up the slope, a vented filler strip
    on each slope.

Not modelled: the cap's clips and continuous cleat, the trays' turned-up ends, laps
between lengths of cap."""

import math

from shapely.geometry import LineString
from shapely.geometry.polygon import orient

from cladding_constants import _prism, _rect, frame_to_world, frame_v
from cladding_primitives import clip_bounds_v

RIDGE_GAP = 30.0      # mm – clear between the two slopes' decks, square to the bisecting plane
RIDGE_LEG = 185.0     # mm – the cap, down each slope from its apex
RIDGE_HEM = 15.0      # mm – the hem turned back under each edge of the cap
RIDGE_REST = 2.0      # mm – the cap's underside above what it rests on
FILLER_W = 50.0       # mm – the vented filler under the cap, measured up the slope
FILLER_EDGE = 90.0    # mm – its lower edge, up from the cap's edge (80 for the clip, 10 clear)
FILLER_CLEAR = 12.0   # mm – a filler stops this far from a seam's line: the seam's own width
RIDGE_TOL = 30.0      # mm – an end this close under the top of the cladding is an end at the ridge
SHORT = 200.0         # mm – a batten no longer than this up the slope moves whole rather than stretch
MEET = ("sheathing", "insulation")                               # these meet on the ridge; the rest leave the vent
WHOLE = ("batten", "counter_batten", "cross_batten", "gasket", "closer")   # short ones of these move whole
OWN = ("ridge_cap", "ridge_filler")


def ridge_of(elev):
    """The face's ridge, or None: none recorded, or the cladding is cut off below it (a
    top level picked under the ridge), in which case the face ends as any other does."""
    r = (elev or {}).get("ridge")
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


def _same_frame(a, b):
    return all(abs(x - y) < 1e-6 for key in ("origin", "n") for x, y in zip(a[key], b[key]))


def ridge_ends(meshes, p):
    """Run every part's top end up to the ridge's bisecting plane (module docstring), as a
    shift per ring vertex ("vshift": v += c + d·s, s the depth), added to any the part has."""
    faces = {e.get("name"): (ridge_of(e), e["frame"]) for e in p.get("elevations", [])}
    for m in meshes:
        r, frame = faces.get(m.get("elevation")) or (None, None)
        if not r or m.get("ifc_type") in OWN or not _same_frame(m["frame"], frame):
            continue
        hold = 0.0 if m.get("ifc_type") in MEET else RIDGE_GAP / 2.0 * math.sqrt(1.0 + r["k"] ** 2)
        rings = [m["profile"]] + list(m.get("holes") or [])
        at = [[q[1] >= r["top"] - RIDGE_TOL and r["u0"] - 1.0 <= q[0] <= r["u1"] + 1.0 for q in ring] for ring in rings]
        if not any(at[0]):
            continue
        # Each end is brought to the ridge line from wherever it stopped (the top edge's own
        # offset no longer applies: the vent slot is what is left open here), then held back.
        vs = [q[1] for q in rings[0]]
        whole = m.get("ifc_type") in WHOLE and max(vs) - min(vs) <= SHORT
        old = m.get("vshift") or []
        out = []
        for k, ring in enumerate(rings):
            was = old[k] if k < len(old) and old[k] else [[0.0] * 4] * len(ring)
            out.append([[sh[0], sh[1], sh[2] + (r["v"] - (max(vs) if whole else q[1]) - hold), sh[3] + r["k"]]
                        if (on or whole) else list(sh) for q, sh, on in zip(ring, was, at[k])])
        m["vshift"] = out
    return meshes


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
        up_slope = p.get("seam") and p.get("plank_orient") == "vertical"      # trays and their seams run with the fall
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
        cap = _prism(ring, 0.0, length, {"origin": frame_to_world(frame, start, r["v"], 0.0), "u": level,
                                         "v": [0.0, 0.0, 1.0], "n": along},
                     "ridge_cap", "%s Ridge Cap" % name, name)
        angle = round(math.degrees(2.0 * math.atan(r["k"])), 1)
        cap["ridge_info"] = {"length": round(length, 1), "leg": RIDGE_LEG, "hem": RIDGE_HEM, "thickness": t,
                             "gap": RIDGE_GAP, "angle": angle, "between": [name, other.get("name")]}
        out.append(cap)
        for e, rr, apex in ((elev, r, apex_a), (other, ro, apex_b)):
            edge = rr["v"] + apex - RIDGE_LEG                     # the cap's edge, in this face's v
            info = info_by.get(e.get("name"))
            if info is not None:
                info["ridge"] = dict(cap["ridge_info"], master=e is elev)
                info["ridge_strip"] = _rect(rr["u0"], edge, rr["u1"], max(edge + 1.0, min(float(e["height"]), rr["v"])))
            if up_slope:
                out += _fillers(p, e, rr, edge, meshes, float(info_by.get(e.get("name"), {}).get("total_depth") or 0.0))
    return out


def _fillers(p, elev, r, edge, meshes, face):
    """The vented filler strips on one slope: FILLER_W up the slope, FILLER_EDGE in from
    the cap's edge, as high as the seams, one between each pair of seams."""
    name = elev.get("name")
    lines = sorted({round(float(m["seam_line"]), 1) for m in meshes
                    if m.get("elevation") == name and m.get("ifc_type") == "seam" and m.get("seam_v")
                    and r["u0"] < float(m["seam_line"]) < r["u1"]})
    stops = [r["u0"] - FILLER_CLEAR] + lines + [r["u1"] + FILLER_CLEAR]
    out = []
    for i, (a, b) in enumerate(zip(stops, stops[1:])):
        a, b = a + FILLER_CLEAR, b - FILLER_CLEAR
        if b - a > 20.0:
            out.append(_prism(_rect(a, edge + FILLER_EDGE, b, edge + FILLER_EDGE + FILLER_W), face, float(p["seam_h"]),
                              elev["frame"], "ridge_filler", "%s Ridge Filler %d" % (name, i + 1), name))
    return out
