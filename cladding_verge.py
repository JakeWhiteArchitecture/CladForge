"""CladForge — the verge: the free side edge of a roof slope clad in standing seam trays
that run with the fall.

After VMZINC's standard detail "Verge junction G3" (VMZ-SN-MA-ZI-G3-01), **drawn as the
eaves is** (cladding_eaves): the roof's edge rolled round in the trays' own sheet. The
face's side edge is taken as the outer face of the roof's edge (the detail's vertical
plywood).

  * **The plywood runs on** past the edge to VERGE_TRAY beyond it: VERGE_PLY_NOSE past the
    drip's line, which stands VERGE_CLEAR (the detail's 10 mm) clear of the edge's face.
    The last tray runs out to the plywood's end; its outline is widened.
  * **The drip is the last tray's own sheet**, the same coil as the roof covering: at the
    plywood's end it turns round it in a half circle, comes back under it, drops square
    to the roof (in the plane of the gable) VERGE_BELOW below the roof's surface and is
    hooked back up at its foot (cladding_eaves._eaves_rings, the eaves' section, level).
    One length of it to each tray at the edge, marked as that tray's ("of": the IFC has
    the tray and its drip as one element); the tray carries the sheet it takes across its
    width ("cut_extra_w"), so the cutting list has it as wide as the coil it is cut from.
  * **The support profile**: an angle under the plywood with a leg hanging behind the
    drop, which the zinc is dressed over and hooked round.
  * The last seam stands between 150 and 370 mm from the tray's edge (the detail's rule),
    reported per verge and checked.

Only the clearance of the drop and the seam's distance are dimensioned on the detail; the
other sizes are the eaves'. Which sides of a chain get a verge is the chain's
"verge" setting: both, the start of the run (the left of its first face), the end, or
none — a side that runs into a wall is not a verge and has to be switched off.

The alternative, where the roof's edge stands over a clad wall, is VMZINC's "Verge with
cladding" (VMZ-SN-MA-ZI-05), the chain's "verge_type" set to "cladding":

  * **The plywood oversails the roof's edge by the thickness of the cladding on the wall
    below** (join_verges), as a sill board runs out over the cladding under a window, so
    everything that follows stands in front of that cladding.
  * **The edge is a standing seam, the same closed double lock as between two trays**
    (_edge_seam). The last tray runs VERGE_FACE less the seam's air beyond the plywood's
    edge and turns up as the undercloak, the head on the roof's side.
  * **The verge flashing is the seam's other sheet**, the overcloak: from the seam it runs
    straight down the face of the edge, past the plywood and the ventilation gap under it,
    to cover the head of the wall's cladding by VERGE_COVER (60, dimensioned). Its foot
    is hemmed inward. Each sheet of the seam is marked as its owner's, the tray's or the
    flashing's ("of").
  * The continuous folded strip goes **up and over the plywood**, VERGE_CLIP_LAP in from
    the roof's edge on its top under the last tray, so it is fixed down through the
    plywood into the batten there; then down the face of the edge behind the flashing,
    its foot kicked out into the flashing's hem to hold it.

Only the 60 is dimensioned on that sheet; the gap under the plywood and the flashing's
stand-off are scaled from it. No tray is widened and the 150 to 370 rule does not apply:
the last tray is a tray, between two seams.

Not modelled: the vertical plywood and the timber behind it, the fixings, the lift the
strip gives the tray's edge, the insect mesh, and the wall's cladding stopping under the
roof's plywood (heal the wall up to the roof for that)."""

from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient

from cladding_constants import SEAM_AIR, _prism, frame_to_world, frame_v
from cladding_ridge import ridge_of, _slot, _same_frame, _up_slope

VERGE_CLEAR = 10.0    # mm – the drop, clear of the edge's face (dimensioned)
VERGE_PLY_NOSE = 40.0  # mm – the plywood, on past the drip's line (the eaves' EAVES_NOSE)
VERGE_TRAY = VERGE_CLEAR + VERGE_PLY_NOSE   # mm – the plywood's end, and the last tray's edge, beyond the roof's edge
VERGE_BELOW = 50.0    # mm – the drop's foot, below the roof's surface (the eaves' EAVES_DROP)
VERGE_DROP = 65.0     # mm – ZI-05 fallback only (a ridge's square end): the drop, down from the deck
VERGE_HEM = 16.0      # mm – ZI-05: the hem at the flashing's foot (scaled)
VERGE_SEAM = (150.0, 370.0)   # mm – the last seam, in from the tray's edge (dimensioned)
VERGE_COVER = 60.0    # mm – ZI-05: the flashing over the head of the wall's cladding (dimensioned)
VERGE_GAP = 25.0      # mm – ZI-05: the ventilation gap under the roof's plywood (scaled)
VERGE_FACE = 5.0      # mm – ZI-05: the flashing, clear of the edge's face (scaled)
VERGE_LOCK = 10.0     # mm – ZI-05: the flashing turned down inside the seam (scaled)
VERGE_CLIP_LAP = 40.0  # mm – ZI-05: the folded strip on the plywood's top, in from the roof's edge: over the batten there (scaled)
VERGE_TYPES = ("g3", "cladding")
ROOF_PITCH = 75.0     # deg – steeper than this a face is a wall, and has no verge
EDGE_TOL = 12.0       # mm – a tray edge this close to the roof's edge is the edge


