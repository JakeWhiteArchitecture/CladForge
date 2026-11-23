"""CladForge — the head verge: the level top edge of a monopitch roof slope clad in standing
seam trays that run with the fall, where no other slope meets it at a ridge.

After VMZINC's standard detail "Head verge G3" (VMZ-SN-MA-NO-G3, G3 on a wall with
cladding). It is half a ridge, and is built as one:

  * The trays stop RIDGE_GAP / 2 short of a plumb plane and are turned up there, the deck
    and battens cut square behind them, exactly as at a ridge (cladding_ridge.ridge_ends,
    given a ridge record with no partner: "head_ridge").
  * Where the top edge is the head of a clad wall the roof falls away from (fabric_extract.
    head_link, kind "head" among the face's "valleys"), **the roof runs on over the wall's
    build-up**: that plane stands HEAD_BACK inside the line of the cap's drop, not at the
    roof's own edge, so the battens' cavity runs out over the wall's and the two vent
    together. The wall is left as it is: it stops its own top offset under the roof.
  * The cap, the G3 ridge piece: on the roof side it lies over the seams as a ridge cap's
    leg does, HEAD_COVER down the slope from the trays' ends, its edge hemmed back under;
    it carries on up the slope's line to the corner and there turns down plumb. The drop
    (the detail's folded strip, drawn in one with the cap) hangs HEAD_CLEAR in front of the
    wall's cladding (its seams' tips), or of the roof's own edge where no wall is picked,
    and runs down to HEAD_DROP below the top of that cladding (or the edge), hemmed.
  * The decompression strips: on the roof under the cap, between the seams, as at a ridge
    (cladding_ridge._fillers); and one behind the drop, against the head of the wall's
    cladding.

The G3 piece is made up to RIDGE_LEG (185 mm) wide. A wall with a deep build-up asks for a
wider cap than that: it is drawn as wide as it has to be and the check says a purpose-made
head flashing is needed.

Whether a chain's slopes get one is its "head" setting: "auto" (the default: where the top
edge is the head of a picked wall), "g3" (every level top edge that is not a ridge) or
"none". The chain's top edge offset does not apply under a head verge.

Not modelled: the ridge clips (fixed and moving parts), the insect mesh, the fixing of the
folded strip, laps between lengths, the cap's ends at a verge."""

import math

from shapely.geometry import LineString
from shapely.geometry.polygon import orient

from cladding_constants import _prism, frame_to_world, frame_v
from cladding_eaves import _joins
from cladding_ridge import (RIDGE_GAP, RIDGE_HEM, RIDGE_LEG, RIDGE_REST, FILLER_EDGE, FILLER_GAP, FILLER_W, _fillers, _slot,
                            _up_slope)
from cladding_verge import ROOF_PITCH

HEAD_BACK = 40.0      # mm – the plumb plane the trays stop short of, inside the line of the cap's drop (scaled off the detail)
HEAD_CLEAR = 20.0     # mm – the drop, in front of the wall's cladding or the roof's edge: room for the decompression strip (assumed)
HEAD_DROP = 60.0      # mm – the drop's foot, below the top of the wall's cladding (the detail's 60 mm)
HEAD_COVER = 150.0    # mm – the cap on the roof, down the slope from the plumb plane (the detail's 80 + 10 + 50 + 10)
HEAD_FILLER = 40.0    # mm – the decompression strip behind the drop, its height (scaled)
HEAD_MIN = 300.0      # mm – a level top edge shorter than this is not a head verge
HEAD_LEVEL = 0.005    # dv/du – a top edge off level by no more than this (1 in 200) is level


def head_setting(elev):
    said = (elev or {}).get("head")
    return said if said in ("g3", "none") else "auto"


