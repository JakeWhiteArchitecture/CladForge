"""CladForge — the head-wall abutment: the level top edge of a roof slope clad in standing
seam trays that run with the fall, where it runs up to a wall that stands on above it (a
lean-to against a higher wall).

Two of VMZINC's standard details, chosen by what the wall above is clad in:

**"G3 head verge wall abutment" (VMZ-SN-MA-NO-G3-03)**: the wall is not picked for cladding,
or is clad in boards or panels. Half a ridge against the wall:

  * The trays stop RIDGE_GAP / 2 short of the wall's face and are turned up there, the deck
    and battens cut square behind them, exactly as at a ridge (cladding_ridge.ridge_ends,
    given a ridge record with no partner: "head_ridge").
  * The flashing, the G3 ridge piece: it lies over the seams as a ridge cap's leg does,
    HEAD_COVER down the slope, its edge hemmed back under; it runs up the slope's line to
    the wall and turns up it ABUT_UP. Against a wall that is not picked its top is turned
    into a chase (the detail's fixing wedge and mastic); behind a picked wall's cladding
    it stops plain, and every layer of that wall stops ABUT_LAP below its top.
  * The decompression strips on the roof under it, between the seams, as at a ridge.

**"Head wall abutment below cladding" (VMZ-SN-MA-KN-03)**: the wall above is clad in
standing seam too.

  * The trays run up to the wall and **turn up it**, each tray's own sheet, ABUT_UPSTAND
    above the tray, its top welted (ABUT_HOOK) for the sheet clips; behind it the
    discontinuous plywood (ABUT_PLY) packs it off the wall. Each tray is cut that much
    longer and the cutting list has it so.
  * A saddle piece over the end of every seam, against the upstand.
  * **The wall's cladding laps the upstand** by ABUT_COVER: every layer of the wall stops
    level, ABUT_UPSTAND − ABUT_COVER above the roof's trays, and a continuous edge flashing
    stands on the wall's plywood at the foot of its trays. The roof's cavity vents up
    behind the upstand into the wall's.

**Mitred** (no standard sheet: the designer's own detail): the wall above is standing seam
in upright trays, and the whole build-up turns the corner as at a mitred chain corner. Every
layer of both faces (the plywood deck and the battens as well as the trays and their seams)
is cut on one plane, the mitre: through the line the two faces meet on and the corner the
two claddings' outer faces meet in. They meet on it with no gap, the zinc to be welded. The
seams have to line up, so the wall's seams follow the roof's (cladding_preview.link_seams)
unless one of the two is already linked to the other. The top and bottom edge offsets do not
apply there.

Whether a chain's slopes get one is its "abutment" setting: "auto" (the default: where the
top edge runs up to a picked wall, or to a wall of the model that stands above the roof),
"g3" (the G3 flashing on every level top edge that is not a ridge), "below" (the turned-up
trays wherever the wall above is standing seam), "mitre" (mitred, where the wall above is
standing seam in upright trays) or "none". An abutment takes the place of
a head verge on the same edge, and the chain's top edge offset does not apply under it.

Not modelled: the ridge and sheet clips, the insect mesh, the fixing wedge and the mastic,
the saddle piece's rise to the wall, the welt at the foot of the wall's trays on the edge
flashing, laps between lengths, the flashing's ends at a verge."""

import math

from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient

from cladding_booleans import _difference, iter_polygons, polygon_to_rings
from cladding_constants import _parse, _prism, _rect, chain_params, frame_to_world, frame_v
from cladding_edges import settings as edge_settings
from cladding_eaves import _joins
from cladding_head import HEAD_COVER, HEAD_MIN, _top_edges
from cladding_primitives import buildup_depth, clip_bounds_v
from cladding_ridge import (RIDGE_GAP, RIDGE_HEM, RIDGE_LEG, RIDGE_REST, RIDGE_TOL, FILLER_EDGE, FILLER_GAP, FILLER_W, _fillers,
                            _same_frame, _slot, _up_slope)