def verge_sides(elev, p):
    """The sides of this face that take a verge: a subset of ("l", "r"). None on a wall,
    a face that looks down, or anything but trays running with the fall."""
    pitch = elev.get("pitch")
    if not _up_slope(p) or pitch is None or float(pitch) > ROOF_PITCH or elev.get("faces_down"):
        return ()
    want = elev.get("verge") or "both"
    if want == "none":
        return ()
    start, end = ("r", "l") if elev.get("chain_reversed") else ("l", "r")
    return {"both": (start, end), "start": (start,), "end": (end,)}.get(want, (start, end))


def verge_type(elev):
    """Which verge this face's chain takes: "g3" (the strip, VMZ-SN-MA-ZI-G3-01) or
    "cladding" (the flashing down over a clad wall, VMZ-SN-MA-ZI-05)."""
    want = elev.get("verge_type")
    return want if want in VERGE_TYPES else "g3"


def verge_most(elev, p):
    """The most the last seam may stand in from the tray's edge at a G3 verge: VMZINC's 370
    (VERGE_SEAM), or with the chain's "verge_last" set to "full", a whole tray (then the
    check warns that it is past the detail)."""
    tray = float(p["plank_w"])
    return tray if elev.get("verge_last") == "full" else min(VERGE_SEAM[1], tray)


def _edges(info):
    """{"l": (u, v0, v1), "r": ...}: the straight side edges at the two ends of the clad
    area, those at least 300 long. A raking side (a hip) is not a verge."""
    sides = [e for e in info.get("edges") or [] if e["kind"] == "side" and abs(e["p1"][0] - e["p2"][0]) < 0.5]
    if not sides:
        return {}
    us = [e["p1"][0] for e in sides]
    out = {}
    for side, u in (("l", min(us)), ("r", max(us))):
        vs = [v for e in sides if abs(e["p1"][0] - u) < 1.0 for v in (e["p1"][1], e["p2"][1])]
        if vs and max(vs) - min(vs) >= 300.0:
            out[side] = (u, min(vs), max(vs))
    return out


VERGE_WALL_REACH = 400.0   # mm – the roof's edge this far or less out from a wall's face stands over that wall
VERGE_WALL_SHORT = 60.0    # mm – or this far or less short of it


def join_verges(params):
    """Before anything is built: at a verge with cladding **the roof's plywood oversails the
    roof's edge by the thickness of the cladding on the wall below**, as a sill board runs
    out over the cladding under a window, so the flashing drops in front of that cladding.
    Each roof slope is told how far, a side: {"l": mm, "r": mm} as "verge_over", and where
    it came from as "verge_over_from" (the wall's name, or "set").

    The wall is found among the picked faces: upright, looking out the way the verge
    does, its face within VERGE_WALL_REACH behind the roof's edge (or VERGE_WALL_SHORT in
    front of it). What the modelled roof already oversails that face by comes off. The
    chain's own "verge_oversail" (mm), where it is set, is used instead."""
    from cladding_constants import _parse, chain_params
    from cladding_primitives import buildup_depth
    faces = params.get("elevations") or []
    dot = lambda a, b: sum(x * y for x, y in zip(a, b))                                           # noqa: E731
    for e in faces:
        e.pop("verge_over", None)
        e.pop("verge_over_from", None)
        pitch = e.get("pitch")
        if verge_type(e) != "cladding" or pitch is None or float(pitch) > ROOF_PITCH or not e.get("frame"):
            continue
        f, W, H = e["frame"], float(e.get("width") or 0.0), float(e.get("height") or 0.0)
        over, source = {}, {}
        try:
            fixed = float(e.get("verge_oversail"))
        except (TypeError, ValueError):
            fixed = None
        for side, u, out in (("l", 0.0, [-c for c in f["u"]]), ("r", W, list(f["u"]))):
            if fixed is not None:
                over[side], source[side] = max(0.0, fixed), "set"
                continue
            at = frame_to_world(f, u, H / 2.0, 0.0)
            best = None
            for w in faces:
                fw = w.get("frame")
                if w is e or not fw or abs(fw["n"][2]) > 0.26 or dot(fw["n"], out) < 0.98:
                    continue
                rel = [a - b for a, b in zip(at, fw["origin"])]
                d, along = dot(rel, fw["n"]), dot(rel, fw["u"])
                if -VERGE_WALL_SHORT <= d <= VERGE_WALL_REACH and -100.0 <= along <= float(w.get("width") or 0.0) + 100.0 \
                        and (best is None or abs(d) < abs(best[0])):
                    best = (d, w)
            if best:
                pw = _parse(dict(chain_params(params, best[1]), elevations=[]))
                thick = buildup_depth(pw) + (float(pw["seam_h"]) if pw.get("seam") else 0.0)       # to the tips of its seams
                over[side], source[side] = round(max(0.0, thick - best[0]), 1), best[1].get("name")
        if over:
            e["verge_over"], e["verge_over_from"] = over, source


