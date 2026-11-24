"""CladForge — the eaves: the level bottom edge of a roof slope clad in standing seam trays
that run with the fall.

Drawn after the designer's own sketch of it and detail DL-41 (a ventilated standing seam
wall to roof, no gutter); the sizes are assumed, and the check says so: agree them against
the supplier's eaves detail. The face's bottom edge is the edge of the roof's own surface
(the surface that was picked: the outer edge of the model's plywood or slab).

  * **The plywood runs on**: the deck under the trays is carried EAVES_NOSE down the slope
    past the drip's line, and the trays and their seams run to its end. Their outlines are
    lengthened, so the cutting list has a tray as long as it is on the roof, and the
    turn round the end and the drip are added to it. The battens and the layers under them stay at the edge.
  * **The drip is the trays' own sheet**, the same coil as the roof covering: at the
    plywood's end each tray turns round it in a half circle, **comes back under the
    plywood**, then drops plumb and is hooked back up at its foot (_eaves_rings). Each
    tray has its own length of it, seam to seam, starting where the tray's flat ends
    and marked as its ("of": the IFC has a tray, its seams and its drip as one element);
    each tray at the eaves carries the sheet it takes ("cut_extra"), so the cutting list
    has the tray as long as the coil it is cut from.
  * **The support profile**: what the zinc is dressed over. An angle, one leg fixed up
    under the plywood and one hanging plumb behind the zinc's drop.
  * The drip's line is where the plywood's underside stands over the roof's edge (the
    build-up's end is square to the slope and leans out over it), never less than
    EAVES_CLEAR out from that edge; its foot is EAVES_DROP below the edge.
  * **The seams are folded back at 45 degrees** at this eaves, whatever the chain's bottom
    seam end says.

**An eaves with no overhang**: where the roof comes down to the head of an upright wall that
is clad too (fabric_extract.eaves_link, kind "eaves" among the two faces' "valleys"), the
roof runs on over the wall's cladding: the drip's line stands EAVES_OVER beyond the
outside of that cladding (its seams' tips) and the drop laps it by EAVES_WALL_LAP; and every
layer of the wall stops level under the roof's plywood (eaves_under, wall_stop).

Whether a chain's slopes get one is its "eaves" setting: on, or none — a bottom edge that
runs into a wall, a parapet gutter or another roof is not an eaves and has to be switched
off. Only a level edge at least 300 long is one: a raking bottom edge is a valley.

Not modelled: the gutter and its brackets, the fascia, the insect mesh in the gap over a
wall's cladding, the zinc's laps and its ends at a verge or a hip."""

import math

from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from cladding_constants import _parse, _prism, chain_params, frame_to_world, frame_v
from cladding_edges import settings as edge_settings
from cladding_primitives import buildup_depth
from cladding_ridge import _same_frame, _up_slope
from cladding_valley import valleys_of
from cladding_verge import ROOF_PITCH, VERGE_TRAY

EAVES_NOSE = 40.0     # mm – the plywood, on past the drip's line, down the slope (assumed)
EAVES_TRAY = EAVES_NOSE   # the trays run to the plywood's end, where the zinc turns round it
EAVES_LAP = 0.0       # the drip is the trays' own sheet turned round the plywood's end: nothing lies under them
EAVES_SUPPORT_T = 1.5  # mm – the support profile's metal (assumed)
EAVES_SUPPORT_LEG = 60.0   # mm – its leg under the plywood, up the slope from the drip's line (assumed)
EAVES_HOOK = 10.0     # mm – the zinc hooked back up behind the support's foot (assumed)
EAVES_CLEAR = 10.0    # mm – the apron's drop, clear of the edge of the roof's surface, on plan (assumed)
EAVES_DROP = 50.0     # mm – the drop's foot, below the edge of the roof's surface (assumed)
EAVES_OVER = 20.0     # mm – over a clad wall: the deck's underside out beyond the wall cladding's outside, on plan (assumed)
EAVES_WALL_LAP = 60.0 # mm – over a clad wall: the drop's foot below the top of the wall's cladding (assumed)
EAVES_MIN = 300.0     # mm – a level bottom edge shorter than this is not an eaves
EAVES_RAKE = 0.3      # dv/du – a bottom edge raking up to this (about 17 degrees across the face; a valley's rakes far more) is still an eaves
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
    """[(u0, u1, v0, v1)]: the bottom edges of the clad area at least EAVES_MIN long, with their
    height on the face at each end: level (v0 == v1), or raking gently (EAVES_RAKE, a roof
    whose eaves follows a wall's raked head, as over a canopy's leaning wall)."""
    out = []
    for e in info.get("edges") or []:
        if e["kind"] != "bottom":
            continue
        (a, va), (b, vb) = sorted((tuple(e["p1"][:2]), tuple(e["p2"][:2])))
        if b - a >= EAVES_MIN and abs(vb - va) <= max(0.5, EAVES_RAKE * (b - a)):
            if abs(vb - va) < 0.5:
                va = vb = (va + vb) / 2.0
            out.append((a, b, va, vb))
    return sorted(out)