from cladding_verge import ROOF_PITCH

ABUT_TYPES = ("auto", "g3", "below", "mitre", "none")
ABUT_FOOT_TOL = 12.0  # mm – a wall part's foot this close over where the wall stops is at the abutment
ABUT_UP = 60.0        # mm – G3: the flashing up the wall, above its corner (the detail's 60 mm)
ABUT_CHASE = 15.0     # mm – its top turned into a wall that is not picked: the chase (scaled)
ABUT_LAP = 25.0       # mm – a picked wall's layers stop this far below the top of the flashing (assumed)
ABUT_PLY = 18.0       # mm – below cladding: the discontinuous plywood behind the upstand (scaled)
ABUT_UPSTAND = 100.0  # mm – the trays turned up the wall, above the tray (the detail's 100 mm)
ABUT_COVER = 60.0     # mm – the wall's cladding laps the upstand by this (the detail's 60 mm)
ABUT_HOOK = 15.0      # mm – the upstand's top welted back down, for the sheet clips (scaled)
ABUT_SADDLE = 75.0    # mm – the saddle piece over a seam's end, down the slope from the upstand (scaled)
ABUT_SADDLE_W = 8.0   # mm – its cheeks, each side of the seam's line
ABUT_SADDLE_OVER = 3.0   # mm – its top, above the seam's
ABUT_EDGE = 40.0      # mm – the edge flashing on the wall's plywood, above the foot of the wall's trays (scaled)
ABUT_EDGE_T = 1.0     # mm – its metal (the detail's 1 mm)
ABUT_EDGE_DRIP = 8.0  # mm – and below it, kicked out (assumed)
ABUT_VENT = 10.0      # mm – the least left between the upstand and the back of the wall's plywood: the vent (assumed)
ABUT_WALL_TOL = 80.0  # mm – a wall of the model this close to the top edge's line stands on it
ABUT_NEAR = 150.0     # mm – an abutment line of the wall's this close to the junction is the roof's own


def abut_setting(elev):
    said = (elev or {}).get("abutment")
    return said if said in ("g3", "below", "mitre", "none") else "auto"


def abuts_of(elev, p):
    """The face's head-wall abutments: [{"u0", "u1", "v": the top edge, "kind": "g3" or
    "below", "over": the picked wall above or None, "wall": the model's wall above where
    none is picked, "base": how far off that wall's face its cavity starts, "pitch"}].
    None on a face that is not a roof slope in trays run with the fall, has a ridge, or
    whose chain says none."""
    pitch = elev.get("pitch")
    said = abut_setting(elev)
    if (said == "none" or not _up_slope(p) or pitch is None or not 0.5 < float(pitch) <= ROOF_PITCH or elev.get("faces_down")
            or elev.get("ridge")):
        return []
    joins = _joins(elev, True, "abut")
    raw = {v.get("with"): v for v in elev.get("valleys") or [] if v.get("kind") == "abut"}
    walls = elev.get("walls_above") or []
    out = []
    for u0, u1, v in _top_edges(elev):
        over = next((j for j in joins if abs(j["v"] - v) < 2.0 and j["u0"] < u1 - 50.0 and j["u1"] > u0 + 50.0), None)
        wall = None if over else next((w for w in walls if abs(float(w["v"]) - v) <= ABUT_WALL_TOL
                                       and min(u1, float(w["u1"])) - max(u0, float(w["u0"])) >= HEAD_MIN), None)
        if over is None and wall is None and said != "g3":
            continue
        if over is not None:
            u0, u1 = max(u0, over["u0"]), min(u1, over["u1"])
        elif wall is not None:
            u0, u1 = max(u0, float(wall["u0"])), min(u1, float(wall["u1"]))
        seam = bool(over and over["seam"] > 0)                 # the wall above is standing seam
        upright = bool(over and over["trays"])                 # in trays that run up it
        kind = "mitre" if said == "mitre" and seam and upright else "below" if seam and said in ("auto", "below", "mitre") else "g3"
        out.append({"u0": u0, "u1": u1, "v": v, "kind": kind, "over": over["with"] if over else None,
                    "wall": (wall or {}).get("name") or None, "pitch": float(pitch), "asked": said, "seam_wall": seam,
                    "cavity": (over["soffit"] if over else 0.0), "depth": (over["depth"] if over else 0.0),
                    "base": float((raw.get(over["with"]) or {}).get("other_base") or 0.0) if over else 0.0})
    return out