def verge_over(elev, side):
    """How far the plywood oversails the roof's edge on that side (join_verges): 0 where
    nothing is clad below it."""
    return float((elev.get("verge_over") or {}).get(side) or 0.0)


def verge_seam_out():
    """How far beyond the roof's edge the last tray runs to its seam at a verge with cladding:
    the seam stands over the flashing, VERGE_FACE clear of the edge's face."""
    return VERGE_FACE - SEAM_AIR


def _flashing_rings(y0, top, drop, t, lap=None):
    """ZI-05's two sheets below the seam, in section (x out from the plywood's edge, y
    the depth from the face): the flashing, straight down from the foot of its upstand in
    the seam (at *top*, the trays' face) to *drop* below the deck's top *y0*, hemmed
    inward; and the continuous strip behind it: **up and over the plywood**, *lap* back on
    its top under the last tray, so it is fixed down through the plywood into the batten
    at the roof's edge, then down the edge's face, its foot kicked out into that hem."""
    g = 2.5 * t
    x = VERGE_FACE + t / 2.0                 # in line with its upstand in the seam above
    foot = y0 - drop
    flashing = LineString([(x, top), (x, foot), (x - g, foot), (x - g, foot + VERGE_HEM)])
    e = t / 2.0 + 0.5
    on = y0 - 1.0 + t / 2.0                  # on the plywood's top, in the 2 mm the trays stand off it
    clip = LineString([(-(VERGE_CLIP_LAP if lap is None else lap), on), (e, on), (e, foot + VERGE_HEM + 12.0),
                       (x - g / 2.0, foot + VERGE_HEM), (x - g / 2.0, foot + t)])
    ring = lambda path: list(path.buffer(t / 2.0, cap_style=2, join_style=2, mitre_limit=3.0).exterior.coords)[:-1]   # noqa: E731
    return ring(flashing), ring(clip)


def _edge_seam(p, frame, name, letter, u, out, v0, v1, face, tray, flashing):
    """The seam at a verge with cladding: **the same closed double lock as between two
    trays** (cladding_geometry._double_lock), sheet by sheet, its line SEAM_AIR / 2 beyond
    the last tray's edge. The tray turns up the undercloak, on the roof's side, and the
    head hangs on that side; the flashing is the overcloak, on the outside. Each sheet is
    marked as its owner's ("of"): the tray's, or the flashing's."""
    from cladding_constants import _rect
    from cladding_geometry import _double_lock, SEAM_PARTS
    line = u + out * (verge_seam_out() + SEAM_AIR / 2.0)
    parts = []
    for part, side, c0, c1, z0, z1 in _double_lock(p["seam_t"], p["seam_h"]):
        if out > 0:                          # the right verge: mirrored, so the head is still on the roof's side
            c0, c1 = -c1, -c0
        mine = side == "+"                   # the undercloak: the tray's own sheet
        owner = tray if mine else flashing
        title = "%s Verge Seam %s" % (tray, SEAM_PARTS[part]) if mine else "%s Verge Seam %s %s" % (name, letter, SEAM_PARTS[part])
        m = _prism(_rect(line + c0, v0, line + c1, v1), face + z0, z1 - z0, frame, "seam", title, name)
        m.update(seam_part=part, seam_side=side, seam_v=True, seam_line=line, seam_face=face, verge_seam=True, of=owner)
        parts.append(m)
    return parts


