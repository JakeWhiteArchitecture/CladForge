"""CladForge — the eaves: the level bottom edge of a roof slope clad in standing seam trays
that run with the fall.

No drawing of the eaves was supplied with the others (ridge, verge, valley), so this is the
usual standing seam eaves in the same manner as cladding_verge's strip, and every size
here is assumed: agree them against the supplier's eaves detail. The face's bottom edge is
taken as the outer face of the roof's edge (the fascia, or the back of the gutter).

  * The eaves apron: a folded sheet lying on the deck under the trays, EAVES_LAP up the
    slope from the edge, its nose EAVES_NOSE out beyond it, turned back under, then down:
    the drop hangs **plumb in front of the edge of the roof's own surface** (the surface
    that was picked: the outer edge of the model's plywood), EAVES_CLEAR clear of it, and
    runs down past that edge by EAVES_DROP, with a drip kicked out at its foot. The
    build-up's end is cut square to the slope, so on a pitch it leans out over the edge:
    the drop stands as much further out as it takes to pass it (apron_drop).
  * The trays, and their seams, run out over the apron to EAVES_TRAY beyond the edge, where
    each is welted round the apron's nose. Their outlines are lengthened, so the cutting
    list has a tray as long as it is on the roof; the welt turned under is not added. A
    seam ends there as the chain's bottom seam end says (folded, or flat).
  * Everything under the trays (deck, battens) runs to the roof's edge. The chain's bottom
    edge offset does not apply at an eaves.

Whether a chain's slopes get one is its "eaves" setting: on, or none — a bottom edge that
runs into a wall, a parapet gutter or another roof is not an eaves and has to be switched
off. Only a level edge at least 300 long is one: a raking bottom edge is a valley.

Not modelled: the gutter and its brackets, the fascia, the continuous clip some systems
fix under the apron, the tray's welt, the apron's laps and its ends at a verge or a hip."""

import math

from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient

from cladding_constants import _parse, _prism, chain_params, frame_to_world, frame_v
from cladding_edges import settings as edge_settings
from cladding_primitives import buildup_depth
from cladding_ridge import _same_frame, _up_slope
from cladding_valley import valleys_of
from cladding_verge import ROOF_PITCH, VERGE_TRAY

EAVES_NOSE = 35.0     # mm – the apron's nose, out beyond the roof's edge (assumed)
EAVES_TRAY = 40.0     # mm – the trays' ends beyond the roof's edge, round the nose (assumed)
EAVES_LAP = 100.0     # mm – the apron on the deck, up from the edge (assumed)
EAVES_CLEAR = 10.0    # mm – the apron's drop, clear of the edge of the roof's surface, on plan (assumed)
EAVES_DROP = 50.0     # mm – the drop's foot, below the edge of the roof's surface (assumed)
EAVES_KICK = 10.0     # mm – the drip kicked out at its foot (assumed)
EAVES_WELT = 25.0     # mm – the trays' ends turned back under the apron's nose (assumed)
EAVES_OVER = 20.0     # mm – over a clad wall: the deck's underside out beyond the wall cladding's outside, on plan (assumed)
EAVES_WALL_LAP = 60.0 # mm – over a clad wall: the drop's foot below the top of the wall's cladding (assumed)
EAVES_MIN = 300.0     # mm – a level bottom edge shorter than this is not an eaves
EDGE_TOL = 12.0       # mm – an end this close to where the bottom offset left it is an end at the eaves
OWN = ("ridge_cap", "ridge_filler", "verge_strip", "valley_gutter", "eaves_strip")


def has_eaves(elev, p):
    """Does this face take an eaves apron: a roof slope that looks up, clad in trays that
    run with the fall, its chain's eaves not switched off."""
    pitch = elev.get("pitch")
    if not _up_slope(p) or pitch is None or float(pitch) > ROOF_PITCH or elev.get("faces_down"):
        return False
    return (elev.get("eaves") or "on") != "none"


def _edges(info):
    """[(u0, u1, v)]: the level bottom edges of the clad area, those at least EAVES_MIN long."""
    out = []
    for e in info.get("edges") or []:
        if e["kind"] == "bottom" and abs(e["p1"][1] - e["p2"][1]) < 0.5:
            u0, u1 = sorted((e["p1"][0], e["p2"][0]))
            if u1 - u0 >= EAVES_MIN:
                out.append((u0, u1, (e["p1"][1] + e["p2"][1]) / 2.0))
    return sorted(out)