def _tray_end(h, t):
    """Where the trays end, out from the wall's face on plan: RIDGE_GAP / 2 short of it under
    a G3 flashing; under cladding, at the upstand, which stands on the discontinuous
    plywood, itself on whatever of the wall's build-up lies under its cavity."""
    return RIDGE_GAP / 2.0 if h["kind"] == "g3" else h["base"] + ABUT_PLY + t


def _plane(h, t):
    """How far up the slope from the roof's top edge the plumb plane of its half ridge
    stands (cladding_ridge stops the trays RIDGE_GAP / 2 short of it, on plan)."""
    return (RIDGE_GAP / 2.0 - _tray_end(h, t)) / math.cos(math.radians(h["pitch"]))


def _section(h, face, seam_h, t):
    """The abutment in section, from the point of the roof's plane its plumb plane stands
    on: x level, down the slope positive; y up. {"wall": the wall's face, "line": the
    height of the line the roof's plane meets it on, "end": the trays' end, "tray": the
    top of the trays there, "top": the top of the flashing or the upstand, "stop": where a
    picked wall's layers stop, as a height above that line}."""
    s, c = math.sin(math.radians(h["pitch"])), math.cos(math.radians(h["pitch"]))
    end = RIDGE_GAP / 2.0
    wall = end - _tray_end(h, t)
    line = wall * s / c * -1.0                                # the roof's plane rises to the wall
    tray = face / c - end * s / c                             # the trays' top at their end
    out = {"wall": wall, "line": line, "end": end, "tray": tray, "s": s, "c": c}
    if h["kind"] == "g3":
        rest = face + seam_h + RIDGE_REST
        x = wall + t / 2.0                                    # the flashing's line up the wall
        along = (x - rest * s) / c                            # where the cap's line reaches it, down the slope from the origin
        corner = (x, rest * c - along * s)
        out.update(rest=rest, along=along, corner=corner, top=corner[1] + ABUT_UP, width=HEAD_COVER - along)
        out["stop"] = out["top"] - ABUT_LAP - line
    else:
        out.update(top=tray + ABUT_UPSTAND)
        out["stop"] = out["top"] - ABUT_COVER - line
    return out


def _mitre(h, face):
    """The mitre in section, from the line the roof's plane meets the wall's face on: x out
    from the wall, y up. The plane runs from that line to the corner the two claddings'
    outer faces meet in, the wall's h["depth"] out from it and the roof's *face* off the
    roof. {"corner": that corner's height, "kw": how far the cut rises for each mm further
    out from the wall (a wall part at depth s is cut at kw·s above the line), "kr": how far
    it comes back down the slope for each mm further off the roof (a roof part at depth s
    is cut kr·s down the slope from its top edge)}."""
    s, c = math.sin(math.radians(h["pitch"])), math.cos(math.radians(h["pitch"]))
    out = max(float(h["depth"]), 1.0)
    corner = face / c - out * s / c
    kw = corner / out
    return {"corner": corner, "kw": kw, "kr": (c - kw * s) / (s + kw * c), "s": s, "c": c}