def _top_edges(elev):
    """[(u0, u1, v)]: the level top edges of the face as picked, HEAD_MIN long or more."""
    out = []
    for pg in elev.get("polygons") or []:
        ring = pg["exterior"]
        for a, b in zip(ring, ring[1:] + ring[:1]):
            # level to HEAD_LEVEL (a modelled top edge a hair off the face's own level: 35 mm in 9.5 m in a real model)
            if abs(a[1] - b[1]) <= max(0.5, HEAD_LEVEL * abs(a[0] - b[0])) and abs(a[0] - b[0]) >= HEAD_MIN and a[0] > b[0]:   # outlines run anticlockwise: a top edge runs back
                out.append((min(a[0], b[0]), max(a[0], b[0]), max(a[1], b[1])))
    top = max((v for _a, _b, v in out), default=None)
    return sorted(e for e in out if top is not None and abs(e[2] - top) < 1.0)


def heads_of(elev, p):
    """The face's head verges: [{"u0", "u1", "v": the top edge, "over": the wall under it or
    None, "depth": how far out the outside of that wall's cladding is, "top": that wall's
    top offset, "pitch"}]. None on a face that is not a roof slope in trays run with the
    fall, has a ridge, or whose chain says none."""
    pitch = elev.get("pitch")
    said = head_setting(elev)
    if said == "none" or not _up_slope(p) or pitch is None or not 0.5 < float(pitch) <= ROOF_PITCH or elev.get("faces_down"):
        return []
    ridge = elev.get("ridge") or {}
    joins = [j for j in _joins(elev, True, "head")]
    out = []
    for u0, u1, v in _top_edges(elev):
        if ridge.get("with"):
            try:
                on_ridge = abs(float(ridge["v"]) - v) < 2.0 and float(ridge["u0"]) < u1 - 50.0 and float(ridge["u1"]) > u0 + 50.0
            except (KeyError, TypeError, ValueError):
                on_ridge = True
            if on_ridge or said == "g3" and not joins:
                continue                              # the ridge's own edge; or, with a ridge, a free top only where a wall is under it
        # the wall's head on the line the two planes meet on: on the top edge, or under it by as much as the
        # deck may stand proud of the wall (fabric_extract.HEAD_OVERSAIL, up the slope)
        under = 150.0 / max(0.2, math.cos(math.radians(float(pitch))))
        over = next((j for j in joins if -max(2.0, HEAD_LEVEL * (u1 - u0)) < v - j["v"] < under + HEAD_LEVEL * (u1 - u0)
                     and j["u0"] < u1 - 50.0 and j["u1"] > u0 + 50.0), None)
        if over is None and said != "g3":
            continue
        if over is not None:
            u0, u1 = max(u0, over["u0"]), min(u1, over["u1"])
        out.append({"u0": u0, "u1": u1, "v": v, "over": over["with"] if over else None, "pitch": float(pitch),
                    "depth": (over["depth"] + over["seam"]) if over else 0.0, "top": over["top"] if over else 0.0})
    return out


def _plane(h):
    """How far up the slope from the roof's top edge the plumb plane stands: over a wall,
    HEAD_BACK inside the line of the drop; else on the edge."""
    c = math.cos(math.radians(h["pitch"]))
    return max(0.0, h["depth"] + HEAD_CLEAR - HEAD_BACK) / c


def head_ridges(p):
    """Before the ridges are cut: give each face with a head verge a ridge record with no
    partner ("head_ridge", read by cladding_ridge.ridge_of), so its layers are brought to
    the plumb plane as at a ridge. One a face: its longest."""
    for elev in p.get("elevations", []):
        elev.pop("head_ridge", None)
        heads = heads_of(elev, p)
        if not heads:
            continue
        h = max(heads, key=lambda q: q["u1"] - q["u0"])
        elev["head_ridge"] = {"with": None, "v": h["v"] + _plane(h), "u0": h["u0"], "u1": h["u1"],
                              "k": math.tan(math.radians(h["pitch"])), "master": True, "head": h}