def verge_parts(meshes, p, infos):
    """At each verge: G3, run the plywood and the last tray on and roll the tray's sheet
    round into the drip, over a support profile; with cladding, the edge seam and the
    flashing. Records each face's verges on its info ("verges", "verge_strips")."""
    from cladding_eaves import _eaves_rings, eaves_paths
    info_by = {i["elevation"]: i for i in infos}
    extra = []
    for elev in p.get("elevations", []):
        name, frame, info = elev.get("name"), elev["frame"], info_by.get(elev.get("name"))
        sides = verge_sides(elev, p)
        if not sides or info is None:
            continue
        mine = [m for m in meshes if m.get("elevation") == name and _same_frame(m["frame"], frame)]
        trays = [m for m in mine if m.get("ifc_type") == "plank"]
        lines = sorted({float(m["seam_line"]) for m in mine if m.get("seam_part") and m.get("seam_v")})
        if not trays:
            continue
        back, t = min(float(m["depth"]) for m in trays), float(p["seam_t"])
        r = ridge_of(elev)
        found = _edges(info)
        for side in sides:
            if side not in found:
                continue
            u, v0, v1 = found[side]
            out = -1.0 if side == "l" else 1.0
            kind = verge_type(elev)
            decks = [m for m in mine if m.get("ifc_type") == "deck"]
            deck_t = max((float(m["thickness"]) for m in decks), default=18.0)
            soffit = min((float(m["depth"]) for m in decks), default=back - 1.0 - deck_t)
            drop = (deck_t + VERGE_BELOW) if kind == "g3" else deck_t + VERGE_GAP + VERGE_COVER
            edge_trays = []
            if kind == "g3":
                for m in trays + [m for m in mine if m.get("ridge_part")] + decks:     # the plywood runs on, and the trays to its end
                    hit = False
                    for q in m["profile"]:
                        if abs(q[0] - u) <= EDGE_TOL and v0 - 1.0 <= q[1]:
                            q[0] = round(u + out * VERGE_TRAY, 3)
                            hit = True
                    if hit and m.get("ifc_type") == "plank":
                        edge_trays.append(m)
            if kind == "g3":    # the eaves' section, level: the tray's sheet round the plywood's end, back under it and down into the drip
                zinc, support, sheet = _eaves_rings(back, soffit, t, 0.0, VERGE_BELOW)
                foot = min(y for x, y in zinc + support)
                roll_path, drop_path = eaves_paths(back, soffit, t, 0.0, VERGE_BELOW)[:2]
            else:
                foot = back - 1.0 - drop
            if r:       # at a ridge: square-ended, its foot no nearer the bisecting plane than the trays come
                v1 = _slot(r) + r["k"] * foot
            V, U, N = frame_v(frame), frame["u"], frame["n"]
            toward = [out * c for c in U]
            across = [V[1] * toward[2] - V[2] * toward[1], V[2] * toward[0] - V[0] * toward[2], V[0] * toward[1] - V[1] * toward[0]]
            sign = 1.0 if sum(a * b for a, b in zip(across, N)) > 0 else -1.0     # which way the section's y runs
            over = verge_over(elev, side) if kind == "cladding" else 0.0
            ue = u + out * over                        # the plywood's edge: the roof's, or out over the cladding below
            place = lambda: {"origin": frame_to_world(frame, ue, v0, 0.0), "u": toward, "v": across, "n": [float(c) for c in V]}   # noqa: E731
            tidy = lambda pts: [[round(x, 3), round(y, 3)] for x, y in                               # noqa: E731
                                list(orient(Polygon([(x, sign * y) for x, y in pts]), 1.0).exterior.coords)[:-1]]
            letter = "L" if side == "l" else "R"
            seam = min((abs(x - u) for x in lines if abs(x - u) > EDGE_TOL), default=None)
            if kind == "cladding":
                face = back + t                        # the trays' face: where a seam's sheets start
                flashing, clip = _flashing_rings(back - 1.0, face, drop, t, over + VERGE_CLIP_LAP)   # over the oversail, onto the roof
                strip = _prism(tidy(flashing), 0.0, v1 - v0, place(), "verge_strip", "%s Verge Flashing %s" % (name, letter), name)
                extra.append(_prism(tidy(clip), 0.0, v1 - v0, place(), "verge_strip", "%s Verge Clip %s" % (name, letter), name))
                edge = [m for m in trays if any(abs(q[0] - u) <= EDGE_TOL for q in m["profile"])]
                for m in decks:                        # the plywood oversails the roof's edge, over the cladding below
                    for q in m["profile"]:
                        if abs(q[0] - u) <= EDGE_TOL and v0 - 1.0 <= q[1]:
                            q[0] = round(ue, 3)
                for m in edge + [m for m in mine if m.get("ridge_part")]:      # the last tray runs on to its seam, just beyond the plywood's edge
                    for q in m["profile"]:
                        if abs(q[0] - u) <= EDGE_TOL and v0 - 1.0 <= q[1]:
                            q[0] = round(ue + out * verge_seam_out(), 3)
                extra.extend(_edge_seam(p, frame, name, letter, ue, out, v0, v1, face, edge[0]["name"] if edge else strip["name"], strip["name"]))
                strip["verge_info"] = {"side": side, "type": kind, "length": round(v1 - v0, 1), "lap": 0.0, "nose": VERGE_FACE,
                                       "drop": round(drop, 1), "clear": VERGE_FACE, "cover": VERGE_COVER, "thickness": t,
                                       "over": round(over, 1), "over_from": (elev.get("verge_over_from") or {}).get(side),
                                       "last_seam": None, "last_tray": None if seam is None else round(seam + verge_seam_out(), 1)}
            else:
                strip = _prism(tidy(support), 0.0, v1 - v0, place(), "verge_strip", "%s Verge Support %s" % (name, letter), name)
                strip.update(verge_support=True, color="#eab308")
                drips = []
                for m in sorted(edge_trays, key=lambda m: min(q[1] for q in m["profile"])):
                    lo = max(v0, min(q[1] for q in m["profile"]))
                    hi = min(v1, max(q[1] for q in m["profile"]))
                    if hi - lo < 1.0:
                        continue
                    m["cut_extra_w"] = round(sheet, 1)     # the cutting list: the tray is cut this much wider
                    drip = _prism(tidy(zinc), 0.0, hi - lo, dict(place(), origin=frame_to_world(frame, ue, lo, 0.0)),
                                  "verge_strip", "%s Verge Drip" % m["name"], name)
                    drip.update(tray_sheet=True, of=m["name"], color=m.get("color", drip["color"]), verge_drip=True,
                                drip_runs=[tidy(list(LineString(path).buffer(t / 2.0, cap_style=2, join_style=2, mitre_limit=3.0).exterior.coords)[:-1])
                                           for path in (roll_path, drop_path)],
                                drip_under=[round(drop_path[0][0], 3), round(sign * drop_path[0][1], 3)])
                    drips.append(drip)
                extra.extend(drips)
                strip["verge_info"] = {"side": side, "type": kind, "length": round(v1 - v0, 1), "lap": 0.0, "nose": VERGE_TRAY,
                                       "drop": VERGE_BELOW, "clear": VERGE_CLEAR, "thickness": t, "sheet": round(sheet, 1),
                                       "drips": len(drips), "wide": elev.get("verge_last") == "full",
                                       "last_seam": None if seam is None else round(seam + VERGE_TRAY, 1)}
            extra.append(strip)
            info.setdefault("verges", []).append(dict(strip["verge_info"], u=round(u, 1)))
            a, b = sorted((ue - out * strip["verge_info"]["lap"], ue + out * (strip["verge_info"]["nose"] + t)))
            info.setdefault("verge_strips", []).append([[a, v0], [b, v0], [b, v1], [a, v1]])
    return meshes + extra