def join_abuts(params):
    """Before anything is built, after cladding_eaves.join_eaves: tell the wall above each
    head-wall abutment how the roof below is finished against it, whatever chain that roof
    is in: on the wall's "abut" record, "abut_kind" and "abut_stop" (where its layers stop,
    above the line); and the roof's record "other_base", what of the wall's build-up lies
    under its cavity. Under cladding the wall's own splash zone over that roof is switched
    off: its trays come down over the roof's upstand."""
    by = {e.get("name"): e for e in params.get("elevations") or []}
    for e in params.get("elevations") or []:
        for v in e.get("valleys") or []:
            other = by.get(v.get("with")) if v.get("kind") == "abut" else None
            if other is None or not v.get("roof"):
                continue
            po = _parse(dict(chain_params(params, other), elevations=[]))
            v["other_base"] = round((po["sheathing_t"] if po["sheathing"] else 0.0) + (po["insulation_t"] if po["insulation"] else 0.0), 3)
    for e in params.get("elevations") or []:                   # e: the roof
        p = _parse(dict(chain_params(params, e), elevations=[]))
        for h in abuts_of(e, p):
            wall = by.get(h["over"])
            rec = next((v for v in (wall or {}).get("valleys") or [] if v.get("kind") == "abut" and v.get("with") == e.get("name")), None)
            if rec is None:
                continue
            if h["kind"] == "mitre":
                mit = _mitre(h, buildup_depth(p))
                rec.update(abut_kind="mitre", abut_stop=round(mit["corner"], 3), abut_kw=mit["kw"], abut_cut=0.0)
                # the seams have to line up: the wall's follow the roof's, unless the two are linked already
                if e.get("seam_from") != wall.get("name") and by.get(wall.get("seam_from")) in (None, wall):
                    wall.update(seam_from=e.get("name"), seam_from_implied=True)
                rec["abut_linked"] = e.get("seam_from") == wall.get("name") or wall.get("seam_from") == e.get("name")
            else:
                sec = _section(h, buildup_depth(p), float(p["seam_h"]), float(p["seam_t"]))
                rec.update(abut_kind=h["kind"], abut_stop=round(sec["stop"], 3), abut_t=float(p["seam_t"]))
            _stand_over(wall, rec)
            if h["kind"] in ("below", "mitre"):
                for ab in wall.get("abutments") or []:
                    line = ab.get("line") or [[ab.get("u0"), ab.get("v")], [ab.get("u1"), ab.get("v")]]
                    try:
                        near = all(abs(float(q[1]) - _line_v(rec)) <= ABUT_NEAR for q in line)
                    except (TypeError, ValueError):
                        continue
                    if near and ab.get("source") != "base":
                        ab["enabled"] = False


def _stand_over(wall, rec):
    """Cut the wall's outline off level over the roof, so that with its chain's bottom edge
    offset every layer of it stops where join_abuts said: whether its outline stood on the
    line the two meet on or ran on down behind the roof, what is built is the wall above."""
    u0, u1 = sorted((float(rec["p1"][0]), float(rec["p2"][0])))
    cut = _line_v(rec) + float(rec.get("abut_cut", rec["abut_stop"])) - edge_settings(wall)["bottom"]
    box = Polygon([(u0, -1e7), (u1, -1e7), (u1, cut), (u0, cut)])
    polys = []
    for pg in wall.get("polygons") or []:
        try:
            left = _difference(Polygon(pg["exterior"], pg.get("holes") or []), box)
        except Exception:   # noqa: BLE001
            return
        for q in iter_polygons(left):
            if q.area > 1.0:
                ext, holes = polygon_to_rings(q)
                polys.append({"exterior": ext, "holes": holes})
    if polys:
        wall["polygons"] = polys


def _line_v(rec):
    return (float(rec["p1"][1]) + float(rec["p2"][1])) / 2.0


def abut_ridges(p):
    """Before the ridges are cut, after cladding_head.head_ridges: give each face with a
    head-wall abutment a ridge record with no partner ("head_ridge", read by
    cladding_ridge.ridge_of) in place of any head verge's, so its layers are brought to
    the wall as at a ridge. One a face: its longest."""
    for elev in p.get("elevations", []):
        elev.pop("abut_mitre", None)
        hs = abuts_of(elev, p)
        if not hs:
            continue
        h = max(hs, key=lambda q: q["u1"] - q["u0"])
        if h["kind"] == "mitre":                 # no half ridge: the trays run on to the corner
            elev.pop("head_ridge", None)
            elev["abut_mitre"] = h
            continue
        elev["head_ridge"] = {"with": None, "v": h["v"] + _plane(h, float(p["seam_t"])), "u0": h["u0"], "u1": h["u1"],
                              "k": math.tan(math.radians(h["pitch"])), "master": True, "abut": h}