def _cap_ring(pitch, rest, out, foot, t):
    """The cap's section, from the point of the roof's plane the plumb plane stands on:
    (level, down the slope positive; up). Its underside *rest* off the roof's face, from
    its hemmed edge HEAD_COVER down the slope up to the corner, *out* beyond the plane on
    plan (the other way), then plumb down to *foot*, hemmed back up behind."""
    s, c = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    d, n = (c, -s), (s, c)                                 # down the slope; out of the face
    at = lambda along: (rest * n[0] + along * d[0], rest * n[1] + along * d[1])       # noqa: E731
    corner_along = (-out - rest * n[0]) / d[0]             # where the cap's line reaches the drop's
    edge, corner = at(HEAD_COVER), at(corner_along)
    under = (edge[0] - 2.0 * t * n[0], edge[1] - 2.0 * t * n[1])
    hem = (under[0] - RIDGE_HEM * d[0], under[1] - RIDGE_HEM * d[1])
    path = [hem, under, edge, corner, (corner[0], foot), (corner[0] + 2.0 * t, foot), (corner[0] + 2.0 * t, foot + RIDGE_HEM)]
    sheet = orient(LineString(path).buffer(t / 2.0, cap_style=2, join_style=2, mitre_limit=3.0), 1.0)
    return [[round(x, 3), round(y, 3)] for x, y in list(sheet.exterior.coords)[:-1]], HEAD_COVER - corner_along


def head_parts(meshes, p, infos):
    """The cap over each head verge, the decompression strips under it and behind its drop.
    Records it on the face's info ("head")."""
    info_by = {i["elevation"]: i for i in infos}
    out = []
    for elev in p.get("elevations", []):
        r = elev.get("head_ridge")
        info = info_by.get(elev.get("name"))
        if not r or "head" not in r or info is None:      # (a head-wall abutment's is cladding_abut's)
            continue
        h, name, frame = r["head"], elev.get("name"), elev["frame"]
        face = float(info.get("total_depth") or 0.0)
        rest = face + float(p["seam_h"]) + RIDGE_REST
        t = float(p["seam_t"])
        s, c = math.sin(math.radians(h["pitch"])), math.cos(math.radians(h["pitch"]))
        back = _plane(h)                                   # the plane, up the slope from the roof's edge
        drop = h["depth"] + HEAD_CLEAR - back * c          # the drop's line, beyond the plane on plan
        head = -back * s - h["top"]                        # the top of the wall's cladding (or the roof's edge), up from the section's origin
        foot = head - HEAD_DROP
        ring, width = _cap_ring(h["pitch"], rest, drop, foot, t)
        up, U = frame_v(frame), frame["u"]
        level = [-up[0] / math.hypot(up[0], up[1]), -up[1] / math.hypot(up[0], up[1]), 0.0]
        along = [level[1], -level[0], 0.0]
        length = h["u1"] - h["u0"]
        start = h["u0"] if sum(a * b for a, b in zip(along, U)) > 0 else h["u1"]
        place = {"origin": frame_to_world(frame, start, r["v"], 0.0), "u": level, "v": [0.0, 0.0, 1.0], "n": along}
        cap = _prism(ring, 0.0, length, place, "ridge_cap", "%s Head Verge Cap" % name, name)
        cap["head_info"] = {"length": round(length, 1), "width": round(width, 1), "drop": HEAD_DROP, "clear": HEAD_CLEAR,
                            "thickness": t, "over": h["over"], "run": round(back, 1), "standard": width <= RIDGE_LEG + 0.5}
        out.append(cap)
        # the strip behind the drop, against the head of the wall's cladding (or the roof's edge)
        a, b = -drop + t, -drop + HEAD_CLEAR - t
        strip = [[a, head - 5.0 - HEAD_FILLER], [b, head - 5.0 - HEAD_FILLER], [b, head - 5.0], [a, head - 5.0]]
        out.append(_prism(strip, 0.0, length, dict(place), "ridge_filler", "%s Head Verge Filler" % name, name))
        # and those on the roof, between the seams: as under a ridge cap
        edge = r["v"] - HEAD_COVER
        upstand = _slot(r) + r["k"] * face
        low = min(edge + FILLER_EDGE, upstand - FILLER_GAP - FILLER_W)
        out += _fillers(p, elev, r, low, meshes, face)
        info["head"] = dict(cap["head_info"], u0=round(h["u0"], 1), u1=round(h["u1"], 1), v=round(h["v"], 1),
                            gap=RIDGE_GAP, clip_room=round(low - edge - 10.0, 1))
    return meshes + out