def apron_drop(y0, t, pitch, below=EAVES_DROP, deck=18.0):
    """Where the apron's drop hangs: (how far out from the edge of the roof's surface, on
    plan; how far below that edge its foot is). The apron wraps over the end of the
    plywood it lies on (*deck* thick, its top *y0* off the face) and the drop falls plumb
    from under that end, so it is as far out as the plywood's underside is there (the end
    is cut square to the slope and leans out over the edge), and never less than
    EAVES_CLEAR; its foot is *below* the edge, and at least 20 mm below where it starts."""
    s, c = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    under = y0 - deck - t                                   # just under the plywood's end
    out = max(t * c + under * s, EAVES_CLEAR + t / 2.0)
    return out, max(below, 20.0 - (under * c - t * s))


def soffit_depth(p):
    """How far off its face a build-up's soffit is: the underside of what sails over a wall
    below. A standing seam roof's deck (its plywood), on the battens; otherwise the back of
    the boards. The battens under it run with the fall and the cavity between them is open."""
    board = p["panel_t"] if p["cladding_type"] == "panel" else p["plank_t"]
    return buildup_depth(p) - board - ((p["deck_t"] + 2.0) if p.get("seam") else 0.0)


def join_eaves(params):
    """Before anything is built: tell each face at an eaves on a wall (fabric_extract.
    eaves_link, kind "eaves" among its "valleys") what it needs to know of the other, which
    may be in another chain with other settings: "other_depth" its build-up's depth,
    "other_soffit" (soffit_depth), "other_top" its top edge offset, "other_trays" whether
    it is trays run with the fall, "other_seam" how far its seams stand off its sheets."""
    by = {e.get("name"): e for e in params.get("elevations") or []}
    for e in params.get("elevations") or []:
        for v in e.get("valleys") or []:
            other = by.get(v.get("with")) if v.get("kind") == "eaves" else None
            if other is None:
                continue
            po = _parse(dict(chain_params(params, other), elevations=[]))
            v.update(other_depth=round(buildup_depth(po), 3), other_soffit=round(soffit_depth(po), 3),
                     other_top=edge_settings(other)["top"], other_trays=_up_slope(po),
                     other_seam=float(po["seam_h"]) if po.get("seam") else 0.0)


def _joins(elev, roof):
    """The face's eaves on a wall, as cladding_valley.valleys_of gives its junctions, each
    with what join_eaves noted: those where it is the roof (*roof* True) or the wall. The
    line is level: "v" its height on the face, "u0", "u1" its ends."""
    raw = {v.get("with"): v for v in elev.get("valleys") or [] if v.get("kind") == "eaves"}
    out = []
    for j in valleys_of(elev, "eaves"):
        v = raw.get(j["with"]) or {}
        if bool(v.get("roof")) != roof or "other_depth" not in v or abs(j["p1"][1] - j["p2"][1]) > 2.0:
            continue
        u0, u1 = sorted((j["p1"][0], j["p2"][0]))
        out.append(dict(j, u0=u0, u1=u1, v=(j["p1"][1] + j["p2"][1]) / 2.0, depth=float(v["other_depth"]),
                        soffit=float(v["other_soffit"]), top=float(v["other_top"]), trays=bool(v.get("other_trays")),
                        seam=float(v.get("other_seam") or 0.0)))
    return out


def wall_stop(depth, soffit, top, pitch):
    """Where a wall's cladding stops under a roof that comes down to its head, as a height
    from the line the two meet on (negative: below it). *top* (the wall's top edge offset:
    the ventilation gap) below the roof's soffit **at the outside surface of the wall's
    cladding**, *depth* out from the wall (the tips of a standing seam's seams), where a
    soffit that falls outward is lowest:
    every layer of the wall is cut back level to that. Never above the line itself, where
    the roof's own layers end."""
    t, c = math.tan(math.radians(pitch)), math.cos(math.radians(pitch))
    return min(0.0, soffit / c - depth * t) - top


def _apron_ring(y0, t, pitch, below=EAVES_DROP, deck=18.0):
    """The apron's section: x out from the roof's edge down the slope, y the depth from the
    face. On the plywood at depth y0, out to the nose and back under it; then it **wraps
    over the end of the plywood**, down its end face to its underside, and from under
    there the drop falls plumb (apron_drop) to its foot, where the drip is kicked out.
    *pitch*: the slope, in degrees from level."""
    g = 2.5 * t
    s, c = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    out, below = apron_drop(y0, t, pitch, below, deck)
    at = lambda h, z: (h * c - z * s, h * s + z * c)        # noqa: E731 — (out on plan, up) from the edge, as (x, y)
    under = y0 - deck - t
    start = (t * c + under * s, under * c - t * s)          # the foot of the wrap, as (out on plan, up)
    path = [(-EAVES_LAP, y0), (EAVES_NOSE, y0), (EAVES_NOSE, y0 - g), (t, y0 - g), (t, under)]
    if out > start[0] + 0.5:
        path.append(at(out, start[1]))                      # (a shallow roof: out to where the drop clears the edge)
    path += [at(out, -below), at(out + EAVES_KICK, -below - EAVES_KICK)]
    return list(LineString(path).buffer(t / 2.0, cap_style=2, join_style=2, mitre_limit=3.0).exterior.coords)[:-1]