def _cut_end(m, cuts, at_start=True):
    """Cut the end of a length of drip (a section extruded along its frame's n) where it
    meets another: its end at its start (or, *at_start* False, at its far end) is cut by
    planes, each part of its section by its own: *cuts* is [(ring or None for the whole
    section, a point of the plane, its normal, a name)]. Returns the pieces that replace
    it: the length itself, cut back square to past the last crossing, and for each cut a
    short piece from its plane over that length's end, the same section carried on along
    the same line (so the two coincide where they overlap) but sheared so its own end
    lies in the plane (an oblique frame, "vshift": cladding_primitives.ring_at, and the
    viewer and the IFC brep the same). None where no plane crosses it."""
    dot = lambda a, b: sum(x * y for x, y in zip(a, b))                     # noqa: E731
    f = m["frame"]
    n = [float(c) for c in f["n"]]
    d0, length = float(m["depth"]), float(m["thickness"])
    e = n if at_start else [-c for c in n]
    base = d0 if at_start else d0 + length
    done, last = [], 0.0
    for ring, point, normal, label in cuts:
        ring = ring or m["profile"]
        pts = [frame_to_world(f, x, y, base) for x, y in ring]
        mv = [float(c) for c in normal]
        size = dot(mv, mv) ** 0.5
        mv = [c / size for c in mv]
        if dot(mv, e) < 0:
            mv = [-c for c in mv]
        em = dot(e, mv)
        if em < 0.05:
            return None
        ts = [dot([a - b for a, b in zip(point, q)], mv) / em for q in pts]
        if max(ts) <= 0.0 or min(ts) >= length:
            continue
        done.append((pts, ts, mv, em, label))
        last = max(last, max(ts))
    if not done:
        return None
    keep = dict(m)
    keep.pop("drip_runs", None)
    if at_start:
        keep["depth"] = d0 + last
    keep["thickness"] = length - last
    out = [keep] if keep["thickness"] > 0.5 else []
    for pts, ts, mv, em, label in done:
        on = [[q[i] + t * e[i] for i in range(3)] for q, t in zip(pts, ts)]
        u = [float(c) for c in f["u"]]
        u = [a - dot(u, mv) * b for a, b in zip(u, mv)]
        us = dot(u, u) ** 0.5
        u = [c / us for c in u]
        v = [mv[1] * u[2] - mv[2] * u[1], mv[2] * u[0] - mv[0] * u[2], mv[0] * u[1] - mv[1] * u[0]]
        o = on[0]
        flat = [(dot([a - b for a, b in zip(q, o)], u), dot([a - b for a, b in zip(q, o)], v)) for q in on]
        poly = orient(Polygon(flat), 1.0)
        if not poly.is_valid or poly.area < 1.0:
            continue
        ring = [[round(x, 4), round(y, 4)] for x, y in list(poly.exterior.coords)[:-1]]
        run = min(length, last - min(ts) + 10.0)
        bu, bv = dot(e, u) / em, dot(e, v) / em
        end = dict(m, profile=ring, holes=[], depth=0.0, thickness=round(run * em, 4),
                   frame={"origin": o, "u": u, "v": v, "n": mv}, name="%s %s End" % (m["name"], label),
                   vshift=[[[0.0, bu, 0.0, bv] for _q in ring]], drip_end=True,
                   of=m.get("of") or m["name"])               # one element with the length it ends (IFC)
        end.pop("drip_runs", None)
        end.pop("corner", None)
        out.append(end)
    return out