def _sheet(path, t):
    sheet = orient(LineString(path).buffer(t / 2.0, cap_style=2, join_style=2, mitre_limit=3.0), 1.0)
    return [[round(x, 3), round(y, 3)] for x, y in list(sheet.exterior.coords)[:-1]]


def _g3_ring(sec, t, chase):
    """The G3 flashing's section: its hemmed edge HEAD_COVER down the slope, up the slope's
    line to the wall, up the wall ABUT_UP, and (*chase*) its top turned into the wall."""
    s, c = sec["s"], sec["c"]
    d, n = (c, -s), (s, c)
    at = lambda along: (sec["rest"] * n[0] + along * d[0], sec["rest"] * n[1] + along * d[1])       # noqa: E731
    edge = at(HEAD_COVER)
    under = (edge[0] - 2.0 * t * n[0], edge[1] - 2.0 * t * n[1])
    hem = (under[0] - RIDGE_HEM * d[0], under[1] - RIDGE_HEM * d[1])
    x = sec["corner"][0]
    path = [hem, under, edge, sec["corner"], (x, sec["top"])] + ([(x - ABUT_CHASE, sec["top"])] if chase else [])
    return _sheet(path, t)


def _upstand_ring(sec, t):
    """A tray's end turned up the wall: from the tray's own sheet up to the top, welted back
    down on the open side."""
    x = sec["end"] - t / 2.0
    return _sheet([(x, sec["tray"] - t), (x, sec["top"]), (x + 2.0 * t, sec["top"]), (x + 2.0 * t, sec["top"] - ABUT_HOOK)], t)


def _saddles(p, elev, r, meshes, face, name):
    """A saddle piece over the end of each seam, against the upstand: two cheeks and a top,
    ABUT_SADDLE down the slope, cut plumb at the upstand as the seams are."""
    t, high = float(p["seam_t"]), float(p["seam_h"]) + ABUT_SADDLE_OVER
    lines = sorted({round(float(m["seam_line"]), 1) for m in meshes
                    if m.get("elevation") == name and m.get("ifc_type") == "seam" and m.get("seam_v")
                    and r["u0"] < float(m["seam_line"]) < r["u1"]})
    out = []
    for i, u in enumerate(lines):
        w = ABUT_SADDLE_W
        for part, (a, b, depth, thick) in (("Cheek L", (u - w - t, u - w, face, high)), ("Cheek R", (u + w, u + w + t, face, high)),
                                           ("Top", (u - w - t, u + w + t, face + high, t))):
            mid = depth + thick / 2.0
            top = _slot(r) + r["k"] * mid
            m = _prism(_rect(a, top - ABUT_SADDLE, b, top), depth, thick, elev["frame"], "ridge_cap",
                       "%s Abutment Saddle %d %s" % (name, i + 1, part), name)
            m["vshift"] = [[[0.0, 0.0, -r["k"] * mid, r["k"]] if q[1] > top - 1.0 else [0.0] * 4 for q in m["profile"]]]
            m.update(ridge_part=True, abut_saddle=i + 1)
            out.append(m)
    return out, len(lines)


def _cut_to(m, at, v_at, row_of):
    """Move the chosen vertices of a part's outline to a cut that leans with depth: to
    *v_at* (the cut at the part's mid-depth), with the shift per vertex ("vshift": v += c +
    d·s) *row_of* gives from the vertex's old row, whose own lean (a fold, a ridge's cut)
    it replaces."""
    rows = (m.get("vshift") or [[]])[0] or [[0.0] * 4 for _ in m["profile"]]
    out = []
    for q, sh, on in zip(m["profile"], rows, at):
        if on:
            q[1] = round(v_at, 3)
            out.append(row_of(sh))
        else:
            out.append(list(sh))
    m["vshift"] = [out] + [[[0.0] * 4 for _ in hole] for hole in (m.get("holes") or [])]