def apron_drop(soffit, pitch, below=EAVES_DROP):
    """Where the drip's drop hangs: (how far up the slope from the plywood's end the plumb
    line of it meets the plywood's underside; how far out from the edge of the roof's
    surface that is, on plan; how far below that edge its foot is). The plywood runs
    EAVES_NOSE on past the line. The line is where the plywood's underside (*soffit* off
    the face) stands over the edge the roof runs to, and never less than EAVES_CLEAR out
    from that edge; the foot is *below* the edge, and at least 20 mm below the plywood."""
    s, c = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    out = max(soffit * s, EAVES_CLEAR)
    return EAVES_NOSE, out, max(below, 20.0 - soffit * c + (out - soffit * s) * s / c)


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
            other = by.get(v.get("with")) if v.get("kind") in ("eaves", "head", "abut") else None
            if other is None:
                continue
            po = _parse(dict(chain_params(params, other), elevations=[]))
            v.update(other_depth=round(buildup_depth(po), 3), other_soffit=round(soffit_depth(po), 3),
                     other_top=edge_settings(other)["top"], other_trays=_up_slope(po),
                     other_seam=float(po["seam_h"]) if po.get("seam") else 0.0)


EAVES_WALL_REACH = 400.0   # mm – an open eaves this far or less out from a picked wall's face, near its head, stands over it


def _bottom_edges(elev):
    """[(a, b)]: the face's lowest edges, level or raking gently (EAVES_RAKE), as (u, v) ends."""
    out = []
    pts = [q for pg in elev.get("polygons") or [] for q in pg["exterior"]]
    if not pts:
        return out
    low = min(q[1] for q in pts)
    for pg in elev.get("polygons") or []:
        ring = pg["exterior"]
        for a, b in zip(ring, ring[1:] + ring[:1]):
            du, dv = abs(a[0] - b[0]), abs(a[1] - b[1])
            if du >= EAVES_MIN and dv <= max(0.5, EAVES_RAKE * du) and min(a[1], b[1]) <= low + 5.0 + EAVES_RAKE * du:
                out.append(tuple(sorted((tuple(a[:2]), tuple(b[:2])))))
    return out