def join_drips(meshes, p):
    """Where an eaves meets a G3 verge, close the corner: the eaves drip runs on out to the
    outside of the verge's drip, and **the verge drip comes down the slope to the back of
    the eaves drip** (the inside face of its drop), cut on that face, so the two meet with
    no gap. Both are their trays' own sheet. Records nothing: the two lengths are longer."""
    dot = lambda a, b: sum(x * y for x, y in zip(a, b))                     # noqa: E731
    out_meshes = list(meshes)
    for elev in p.get("elevations", []):
        name, frame = elev.get("name"), elev["frame"]
        U, V = [float(c) for c in frame["u"]], [float(c) for c in frame_v(frame)]
        o = frame["origin"]
        uv = lambda q: (dot([a - b for a, b in zip(q, o)], U), dot([a - b for a, b in zip(q, o)], V))   # noqa: E731
        verge = [m for m in out_meshes if m.get("elevation") == name and m.get("verge_drip")]
        flashings = [m for m in out_meshes if m.get("elevation") == name and m.get("ifc_type") == "verge_strip"
                     and " Verge Flashing " in m.get("name", "") + " " and not m.get("drip_end")]
        clips = [m for m in out_meshes if m.get("elevation") == name and m.get("ifc_type") == "verge_strip"
                 and " Verge Clip " in m.get("name", "") + " " and not m.get("drip_end")]
        eaves = [m for m in out_meshes if m.get("elevation") == name and m.get("eaves_info") and m.get("tray_sheet")
                 and abs(dot(m["frame"]["n"], U)) > 0.99]
        if not (verge or flashings) or not eaves:
            continue
        N = [float(c) for c in frame["n"]]
        decks = [m for m in out_meshes if m.get("elevation") == name and m.get("ifc_type") == "deck"]
        for side in (-1.0, 1.0):
            mine = [m for m in verge if dot(m["frame"]["u"], U) * side > 0]
            extras = []
            if mine:
                low = min(mine, key=lambda m: uv(m["frame"]["origin"])[1])
                runs = low.get("drip_runs") or [None, None]
            else:
                # a verge with cladding (ZI-05): its flashing, split at the plywood's underside into
                # the part over the edge (mitred to the eaves bullnose) and the drop (down to the
                # back of the eaves drip's drop), and the clip behind it cut with the drop
                mine = [m for m in flashings if dot(m["frame"]["u"], U) * side > 0]
                if not mine:
                    continue
                low = min(mine, key=lambda m: uv(m["frame"]["origin"])[1])
                sgn = 1.0 if dot(low["frame"]["v"], N) > 0 else -1.0
                soffit = min((float(m["depth"]) for m in decks), default=0.0)
                poly = Polygon(low["profile"])
                xs = [x for x, _y in low["profile"]]
                big = 1.0e4
                upper = Polygon([(min(xs) - 1, sgn * soffit), (max(xs) + 1, sgn * soffit), (max(xs) + 1, sgn * big), (min(xs) - 1, sgn * big)])
                parts = [poly.intersection(upper), poly.difference(upper)]
                runs = []
                for g in parts:
                    g = max(getattr(g, "geoms", [g]), key=lambda q: q.area) if not g.is_empty else None
                    runs.append([[round(x, 4), round(y, 4)] for x, y in list(orient(g, 1.0).exterior.coords)[:-1]] if g is not None and g.area > 1.0 else None)
                if not all(runs):
                    continue
                low = dict(low, drip_runs=runs)
                i = out_meshes.index(next(m for m in mine if m["name"] == low["name"]))
                out_meshes[i] = low
                extras = [m for m in clips if dot(m["frame"]["u"], U) * side > 0]
            ue, v_lo = uv(low["frame"]["origin"])
            reach = max(x for x, _y in low["profile"])                     # the verge drip's outside, out from the roof's edge
            # the eaves drip nearest this verge, and which of its ends is there
            def end_u(m):
                a = uv(m["frame"]["origin"])[0]
                b = a + float(m["thickness"]) * (1.0 if dot(m["frame"]["n"], U) > 0 else -1.0)
                return a, b
            near = min(eaves, key=lambda m: min(abs(x - ue) for x in end_u(m)))
            a, b = end_u(near)
            if min(abs(a - ue), abs(b - ue)) > VERGE_TRAY + 60.0:
                continue                                                   # this eaves does not reach the verge
            # run the eaves drip on out to the outside of the verge's drip
            target = ue + side * (reach + 1.0)
            fwd = dot(near["frame"]["n"], U) > 0
            at_start = (side < 0) == fwd                                   # the verge is at the eaves drip's start
            if at_start:
                grow = abs(min(a, b) - target) if side < 0 else abs(target - max(a, b))
                near["frame"] = dict(near["frame"], origin=[c - grow * k for c, k in zip(near["frame"]["origin"], near["frame"]["n"])])
                near["thickness"] = float(near["thickness"]) + grow
            else:
                near["thickness"] = float(near["thickness"]) + (abs(target - max(a, b)) if side > 0 else abs(min(a, b) - target))
            t = float(p["seam_t"])
            # the back of the eaves drip's drop: a plane along the eaves, plumb, through the drop's inside face
            ux, uy = near["drip_under"]
            under = frame_to_world(near["frame"], ux, uy, float(near["depth"]))
            up = [V[0], V[1], 0.0]
            hs = dot(up, up) ** 0.5
            up = [c / hs for c in up]                                      # level, up the slope
            back = [c + t / 2.0 * k for c, k in zip(under, up)]
            # the mitre of the two bullnoses: square to the roof, through the corner of their outsides
            e_reach = max(x for x, _y in near["profile"])                   # the eaves drip's outside, down the slope from its line
            e_line = uv(frame_to_world(near["frame"], 0.0, 0.0, float(near["depth"])))[1]
            corner = frame_to_world(frame, ue + side * reach, e_line - e_reach, 0.0)
            mitre = [side * U[i] + V[i] for i in range(3)]
            # the outside of the verge drip's drop: where the eaves drip's drop stops
            v_drop = max(x for x, _y in runs[1]) if runs and runs[1] else VERGE_CLEAR
            stop = frame_to_world(frame, ue + side * v_drop, 0.0, 0.0)
            # bring the verge drip down past the eaves, then cut both at the corner
            v_start = uv(back)[1] - 200.0
            for piece in [low] + extras:
                pu, pv = uv(piece["frame"]["origin"])
                if v_start < pv:
                    piece["frame"] = dict(piece["frame"], origin=frame_to_world(frame, pu, v_start, 0.0))
                    piece["thickness"] = float(piece["thickness"]) + (pv - v_start)
            for piece, cuts, first in [
                    (low, [(runs[0], corner, mitre, "Bullnose"), (runs[1], back, up, "Drop")], True),
                    (near, [((near.get("drip_runs") or [None, None])[0], corner, mitre, "Bullnose"),
                            ((near.get("drip_runs") or [None, None])[1], stop, U, "Drop")], at_start)] + [
                    (x, [(None, back, up, "Drop")], True) for x in extras]:
                pieces = _cut_end(piece, cuts, at_start=first)
                if pieces:
                    i = out_meshes.index(piece)
                    out_meshes[i:i + 1] = pieces
    return out_meshes