def _welt_ring(y_tray, y0, t):
    """The trays' ends welted round the apron's nose, in the apron's section: on out past
    the nose, turned down round it and back under it for EAVES_WELT. That turn is the drip
    at the roof's edge. *y_tray*: the middle of the trays' sheet; *y0*: the apron's top."""
    under = y0 - 2.5 * t - 1.5 * t
    path = [(EAVES_TRAY - 1.0, y_tray), (EAVES_TRAY + t, y_tray), (EAVES_TRAY + t, under), (EAVES_TRAY - EAVES_WELT, under)]
    return list(LineString(path).buffer(t / 2.0, cap_style=2, join_style=2, mitre_limit=3.0).exterior.coords)[:-1]


def _lower(q, others, v):
    """Bring a bottom vertex to the level *v*, sliding it along its other edge: straight
    down a tray's side, and on down the rake of an end cut to a hip or a valley."""
    for r in others:
        du, dv = q[0] - r[0], q[1] - r[1]
        if dv < -1.0 and abs(du) < 3.0 * abs(dv):          # an edge coming down to it, not too flat
            q[0] = round(q[0] + du / dv * (v - q[1]), 3)
            break
    q[1] = round(v, 3)


def eaves_parts(meshes, p, infos):
    """Run the trays out at each eaves, the layers under them to the edge, and add the
    apron. Records each face's eaves on its info ("eaves", "eaves_strips")."""
    info_by = {i["elevation"]: i for i in infos}
    extra = []
    for elev in p.get("elevations", []):
        name, frame, info = elev.get("name"), elev["frame"], info_by.get(elev.get("name"))
        if info is None or not has_eaves(elev, p):
            continue
        mine = [m for m in meshes if m.get("elevation") == name and m.get("ifc_type") not in OWN
                and _same_frame(m["frame"], frame) and not (m.get("hip_part") or m.get("ridge_part"))]   # not a turned-up end
        trays = [m for m in mine if m.get("ifc_type") == "plank"]
        if not trays:
            continue
        back, t = min(float(m["depth"]) for m in trays), float(p["seam_t"])
        tol = edge_settings(elev)["bottom"] + EDGE_TOL
        reach = VERGE_TRAY + 5.0            # a tray widened over a verge strip is still at the eaves
        V, U, N = frame_v(frame), frame["u"], frame["n"]
        toward = [-float(c) for c in V]                                    # out over the edge: down the slope
        along = [float(c) for c in U]
        across = [along[1] * toward[2] - along[2] * toward[1], along[2] * toward[0] - along[0] * toward[2],
                  along[0] * toward[1] - along[1] * toward[0]]
        sign = 1.0 if sum(a * b for a, b in zip(across, N)) > 0 else -1.0     # which way the section's y runs
        pitch = min(float(elev.get("pitch") or 0.0), 80.0)
        decks = [float(m["depth"]) for m in mine if m.get("ifc_type") == "deck"]
        soffit = min(decks) if decks else back
        joins = _joins(elev, True)
        n = 0
        for u0, u1, v in _edges(info):
            # Where the edge is the head of a clad wall the roof comes down to (no overhang),
            # the roof runs on over the wall's cladding; the rest of it is an edge in the open.
            cuts = sorted({u0, u1} | {u for q in joins if abs(q["v"] - v) < 2.0 for u in (q["u0"], q["u1"]) if u0 + 1.0 < u < u1 - 1.0})
            for a, b in zip(cuts, cuts[1:]):
                over = next((q for q in joins if abs(q["v"] - v) < 2.0 and q["u0"] <= (a + b) / 2.0 <= q["u1"]), None)
                run, below = 0.0, EAVES_DROP
                if over:
                    # the deck's underside out to EAVES_OVER beyond the outside of the wall's cladding (its seams' tips), on plan
                    s, c = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
                    run = max(0.0, (over["depth"] + over["seam"] + EAVES_OVER - soffit * s) / c)
                    below = EAVES_WALL_LAP - wall_stop(over["depth"] + over["seam"], soffit, over["top"], pitch) - run * s
                ve = v - run
                for m in mine:
                    kind = m.get("ifc_type")
                    out = ve - EAVES_TRAY if kind in ("plank", "seam") else ve if kind == "deck" else v
                    pts = m["profile"]
                    at = [u0 - reach <= q[0] <= u1 + reach and v - 1.0 <= q[1] <= v + tol for q in pts]
                    mid = [q[0] for q, on in zip(pts, at) if on]
                    if not mid or not (a - (reach if a == u0 else 0.0) <= sum(mid) / len(mid) <= b + (reach if b == u1 else 0.0)):
                        continue
                    for k, q in enumerate(pts):
                        if at[k]:
                            _lower(q, [pts[x] for x in ((k - 1) % len(pts), (k + 1) % len(pts)) if not at[x]], out)
                y0, deck_t = back - 1.0, max(1.0, back - 1.0 - soffit)
                ring = lambda pts: [[round(x, 3), round(y, 3)] for x, y in                   # noqa: E731
                                    list(orient(Polygon([(x, sign * y) for x, y in pts]), 1.0).exterior.coords)[:-1]]
                place = {"origin": frame_to_world(frame, a, ve, 0.0), "u": toward, "v": across, "n": along}
                n += 1
                apron = _prism(ring(_apron_ring(y0, t, pitch, below, deck_t)), 0.0, b - a, place,
                               "eaves_strip", "%s Eaves Apron %d" % (name, n), name)
                welt = _prism(ring(_welt_ring(back + t / 2.0, y0, t)), 0.0, b - a, dict(place),
                              "eaves_strip", "%s Eaves Welt %d" % (name, n), name)
                welt["eaves_welt"] = True
                extra.append(welt)
                out, below = apron_drop(y0, t, pitch, below, deck_t)
                apron["eaves_info"] = {"length": round(b - a, 1), "lap": EAVES_LAP, "nose": EAVES_NOSE, "drop": round(below, 1),
                                       "clear": round(out - t / 2.0, 1), "tray": EAVES_TRAY, "thickness": t,
                                       "over": over["with"] if over else None, "run": round(run, 1)}
                extra.append(apron)
                info.setdefault("eaves", []).append(dict(apron["eaves_info"], u0=round(a, 1), u1=round(b, 1), v=round(v, 1)))
                info.setdefault("eaves_strips", []).append([[a, ve - EAVES_NOSE], [b, ve - EAVES_NOSE], [b, ve + EAVES_LAP], [a, ve + EAVES_LAP]])
    return meshes + extra