def eaves_walls(params):
    """Before anything is built: each roof slope is told which picked walls stand under its open
    eaves (not on the line their planes meet on, as eaves_link's are, but under an edge that
    sails out over them, by less than their cladding may stand proud of it): "eaves_walls",
    [{"with", "u0", "u1" (the stretch of the eaves over it, in the roof's u), "proud": how far
    the roof's edge stands out from the wall's face, at its nearest, "depth": how far out the
    wall's cladding is (its seams' tips)}]. The eaves runs on over such a wall (eaves_parts),
    its drip clear of the cladding's outside, whatever the model's roof oversails it by."""
    _fv = frame_v
    elevs = [e for e in params.get("elevations") or [] if e.get("ok", True) and e.get("polygons")]
    dot = lambda a, b: sum(x * y for x, y in zip(a, b))                     # noqa: E731
    walls = []
    for w in elevs:
        n = [float(c) for c in w["frame"]["n"]]
        if abs(n[2]) <= 0.035 or (n[2] < 0 and abs(n[2]) <= 0.5):          # upright, or leaning out (a canopy's)
            po = _parse(dict(chain_params(params, w), elevations=[]))
            top = max(q[1] for pg in w["polygons"] for q in pg["exterior"])
            walls.append((w, n, buildup_depth(po) + (float(po["seam_h"]) if po.get("seam") else 0.0), top))
    for r in elevs:
        r.pop("eaves_walls", None)
        nr = [float(c) for c in r["frame"]["n"]]
        if nr[2] <= 0.5 or r.get("faces_down"):
            continue
        found = []
        linked = {v.get("with") for v in r.get("valleys") or [] if v.get("kind") in ("eaves", "head", "abut")}
        for a, b in _bottom_edges(r):
            P = [frame_to_world(r["frame"], q[0], q[1], 0.0) for q in (a, b)]
            for w, n, depth, top in walls:
                if w.get("name") in linked:
                    continue                                  # on its head: an eaves on a wall proper (eaves_link)
                f = w["frame"]
                d = [dot([p[i] - f["origin"][i] for i in range(3)], n) for p in P]       # out from the wall's face
                hv = [(dot([p[i] - f["origin"][i] for i in range(3)], f["u"]),
                       dot([p[i] - f["origin"][i] for i in range(3)], _fv(f))) for p in P]  # on the wall: (u, height up it)
                if not all(-60.0 <= x <= EAVES_WALL_REACH for x in d) or min(d) >= depth:
                    continue                                  # not over it, or clear of its cladding already
                if not all(top - 600.0 <= y <= top + 1500.0 for _x, y in hv):
                    continue                                  # not at its head
                # the stretch of the eaves over the wall, back in the roof's u
                wa, wb = hv[0][0], hv[1][0]
                if abs(wb - wa) < 1.0:
                    continue
                ts = sorted(((x - wa) / (wb - wa) for x in (-50.0, float(w["width"]) + 50.0)))
                t0, t1 = max(0.0, ts[0]), min(1.0, ts[1])
                u0, u1 = a[0] + (b[0] - a[0]) * t0, a[0] + (b[0] - a[0]) * t1
                if u1 - u0 < EAVES_MIN:
                    continue
                found.append({"with": w.get("name"), "u0": round(u0, 1), "u1": round(u1, 1), "proud": round(min(d), 1),
                              "depth": round(depth, 1)})
        if found:
            r["eaves_walls"] = found


def _joins(elev, roof, kind="eaves"):
    """The face's eaves on a wall, as cladding_valley.valleys_of gives its junctions, each
    with what join_eaves noted: those where it is the roof (*roof* True) or the wall. The
    line is level: "v" its height on the face, "u0", "u1" its ends."""
    raw = {v.get("with"): v for v in elev.get("valleys") or [] if v.get("kind") == kind}
    out = []
    for j in valleys_of(elev, kind):
        v = raw.get(j["with"]) or {}
        # level to 1 in 200 (a modelled line a hair off the face's own level: 35 mm in 9.5 m in a real model)
        if bool(v.get("roof")) != roof or "other_depth" not in v or abs(j["p1"][1] - j["p2"][1]) > max(2.0, 0.005 * abs(j["p1"][0] - j["p2"][0])):
            continue
        u0, u1 = sorted((j["p1"][0], j["p2"][0]))
        out.append(dict(j, u0=u0, u1=u1, v=(j["p1"][1] + j["p2"][1]) / 2.0, depth=float(v["other_depth"]),
                        soffit=float(v["other_soffit"]), top=float(v["other_top"]), trays=bool(v.get("other_trays")),
                        seam=float(v.get("other_seam") or 0.0),
                        other_pitch=math.degrees(math.acos(max(0.0, min(1.0, abs(float(j["other_n"][2])))))),
                        ))
    for q in out:
        q["mansard"] = roof and q["other_pitch"] < 89.0                # the face below the roof leans back under it
    return out


def wall_stop(depth, soffit, top, pitch, lean=90.0):
    """Where a wall's cladding stops under a roof that comes down to its head, as a height
    from the line the two meet on (negative: below it), up the wall's face. *top* (the
    wall's top edge offset: the ventilation gap) below the roof's soffit **at the outside
    surface of the wall's cladding**, *depth* out from the wall (the tips of a standing
    seam's seams), where a soffit that falls outward is lowest:
    every layer of the wall is cut back to that, square to its face. The gap is plumb, as it
    is on an upright wall. Never above the line itself, where the roof's own layers end. *pitch*: the roof's, in degrees; *lean*: the
    wall's (90 upright; less, a mansard's lower slope, leaning back under the roof)."""
    gap = math.radians(lean - pitch)                       # the angle the two faces make, less 90
    meet = (soffit - depth * math.cos(gap)) / math.sin(gap)    # where the cladding's outside meets the soffit, up the wall
    # *top* clear under the soffit, plumb: on a wall that leans back that is further down its face
    return min(-top, meet - top * math.cos(math.radians(pitch)) / math.sin(gap))