def _up_to_plane(m, point, normal, frame, r):
    """Carry the top end of one verge piece up to a cap's bisecting plane and cut it there.
    A section run up the slope (frame n along the face's v: drip, support, flashing, clip)
    is lengthened past the plane and cut on it (_cut_end); a sheet set in the face's own
    frame (an edge seam's) has its top moved to the plane at its mid-depth and sheared with
    depth, as ridge_ends cuts the trays. Returns the pieces that replace it."""
    V = [float(c) for c in frame_v(frame)]
    n = [float(c) for c in m["frame"]["n"]]
    if abs(sum(a * b for a, b in zip(n, V))) > 0.99:
        m["thickness"] = float(m["thickness"]) + 600.0
        return _cut_end(m, [(None, point, normal, "Ridge")], at_start=False) or [m]
    top = max(q[1] for q in m["profile"])
    mid = float(m["depth"]) + float(m["thickness"]) / 2.0
    old = m.get("vshift") or []
    row = []
    was = old[0] if old and old[0] else [[0.0] * 4] * len(m["profile"])
    for q, sh in zip(m["profile"], was):
        if q[1] >= top - 0.5:
            q[1] = round(r["v"] + r["k"] * mid, 3)
            row.append([sh[0], sh[1], sh[2] - r["k"] * mid, sh[3] + r["k"]])
        else:
            row.append(list(sh))
    m["vshift"] = [row] + list(old[1:])
    return [m]