def _mitre_roof(meshes, p, elev, h, info):
    """The roof's side of a mitred abutment: every layer runs up to the mitre and is cut on
    it, the trays and their seams to the corner their outer faces make with the wall's."""
    name, frame = elev.get("name"), elev["frame"]
    face = float(info.get("total_depth") or 0.0)
    mit = _mitre(h, face)
    top = clip_bounds_v(elev)[1]
    kr = mit["kr"]
    trays, lines, layers = 0, set(), set()
    for m in meshes:
        if m.get("elevation") != name or not _same_frame(m["frame"], frame) or m.get("ridge_part"):
            continue
        at = [q[1] >= top - RIDGE_TOL and h["u0"] - 1.0 <= q[0] <= h["u1"] + 1.0 for q in m["profile"]]
        if not any(at):
            continue
        mid = float(m["depth"]) + float(m["thickness"]) / 2.0
        _cut_to(m, at, h["v"] - kr * mid, lambda sh: [sh[0], sh[1], kr * mid, -kr])
        m["abut_mitre"] = True
        layers.add(m.get("ifc_type"))
        if m.get("ifc_type") == "plank":
            trays += 1
        elif m.get("ifc_type") == "seam" and m.get("seam_line") is not None:
            lines.add(round(float(m["seam_line"]), 1))
    info["abut"] = {"kind": "mitre", "length": round(h["u1"] - h["u0"], 1), "over": h["over"], "wall": None, "asked": h["asked"],
                    "u0": round(h["u0"], 1), "u1": round(h["u1"], 1), "v": round(h["v"], 1), "trays": trays, "seams": len(lines),
                    "angle": round(90.0 + h["pitch"], 1), "stop": round(mit["corner"], 1), "seam_wall": True,
                    "layers": sorted(k for k in layers if k),
                    "follows": (elev.get("seam_lines") or {}).get("from") if (elev.get("seam_lines") or {}).get("lines") else None}