def over_wall(depth, soffit, top, pitch, lean=90.0, sheet=None):
    """An eaves run on over a clad wall (or a mansard's lower slope) below it: (how far down
    the slope the roof runs past the line the two meet on, how far below the roof's moved
    edge the drop's foot hangs). *depth*: the outside of the wall's cladding (its seams'
    tips); *sheet*: the outside of its sheets or boards, if less. The foot laps the top of
    those by EAVES_WALL_LAP, plumb, and stands EAVES_OVER clear of the cladding's outside
    there (a wall that leans back is furthest out at the foot)."""
    sheet = depth if sheet is None else sheet
    s, c = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    sl, cl = math.sin(math.radians(lean)), math.cos(math.radians(lean))
    stop = wall_stop(depth, soffit, top, pitch, lean)         # up the wall's face, at its cladding's outside
    y_foot = sheet * cl + stop * sl - EAVES_WALL_LAP          # x out from the line on plan, y up from it
    x_foot = depth * sl - (y_foot - depth * cl) / sl * cl
    run = max(0.0, (x_foot + EAVES_OVER - soffit * s) / c)
    return run, -run * s - y_foot


def _eaves_rings(tray, soffit, t, pitch, below=EAVES_DROP):
    roll, drop, support, length = eaves_paths(tray, soffit, t, pitch, below)
    zinc = LineString(roll + drop[1:]).buffer(t / 2.0, cap_style=2, join_style=2, mitre_limit=3.0)
    return list(zinc.exterior.coords)[:-1], support, length


def eaves_paths(tray, soffit, t, pitch, below=EAVES_DROP):
    """The eaves in section, as (the zinc's ring, the support profile's ring, the zinc's
    developed length): x out from the edge the roof runs to, down the slope, y the depth
    from the face.

    The plywood (its underside *soffit* off the face) runs EAVES_NOSE on past the drip's
    line, under the trays (their backs *tray* off the face). **The zinc** is the trays'
    own sheet carried on: from where their flat ends at the plywood's end it turns
    round it in a half circle, **comes back under the plywood**, then
    drops plumb and is hooked back under at its foot: the drip. **The support profile** is
    what it is dressed over: an angle, one leg fixed up under the plywood and one hanging
    plumb, the zinc lying on the outside of that and hooked round its foot. *pitch*: the
    slope, in degrees from level.

    eaves_paths gives the zinc as its two runs, (the bullnose: from the tray's flat round the
    plywood's end and back under it to the drop's line; the drop: from there down and
    hooked), the support's ring and the zinc's developed length; _eaves_rings, the zinc's
    ring whole."""
    s, c = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    nose, out, below = apron_drop(soffit, pitch, below)
    down, away = (s, -c), (c, s)                           # plumb down, and level out from the building, as (x, y)
    ts = EAVES_SUPPORT_T
    q = ((out - soffit * s) / c, soffit)                   # where the drop's line meets the plywood's underside
    end = q[0] + nose                                      # the plywood's end
    fall = soffit * c - q[0] * s + below                   # from there down to the foot
    # the support: under the plywood from near its end to EAVES_SUPPORT_LEG up the slope past the line, and the leg down
    leg = [(q[0] + away[0] * ts / 2.0, q[1] - ts / 2.0), (q[0] + away[0] * ts / 2.0 + down[0] * fall, q[1] - ts / 2.0 + down[1] * fall)]
    flange = [(end - 5.0, soffit - ts / 2.0), (q[0] - EAVES_SUPPORT_LEG, soffit - ts / 2.0)]
    support = unary_union([LineString(leg).buffer(ts / 2.0, cap_style=2), LineString(flange).buffer(ts / 2.0, cap_style=2)])
    # the zinc
    yt, yb = tray + t / 2.0, soffit - ts - t               # the tray's own sheet, and under the support's flange
    r, yc = (yt - yb) / 2.0, (yt + yb) / 2.0
    arc = [(end + r * math.cos(math.radians(a)), yc + r * math.sin(math.radians(a))) for a in range(75, -76, -15)]
    off = ts + t                                           # the drop: on the outside of the support's leg
    line = (q[0] + away[0] * off, q[1] + away[1] * off)
    k = (line[1] - yb) / c                                 # down the line to the level of the run under the plywood
    under = (line[0] + down[0] * k, yb) if k > 0 else (line[0], yb)
    foot = (line[0] + down[0] * (fall + off * s / c + 2.0 * t), line[1] + down[1] * (fall + off * s / c + 2.0 * t))
    hook = (foot[0] - away[0] * (ts + 3.0 * t), foot[1] - away[1] * (ts + 3.0 * t))
    roll = [(end, yt)] + arc + [(end, yb), under]
    drop = [under, foot, hook, (hook[0] - down[0] * EAVES_HOOK, hook[1] - down[1] * EAVES_HOOK)]
    ring = lambda g: list(g.exterior.coords)[:-1]          # noqa: E731
    return (roll, drop, ring(support if support.geom_type == "Polygon" else max(support.geoms, key=lambda g: g.area)),
            LineString(roll + drop[1:]).length)