def close_cap_ends(meshes, p):
    """Where a ridge cap or a head verge cap ends at a verge, close it (VMZINC's stop end):

      * **each verge runs on up to the cap's bisecting plane** and is cut on it: the G3
        drip and its support, or the verge with cladding's flashing, clip and edge seam, so
        the two slopes' verges meet at the apex (at a head verge, the verge meets the plane
        under the cap's corner) and no V is left open between them;
      * **the cap runs on out over the verge**, to the outside of whatever the verge puts
        beyond the roof's edge;
      * **a stop end closes the cap's end**: the cap's own section, filled, one sheet thick,
        in the plane of its end, part of the cap (one element in the IFC).

    The bisecting plane in a face's own terms is v = r.v + k·s (s the depth): the ridge's
    (cladding_ridge.ridge_of), or a head verge's, cut as half a ridge."""
    dot = lambda a, b: sum(x * y for x, y in zip(a, b))                     # noqa: E731
    by = {e.get("name"): e for e in p.get("elevations", [])}
    out = list(meshes)
    caps = [m for m in out if m.get("ifc_type") == "ridge_cap" and (m.get("ridge_info") or m.get("head_info"))
            and not m.get("stop_end")]
    for cap in caps:
        info = cap.get("ridge_info") or {}
        faces = info.get("between") or [cap.get("elevation")]
        f = cap["frame"]
        n = [float(c) for c in f["n"]]
        o = [float(c) for c in f["origin"]]
        length = float(cap["thickness"])
        t = float(info.get("thickness") or (cap.get("head_info") or {}).get("thickness") or p.get("seam_t") or 0.7)
        along = lambda q: dot([a - b for a, b in zip(q, o)], n)            # noqa: E731
        reach = {0: 0.0, 1: 0.0}
        met = {0: False, 1: False}
        letters = {}
        for name in faces:
            elev = by.get(name)
            r = ridge_of(elev) if elev else None
            if not r:
                continue
            frame = elev["frame"]
            V, N = [float(c) for c in frame_v(frame)], [float(c) for c in frame["n"]]
            normal = [V[i] - r["k"] * N[i] for i in range(3)]               # the plane v = r.v + k·s
            point = frame_to_world(frame, r["u0"], r["v"], 0.0)
            pieces = [m for m in out if m.get("elevation") == name
                      and (m.get("verge_drip") or m.get("verge_support") or m.get("verge_seam")
                           or (m.get("ifc_type") == "verge_strip" and re_flash(m.get("name", ""))))]
            for end in (0, 1):
                here = []
                for m in pieces:
                    pts = [frame_to_world(m["frame"], x, y, d) for x, y in m["profile"]
                           for d in (float(m["depth"]), float(m["depth"]) + float(m["thickness"]))]
                    if max(dot([a - b for a, b in zip(q, frame["origin"])], V) for q in pts) < r["v"] - 800.0:
                        continue                                            # a length lower down the verge (at the eaves, say)
                    s = [along(q) for q in pts]
                    if (end == 0 and max(s) < length / 2.0) or (end == 1 and min(s) > length / 2.0):
                        here.append((m, s, pts))
                if not here:
                    continue
                met[end] = True
                if name == faces[0] and end not in letters:
                    for m, _s, _p in here:
                        tail = m.get("name", "").split()
                        if m.get("verge_support") or re_flash(m.get("name", "")):
                            letters[end] = tail[-1] if tail[-1] in ("L", "R") else tail[-2] if len(tail) > 1 and tail[-2] in ("L", "R") else letters.get(end)
                reach[end] = max(reach[end], max(max(-min(s), 0.0) if end == 0 else max(max(s) - length, 0.0) for _m, s, _p in here))
                # the top-most of each kind of piece is carried up to the plane
                tops = {}
                for m, s, pts in here:
                    key = ("drip" if m.get("verge_drip") else "support" if m.get("verge_support")
                           else "seam:" + m.get("name", "") if m.get("verge_seam") else "strip:" + m.get("name", ""))
                    hi = max(dot([a - b for a, b in zip(q, frame["origin"])], V) for q in pts)
                    if key not in tops or hi > tops[key][1]:
                        tops[key] = (m, hi)
                for m, hi in tops.values():
                    if hi < r["v"] - 600.0 or m.get("drip_end"):
                        continue                                            # not the length that reaches the ridge
                    i = out.index(m)
                    out[i:i + 1] = _up_to_plane(m, point, normal, frame, r)
        if not (met[0] or met[1]):
            continue
        # the cap over the verge, and its ends closed
        grow0 = reach[0] + t if met[0] else 0.0
        grow1 = reach[1] + t if met[1] else 0.0
        cap["frame"] = dict(f, origin=[c - grow0 * k for c, k in zip(o, n)])
        cap["thickness"] = length + grow0 + grow1
        hull = Polygon(cap["profile"]).convex_hull
        ring = [[round(x, 3), round(y, 3)] for x, y in list(orient(hull, 1.0).exterior.coords)[:-1]]
        for end in (0, 1):
            if not met[end]:
                continue
            letter = letters.get(end) or ("L" if end == 0 else "R")
            at = 0.0 if end == 0 else float(cap["thickness"]) - t
            stop = _prism(ring, at, t, dict(cap["frame"]), "ridge_cap", "%s Stop End %s" % (cap["name"].replace(" Cap", ""), letter), cap.get("elevation"))
            stop.update(tray_sheet=True, of=cap["name"], stop_end=True, color=cap.get("color", stop["color"]))
            out.append(stop)
        cap.setdefault("ridge_info" if cap.get("ridge_info") else "head_info", {})["stop_ends"] = [k for k in (0, 1) if met[k]]
    return out


def re_flash(name):
    return " Verge Flashing " in name + " " or " Verge Clip " in name + " "