def abut_parts(meshes, p, infos):
    """The roof's side of each head-wall abutment: the G3 flashing and its decompression
    strips, or the trays' turned-up ends, the plywood behind them and the saddle pieces.
    Records it on the face's info ("abut")."""
    info_by = {i["elevation"]: i for i in infos}
    out, gone = [], set()
    for elev in p.get("elevations", []):
        r = elev.get("head_ridge")
        info = info_by.get(elev.get("name"))
        if info is not None and elev.get("abut_mitre"):
            _mitre_roof(meshes, p, elev, elev["abut_mitre"], info)
            continue
        if not r or "abut" not in r or info is None:
            continue
        h, name, frame = r["abut"], elev.get("name"), elev["frame"]
        face, t = float(info.get("total_depth") or 0.0), float(p["seam_t"])
        sec = _section(h, face, float(p["seam_h"]), t)
        up, U = frame_v(frame), frame["u"]
        level = [-up[0] / math.hypot(up[0], up[1]), -up[1] / math.hypot(up[0], up[1]), 0.0]
        along = [level[1], -level[0], 0.0]
        length = h["u1"] - h["u0"]
        forward = sum(a * b for a, b in zip(along, U)) > 0
        place = lambda u0, u1: {"origin": frame_to_world(frame, u0 if forward else u1, r["v"], 0.0), "u": level,       # noqa: E731
                                "v": [0.0, 0.0, 1.0], "n": along}
        rec = {"kind": h["kind"], "length": round(length, 1), "over": h["over"], "wall": h["wall"], "thickness": t,
               "u0": round(h["u0"], 1), "u1": round(h["u1"], 1), "v": round(h["v"], 1), "asked": h["asked"],
               "stop": round(sec["stop"], 1), "gap": RIDGE_GAP, "seam_wall": h["seam_wall"]}
        if h["kind"] == "g3":
            cap = _prism(_g3_ring(sec, t, chase=not h["over"]), 0.0, length, place(h["u0"], h["u1"]), "ridge_cap",
                         "%s Abutment Flashing" % name, name)
            rec.update(width=round(sec["width"], 1), up=ABUT_UP, standard=sec["width"] <= RIDGE_LEG + 0.5, chase=not h["over"])
            cap["abut_info"] = rec
            out.append(cap)
            edge = r["v"] - HEAD_COVER
            upstand = _slot(r) + r["k"] * face
            low = min(edge + FILLER_EDGE, upstand - FILLER_GAP - FILLER_W)
            out += _fillers(p, elev, r, low, meshes, face)
            rec["clip_room"] = round(low - edge - 10.0, 1)
        else:
            # each tray's end turned up: in place of the short turn-up a ridge gives it
            sheet = ABUT_UPSTAND + ABUT_HOOK + 2.0 * t
            ups = [m for m in meshes if m.get("elevation") == name and m.get("ridge_part") and m.get("name", "").endswith(" Ridge Upstand")]
            ups.sort(key=lambda m: min(q[0] for q in m["profile"]))
            spans = [(min(q[0] for q in m["profile"]) - 3.0, max(q[0] for q in m["profile"]) + 3.0, m) for m in ups]
            marks = [h["u0"]] + [(a[1] + b[0]) / 2.0 for a, b in zip(spans, spans[1:])] + [h["u1"]]
            trays = {m.get("name"): m for m in meshes if m.get("elevation") == name and m.get("ifc_type") == "plank"}
            ring, n = _upstand_ring(sec, t), 0
            for (m0, m1), (_a, _b, m) in zip(zip(marks, marks[1:]), spans):
                gone.add(id(m))
                owner = m["name"][:-len(" Ridge Upstand")]
                if m1 - m0 < 0.5:
                    continue
                turn = _prism(ring, 0.0, m1 - m0, place(m0, m1), "seam", "%s Abutment Upstand" % owner, name)
                turn.update(opacity=1.0, ridge_part=True, tray_sheet=True, of=owner, abut_upstand=True)
                tray = trays.get(owner)
                if tray is not None:
                    tray["cut_extra"] = round(float(tray.get("cut_extra") or 0.0) + sheet, 1)
                    turn["color"] = tray.get("color", turn["color"])
                out.append(turn)
                n += 1
            x0 = sec["end"] - t - ABUT_PLY
            back = [[x0, sec["tray"] - 60.0], [x0 + ABUT_PLY, sec["tray"] - 60.0], [x0 + ABUT_PLY, sec["top"] + 10.0], [x0, sec["top"] + 10.0]]
            out.append(_prism(back, 0.0, length, place(h["u0"], h["u1"]), "deck", "%s Abutment Backing" % name, name))
            saddles, count = _saddles(p, elev, r, meshes, face, name)
            out += saddles
            rec.update(upstand=ABUT_UPSTAND, cover=ABUT_COVER, sheet=round(sheet, 1), trays=n, saddles=count, ply=ABUT_PLY,
                       room=round(h["cavity"] - h["base"] - ABUT_PLY - t, 1))      # behind the wall's plywood, clear of the upstand: the vent
            out[-1 if not saddles else -len(saddles) - 1]["abut_info"] = rec
        info["abut"] = rec
    return [m for m in meshes if id(m) not in gone] + out