def _fold_back(m, at):
    """Fold a seam's end back at 45 degrees where it runs out at the eaves: its end slopes
    from the tray up to its full height over the seam's height (cladding_edges.finish_seams
    does the same where the chain says folded; at this eaves it is always done). *at*: which
    of its outline's corners are its end."""
    if not (m.get("seam_part") and m.get("seam_v")) or not any(at):
        return
    rows = (m.get("vshift") or [[[0.0] * 4 for _ in m["profile"]]])[0]
    if any(abs(rows[i][3]) > 1e-9 for i, on in enumerate(at) if on):
        return                                             # folded already
    face = float(m.get("seam_face") or 0.0)
    rows = [[sh[0], sh[1], sh[2] - face, sh[3] + 1.0] if on else list(sh) for sh, on in zip(rows, at)]
    m["vshift"] = [rows] + [[[0.0] * 4 for _ in h] for h in (m.get("holes") or [])]


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
        # a tray widened over a verge strip is still at the eaves (and over a verge carried out
        # over a wall's cladding, by that much more)
        reach = VERGE_TRAY + 5.0 + max([float(g.get("over") or 0.0) for g in info.get("verges") or []] + [0.0])
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
        for u0, u1, v0, v1 in _edges(info):
            # the edge's height at u, on the face: one level for a level eaves, rising along a raking one
            rake = (v1 - v0) / (u1 - u0)
            vat = lambda u, v0=v0, u0=u0, rake=rake: v0 + rake * (u - u0)        # noqa: E731
            v = (v0 + v1) / 2.0
            stretch = math.sqrt(1.0 + rake * rake)
            # Where the edge is the head of a clad wall the roof comes down to (no overhang),
            # the roof runs on over the wall's cladding; the rest of it is an edge in the open.
            cuts = sorted({u0, u1} | {u for q in joins if abs(q["v"] - v) < 2.0 for u in (q["u0"], q["u1"]) if u0 + 1.0 < u < u1 - 1.0})
            for a, b in zip(cuts, cuts[1:]):
                over = next((q for q in joins if abs(q["v"] - v) < 2.0 and q["u0"] <= (a + b) / 2.0 <= q["u1"]), None)
                run, below = 0.0, EAVES_DROP
                s, c = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
                under = None if over else next((w for w in elev.get("eaves_walls") or []
                                                if w["u0"] - 1.0 <= (a + b) / 2.0 <= w["u1"] + 1.0), None)
                if over:
                    # the drip's line EAVES_OVER beyond the outside of the wall's cladding (its seams' tips), on plan
                    run, below = over_wall(over["depth"] + over["seam"], soffit, over["top"], pitch, over["other_pitch"], over["depth"])
                nose, drop_out, below = apron_drop(soffit, pitch, below)
                if under:
                    # an open eaves over a wall whose cladding stands proud of the roof's edge: run on until the drop
                    # stands EAVES_OVER clear of the cladding's outside, on plan, and laps down over its head
                    need = under["depth"] - under["proud"] + EAVES_OVER
                    if need > drop_out:
                        run = (need - drop_out) / c
                        below = max(below, EAVES_WALL_LAP + run * s)
                ahead = run + (drop_out - soffit * s) / c + nose        # the plywood's end, and the trays', below the edge
                ve, end = vat(a) - run, vat(a) - ahead                  # at the length's start
                turned = []
                for m in mine:
                    kind = m.get("ifc_type")
                    pts = m["profile"]
                    # (past an end of a raking eaves, over a verge strip, the edge may run on at the
                    # end's level as well as on the line: either is the eaves)
                    at = [u0 - reach <= q[0] <= u1 + reach
                          and min(vat(q[0]), vat(min(max(q[0], u0), u1))) - 1.0 <= q[1] <= max(vat(q[0]), vat(min(max(q[0], u0), u1))) + tol
                          for q in pts]
                    mid = [q[0] for q, on in zip(pts, at) if on]
                    if not mid or not (a - (reach if a == u0 else 0.0) <= sum(mid) / len(mid) <= b + (reach if b == u1 else 0.0)):
                        continue
                    for k, q in enumerate(pts):
                        if at[k]:
                            out = vat(q[0]) - ahead if kind in ("plank", "seam", "deck") else vat(q[0])
                            _lower(q, [pts[x] for x in ((k - 1) % len(pts), (k + 1) % len(pts)) if not at[x]], out)
                    if kind == "seam":
                        _fold_back(m, at)                  # at this eaves the seams are always folded back
                    elif kind == "plank":
                        turned.append((min(mid), max(mid), m))   # its sheet goes on round the plywood's end and into the drip
                ring = lambda pts: [[round(x, 3), round(y, 3)] for x, y in                   # noqa: E731
                                    list(orient(Polygon([(x, sign * y) for x, y in pts]), 1.0).exterior.coords)[:-1]]
                # along the edge: level, the face's own u; raking, up it as the edge rises (its length so much longer)
                run_dir = [(along[i] + rake * float(V[i])) / stretch for i in range(3)]
                place = {"origin": frame_to_world(frame, a, ve, 0.0), "u": toward, "v": across, "n": run_dir}
                zinc, support, sheet = _eaves_rings(back, soffit, t, pitch, below)
                runs = [ring(list(LineString(path).buffer(t / 2.0, cap_style=2, join_style=2, mitre_limit=3.0).exterior.coords)[:-1])
                        for path in eaves_paths(back, soffit, t, pitch, below)[:2]]
                n += 1
                prop = _prism(ring(support), 0.0, (b - a) * stretch, dict(place), "eaves_strip", "%s Eaves Support %d" % (name, n), name)
                prop.update(eaves_support=True, color="#eab308")
                extra.append(prop)
                # The drip is each tray's own sheet: one length of it per tray, from seam to seam, and part of that tray
                # ("of": the IFC has the tray, its seams and its drip as one element).
                turned.sort(key=lambda q: q[0])
                marks = [a] + [(p0[1] + p1[0]) / 2.0 for p0, p1 in zip(turned, turned[1:])] + [b]
                drips = []
                for (m0, m1), (_lo, _hi, m) in zip(zip(marks, marks[1:]), turned):
                    m["cut_extra"] = round(sheet, 1)       # the cutting list: the tray is cut this much longer
                    if m1 - m0 < 0.5:
                        continue
                    drip = _prism(ring(zinc), 0.0, (m1 - m0) * stretch, dict(place, origin=frame_to_world(frame, m0, vat(m0) - run, 0.0)),
                                  "eaves_strip", "%s Eaves Drip" % m["name"], name)
                    drip.update(tray_sheet=True, of=m["name"], color=m.get("color", drip["color"]), drip_runs=runs,
                                drip_under=[round(eaves_paths(back, soffit, t, pitch, below)[1][0][0], 3),
                                            round(sign * eaves_paths(back, soffit, t, pitch, below)[1][0][1], 3)])
                    drips.append(drip)
                if not drips:                              # no tray reaches this length of the eaves: nothing to turn
                    extra.pop()
                    n -= 1
                    continue
                apron = drips[0]
                extra.extend(drips[1:])
                apron["eaves_info"] = {"length": round((b - a) * stretch, 1), "lap": EAVES_LAP, "nose": nose, "drop": round(below, 1),
                                       "clear": round(drop_out, 1), "tray": EAVES_TRAY, "thickness": t, "sheet": round(sheet, 1),
                                       "over": over["with"] if over else None, "run": round(run, 1),
                                       "mansard": bool(over and over["mansard"]), "over_wall": under["with"] if under else None}
                extra.append(apron)
                for d in drips[1:]:
                    d["eaves_info"] = apron["eaves_info"]
                info.setdefault("eaves", []).append(dict(apron["eaves_info"], u0=round(a, 1), u1=round(b, 1), v=round(v, 1)))
                eb = vat(b) - ahead
                info.setdefault("eaves_strips", []).append([[a, end - t], [b, eb - t], [b, eb + 2.0], [a, end + 2.0]])
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
            lean = min(90.0, float(elev.get("pitch") or 90.0))
            stop = j["v"] + wall_stop(depth, j["soffit"], top, pitch, lean)
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
                                                        "gap": top, "pitch": round(pitch, 1), "mansard": lean < 89.0})
    return [m for m in meshes if id(m) not in gone]