def eaves_under(meshes, p, infos):
    """A wall a roof comes down to the head of, with no overhang: stop every layer of the
    wall under the roof's soffit (wall_stop), level. Records it on the face's info
    ("eaves_under"). A part that would be left with nothing is left out."""
    info_by = {i["elevation"]: i for i in infos}
    gone = set()
    for elev in p.get("elevations", []):
        name, frame, info = elev.get("name"), elev.get("frame"), info_by.get(elev.get("name"))
        joins = _joins(elev, False) if info is not None else []
        if not joins:
            continue
        # the outside of the cladding: a standing seam's seams stand off its sheets
        depth = float(info.get("total_depth") or 0.0) + (float(p["seam_h"]) if p.get("seam") else 0.0)
        top = edge_settings(elev)["top"]
        mine = [m for m in meshes if m.get("elevation") == name and _same_frame(m["frame"], frame)]
        for j in joins:
            nz = max(0.05, min(1.0, abs(j["other_n"][2])))
            pitch = math.degrees(math.acos(nz))
            stop = j["v"] + wall_stop(depth, j["soffit"], top, pitch)
            for m in mine:
                pts = m["profile"]
                at = [j["u0"] - 60.0 <= q[0] <= j["u1"] + 60.0 and q[1] > stop + 0.01 for q in pts]
                if not any(at):
                    continue
                if min(q[1] for q in pts) >= stop - 1.0:
                    gone.add(id(m))                    # all of it stands above where the wall stops
                    continue
                for q, on in zip(pts, at):
                    if on:
                        q[1] = round(stop, 3)
            info.setdefault("eaves_under", []).append({"with": j["with"], "length": round(j["length"], 1), "stop": round(stop - j["v"], 1),
                                                        "gap": top, "pitch": round(pitch, 1)})
    return [m for m in meshes if id(m) not in gone]