def abut_over(meshes, p, infos):
    """The wall above a head-wall abutment: stop every layer level above the roof (where
    join_abuts said: under the top of a G3 flashing, or lapping the roof's upstand), and
    under standing seam cladding stand the edge flashing on its plywood. Records it on the
    face's info ("abut_over"). A part that would be left with nothing is left out."""
    info_by = {i["elevation"]: i for i in infos}
    gone, extra = set(), []
    for elev in p.get("elevations", []):
        name, frame, info = elev.get("name"), elev.get("frame"), info_by.get(elev.get("name"))
        if info is None:
            continue
        raw = {v.get("with"): v for v in elev.get("valleys") or [] if v.get("kind") == "abut" and "abut_kind" in v}
        mine = [m for m in meshes if m.get("elevation") == name and _same_frame(m["frame"], frame)]
        for j in _joins(elev, False, "abut"):
            rec = raw.get(j["with"])
            if rec is None:
                continue
            stop = j["v"] + float(rec["abut_stop"])
            if rec["abut_kind"] == "mitre":
                info.setdefault("abut_over", []).append(_mitre_wall(mine, j, rec, stop, float(info.get("total_depth") or 0.0)))
                continue
            for m in mine:
                pts = m["profile"]
                at = [j["u0"] - 1.0 <= q[0] <= j["u1"] + 1.0 and j["v"] - 60.0 <= q[1] < stop - 0.01 for q in pts]
                if not any(at):
                    continue
                if all(j["v"] - 60.0 <= q[1] <= stop + 1.0 for q in pts):
                    gone.add(id(m))                    # all of it stands below where the wall stops
                    continue
                for q, on in zip(pts, at):
                    if on:
                        q[1] = round(stop, 3)
            over = {"with": j["with"], "kind": rec["abut_kind"], "length": round(j["length"], 1), "stop": round(float(rec["abut_stop"]), 1)}
            if rec["abut_kind"] == "below" and p.get("seam"):
                decks = [float(m["depth"]) + float(m["thickness"]) for m in mine if m.get("ifc_type") == "deck"]
                if decks:
                    extra.append(_edge_flashing(frame, j, stop, max(decks), name))
                    over["edge"] = ABUT_EDGE
            info.setdefault("abut_over", []).append(over)
    return [m for m in meshes if id(m) not in gone] + extra


def _mitre_wall(mine, j, rec, stop, face):
    """The wall's side of a mitred abutment: every layer comes down to the mitre and is cut
    on it, its trays and their seams to the corner."""
    kw = float(rec["abut_kw"])
    trays, lines = 0, set()
    for m in mine:
        at = [j["u0"] - 1.0 <= q[0] <= j["u1"] + 1.0 and j["v"] - 80.0 <= q[1] <= j["v"] + 30.0 for q in m["profile"]]
        if not any(at):
            continue
        mid = float(m["depth"]) + float(m["thickness"]) / 2.0
        _cut_to(m, at, j["v"] + kw * mid, lambda sh: [sh[0], sh[1], -kw * mid, kw])
        m["abut_mitre"] = True
        if m.get("ifc_type") == "plank":
            trays += 1
        elif m.get("ifc_type") == "seam" and m.get("seam_line") is not None:
            lines.add(round(float(m["seam_line"]), 1))
    return {"with": j["with"], "kind": "mitre", "length": round(j["length"], 1), "stop": round(float(rec["abut_stop"]), 1),
            "trays": trays, "seams": len(lines), "followed": bool(rec.get("abut_linked"))}


def _edge_flashing(frame, j, stop, deck, name):
    """The continuous edge flashing at the foot of the wall's trays: on the plywood, from
    ABUT_EDGE above their foot to a drip kicked out below it."""
    N, U = frame["n"], frame["u"]
    out = [float(N[0]), float(N[1]), 0.0]
    along = [out[1], -out[0], 0.0]
    forward = sum(a * b for a, b in zip(along, U)) > 0
    x = deck + ABUT_EDGE_T / 2.0
    ring = _sheet([(x, ABUT_EDGE), (x, -3.0), (x + 6.0, -ABUT_EDGE_DRIP)], ABUT_EDGE_T)
    place = {"origin": frame_to_world(frame, j["u0"] if forward else j["u1"], stop, 0.0), "u": out, "v": [0.0, 0.0, 1.0], "n": along}
    m = _prism(ring, 0.0, j["u1"] - j["u0"], place, "ridge_cap", "%s Abutment Edge Flashing" % name, name)
    m["abut_edge"] = True
    return m
