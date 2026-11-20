"""CladForge — the validation table from the requirements. Not a compliance check."""

import math

from cladding_constants import (_parse, PLANK_SPAN_TABLE, DECK_SPAN, MIN_CAVITY, FIXING_EMBEDMENT,
                                MAX_BATTEN_SPAN)
from cladding_primitives import ROW_MIN
from cladding_corners import PANEL_SIZES as PROFILE_SIZES, TRIM_SIZE, TRIM_LEG, FLANGE_P
from cladding_verge import VERGE_SEAM
from cladding_valley import VALLEY_MIN_PITCH

MIN_CLOSING_CUT = 100.0   # mm – narrower closing pieces are hard to fix and look wrong
SEAM_MIN_PITCH = 3.0      # deg – below this a double-lock standing seam is generally not used at all
SEAM_LOW_PITCH = 7.0      # deg – and below this its seams are generally sealed
WALL_PITCH = 89.0         # deg – a face steeper than this is a wall (fabric_extract stands it up)
ROOF_PITCH = 75.0         # deg – a face shallower than this, facing the sky, takes the weather as a roof does


def _pitched(elevations):
    """(name, pitch in degrees from level, faces down) for every face that is not a wall."""
    return [(e.get("name") or "Elevation", float(e["pitch"]), bool(e.get("faces_down")))
            for e in elevations if e.get("pitch") is not None and float(e["pitch"]) <= WALL_PITCH]


def _pitch_checks(p, add):
    """What a pitched face asks of the build-up. The build-up is a wall's, laid on the
    slope, so these say where a roof wants more than it gives."""
    for name, pitch, down in _pitched(p["elevations"]):
        lie = "level" if pitch < 0.3 else "pitched %.1f°" % pitch
        if down:
            add("Pitch", "pass", "%s is %s and faces down: it sheds no water, so it is set out as a wall is" % (name, lie), pitch)
            continue
        if p["seam"]:
            if p["plank_orient"] != "vertical":
                add("Pitch", "fail", "%s is %s and its trays run across the slope: standing seams run with the fall, "
                    "or they dam the water. Turn the trays to run up the slope" % (name, lie), pitch)
            elif pitch < SEAM_MIN_PITCH:
                add("Pitch", "fail", "%s is %s: below the %.0f° generally given as the least for a double-lock standing "
                    "seam" % (name, lie, SEAM_MIN_PITCH), pitch)
            elif pitch < SEAM_LOW_PITCH:
                add("Pitch", "warn", "%s is %s: between %.0f° and %.0f° standing seams are generally sealed. Confirm the "
                    "seam and the least pitch with the manufacturer" % (name, lie, SEAM_MIN_PITCH, SEAM_LOW_PITCH), pitch)
            else:
                add("Pitch", "pass", "%s is %s: the trays and their seams run up the slope" % (name, lie), pitch)
        elif pitch < ROOF_PITCH:
            add("Pitch", "warn", "%s is %s and faces the sky: open-jointed %s let the rain through, so the layer behind "
                "them has to be the roof's waterproofing, and the battens and fixings chosen for a roof"
                % (name, lie, "panels" if p["cladding_type"] == "panel" else "boards"), pitch)
        else:
            add("Pitch", "pass", "%s leans back, %s: set out up the slope as a wall is" % (name, lie), pitch)


def check_rules(params, infos=None):
    """Pass the *infos* from generate_preview to avoid rebuilding the geometry."""
    p, checks = _parse(params), []
    add = lambda name, status, message, value=None: checks.append(  # noqa: E731
        {"name": name, "status": status, "message": message, "value": value})

    if p["seam"]:
        # Trays lie on a continuous deck, so it is the deck that spans the battens.
        max_span = DECK_SPAN[p["seam_deck"]]
        c = p["batten_centres"]
        status = "pass" if c <= max_span + 0.5 else "fail"
        add("Deck span", status, "Batten centres %.0fmm vs %.0fmm max span for the %.0fmm %s deck%s"
            % (c, max_span, p["deck_t"], "WBP plywood" if p["seam_deck"] == "ply" else "softwood boarding",
               " — exceeds max span" if status == "fail" else ""), c)
        if p["seam_full"]:
            # one piece a run: how long that can be is the metal's and the manufacturer's call
            run = max([float(e.get("height" if p["plank_orient"] == "vertical" else "width") or 0.0)
                       for e in p["elevations"]] or [0.0])
            add("Tray length", "warn" if run > 10000 else "pass",
                "Full-length trays, no cross joints: up to %.1f m in one piece, cut from the coil%s"
                % (run / 1000.0, " — confirm the longest tray for the metal with the manufacturer (thermal movement, handling)"
                   if run > 10000 else ""), run)
    elif p["cladding_type"] == "plank":
        max_span = max([400.0] + [span for min_t, span in PLANK_SPAN_TABLE if p["plank_t"] >= min_t])
        c = p["batten_centres"]
        status = "pass" if c < max_span - 0.5 else ("warn" if c <= max_span + 0.5 else "fail")
        add("Batten centres", status, "Batten centres %.0fmm vs %.0fmm max span for %.0fmm planks%s"
            % (c, max_span, p["plank_t"], " — exceeds max span" if status == "fail" else ""), c)
    else:
        add("Panel joints", "pass", "Panel joints land on battens at %.0fmm centres (locked, max %.0f)"
            % (p["batten_centres"], MAX_BATTEN_SPAN), p["batten_centres"])
        add("Panel size", "pass", "Panels %.0f x %.0fmm within max sheet size" % (p["panel_w"], p["panel_h"]))
        add("Panel seams", "pass" if p["has_cb"] else "warn",
            "Noggins fitted behind the horizontal seams: the counter-batten layer keeps the drainage plane clear"
            if p["has_cb"] else
            "Horizontal seams left unsupported — a noggin between vertical battens would dam the cavity. "
            "Add counter-battens to support them, or use a proprietary horizontal joint profile")

    s = p["splash"]
    status = "pass" if s >= 150 else ("warn" if s >= 100 else "fail")
    # On a pitched face the splash zone, like every height, is measured up the slope.
    slope = ["%.0fmm of height on %s" % (s * math.sin(math.radians(pitch)), name)
             for name, pitch, _down in _pitched(p["elevations"]) if pitch >= 0.3]
    add("Splash zone", status, "Splash zone %.0fmm%s%s" % (
        s, "" if status == "pass" else " — below the 150mm recommended minimum",
        (" (measured up the slope: " + ", ".join(slope) + ")") if slope and s else ""), s)
    _pitch_checks(p, add)

    if p["insulation"]:
        first = p["cb_d"] if p["has_cb"] else p["batten_d"]
        need = p["insulation_t"] + FIXING_EMBEDMENT
        status = "pass" if first >= need else "fail"
        add("Fixing through insulation", status, "First batten layer %.0fmm deep vs %.0fmm insulation + %.0fmm%s"
            % (first, p["insulation_t"], FIXING_EMBEDMENT, "" if status == "pass" else " — insufficient embedment"), first)

    cavity = p["batten_d"] if p["battens"] == "vertical" else (p["cb_d"] if p["has_cb"] else 0.0)
    status = "pass" if cavity >= MIN_CAVITY else "fail"
    add("Cavity depth", status, "Drained cavity %.0fmm%s" % (cavity, "" if status == "pass" else " — below %.0fmm minimum" % MIN_CAVITY), cavity)

    if p["battens"] == "horizontal" and not p["has_cb"]:
        add("Buildup", "fail", "Horizontal battens without counter-battens block the cavity drainage")
    elif p["has_cb"] and not p["cb_derived"]:
        add("Buildup", "warn", "Counter-battens forced on: horizontal counter-battens interrupt the drained cavity")
    else:
        add("Buildup", "pass", "%s battens%s support %s boards" % (
            p["battens"].title(), " on vertical counter-battens" if p["has_cb"] else "", p["boards_run"]))

    if infos is None:
        from cladding_preview import _build_all
        infos = _build_all(p)[2]
    for i in infos:
        if i.get("set_out_from_openings"):
            add("Setting-out", "pass", "%s: %d opening(s) drive the setting-out; panel edges land on the "
                "jambs and the widest bay is %.0fmm" % (i["elevation"], i.get("openings", 0),
                                                        max(i.get("panel_widths") or [0])))
        cuts = [c for c in (i.get("closing_cut_left", 0), i.get("closing_cut_right", 0),
                            i.get("min_panel", 0)) if 0 < c < MIN_CLOSING_CUT]
        if i.get("short_rows"):
            add("Panel rows", "warn", "%s: row %s asked for less than %.0fmm — raised to %.0fmm, the "
                "shortest row that can be fixed" % (i["elevation"], ", ".join(str(r + 1) for r in i["short_rows"]),
                                                    ROW_MIN, ROW_MIN), len(i["short_rows"]))
        top = i.get("closing_cut_top", 0) if i.get("rows") else 0
        if 0 < top < MIN_CLOSING_CUT:
            add("Panel rows", "warn", "%s: the closing row at the top is only %.0fmm — change a row height "
                "so it is at least %.0fmm" % (i["elevation"], top, MIN_CLOSING_CUT), top)
        if cuts:
            add("Closing cut", "warn", "%s: a panel only %.0fmm wide is narrower than %.0fmm — shift the "
                "setting-out or move a joint" % (i["elevation"], min(cuts), MIN_CLOSING_CUT), min(cuts))
    # A ridge is reported once, by the slope that carries its cap.
    for i in infos:
        r = i.get("ridge")
        if r and r.get("master"):
            what = "%s and %s meet at a ridge: a vented cap %.2f m long, %.0f mm down each slope, %.0f mm clear between " \
                   "the two slopes' cladding under it" % (r["between"][0], r["between"][1], r["length"] / 1000.0, r["leg"], r["gap"])
            room = r.get("clip_room")
            if not p["seam"]:
                add("Ridge", "warn", what + ". The cap is a standing seam detail: agree the ridge for %s with the supplier"
                    % ("panels" if p["cladding_type"] == "panel" else "boards"), r["length"])
            elif room is not None and room < 79.5:
                add("Ridge", "warn", what + ". At this pitch the decompression strip has to sit lower to stay 10 mm off the "
                    "trays' turned-up ends, leaving %.0f mm for the ridge clip where the detail shows 80: confirm with the "
                    "manufacturer" % room, room)
            else:
                add("Ridge", "pass", what + ", the trays turned up 15 mm. The ridge clips, the insect mesh and laps between "
                    "lengths of cap are not drawn", r["length"])
    # A valley is reported once, by the slope that carries its gutter.
    for i in infos:
        for v in i.get("valleys") or []:
            if not v.get("master"):
                continue
            what = "%s and %s meet at a valley %.2f m long: the cladding stands %.0f mm back from the valley line on each " \
                   "slope, %.0f mm clear across it, over a valley gutter that runs %.0f mm on under it" % (
                       i["elevation"], v["with"], v["length"] / 1000.0, v["back"], v["clear"], v["lap"])
            low = [a for a in v["pitches"] if a <= VALLEY_MIN_PITCH]
            if not p["seam"]:
                add("Valley", "warn", what + ". The gutter is a standing seam detail: agree the valley for %s with the supplier"
                    % ("panels" if p["cladding_type"] == "panel" else "boards"), v["length"])
            elif low:
                add("Valley", "warn", what + ". A slope here is pitched %.1f°: the detail is for slopes over %.0f°"
                    % (min(low), VALLEY_MIN_PITCH), min(low))
            else:
                add("Valley", "pass", what + ". That overlap is the detail's R: confirm it against the installation guide. "
                    "The welt strip, the sheet clips and a water flow controller are not drawn", v["length"])
    # A hip is reported once, by the slope that carries its cap; seams too far apart along it, by each slope.
    for i in infos:
        for h in i.get("hips") or []:
            spacing, limit = h.get("seam_spacing"), h["seam_limit"]
            wide = spacing is not None and spacing > limit + 0.5
            apart = "%s: the seams are %.0f mm apart along the hip with %s; the detail allows at most %.0f mm between two " \
                    "standing seams at a hip. Use the narrower tray, or agree it with the manufacturer" % (
                        i["elevation"], spacing or 0.0, h["with"], limit)
            if not h.get("master"):
                if wide:
                    add("Hip", "warn", apart, spacing)
                continue
            what = "%s and %s meet at a hip %.2f m long: a cap %.0f mm down each slope, %.0f mm clear between the two " \
                   "slopes' cladding under it" % (i["elevation"], h["with"], h["length"] / 1000.0, h["leg"], h["gap"])
            if not p["seam"]:
                add("Hip", "warn", what + ". The cap is a standing seam detail: agree the hip for %s with the supplier"
                    % ("panels" if p["cladding_type"] == "panel" else "boards"), h["length"])
                continue
            add("Hip", "pass", what + (", the trays turned up 15 mm along their raking ends and the seams %.0f mm apart along "
                                       "it (%.0f at most)" % (spacing, limit) if spacing is not None and not wide else "")
                + ". The decompression strips and the ridge clips are not drawn", h["length"])
            if wide:
                add("Hip", "warn", apart, spacing)
    # A raking corner is reported once, by its master.
    for i in infos:
        for r in i.get("rakes") or []:
            if not r.get("master"):
                continue
            what = "%s and %s meet at a raking corner %.2f m long (%s, %.0f°)" % (
                i["elevation"], r["with"], r["length"] / 1000.0, "external" if r["external"] else "internal", r["angle"])
            if r["detail"] == "profile":
                add("Raking corner", "pass", what + ": the boards stop square against a corner profile run up the corner", r["length"])
            else:
                said = {"lap": "Master is", "butt": "Square is", "profile": "Profile is for external right-angled corners in boards or panels and is"}.get(r["asked"])
                if r["asked"] == "profile" and not r.get("match", True):
                    said = "The two faces are not clad alike, so a corner profile is"
                tail = ". %s not drawn on this raking corner: it is mitred" % said if said else ""
                add("Raking corner", "warn" if said or p["seam"] else "pass",
                    what + ": every layer is cut to the plane that bisects it, the boards %.0f mm apart across the mitre" % r["gap"] + tail
                    + (". Agree a standing seam corner on a raking line with the supplier" if p["seam"] else ""), r["length"])
    for i in infos:
        eaves = i.get("eaves") or []
        if eaves:
            over = sorted({e["over"] for e in eaves if e.get("over")})
            add("Eaves", "pass", "%s: an eaves %.2f m long. The plywood and the trays run on %.0f mm past the drip's line; the trays' own sheet "
                "turns round the plywood's end, comes back under it and drops plumb over a support profile, hooked under at its foot, "
                "%.0f mm below the roof's edge (each tray is cut %.0f mm longer for it, and the cutting list has it so); the seams are "
                "folded back at 45 degrees%s. Its sizes are assumed: agree them against the supplier's "
                "eaves detail. Switch it off (Edges, Eaves drip) where the slope runs into a wall or a gutter behind a parapet"
                % (i["elevation"], sum(e["length"] for e in eaves) / 1000.0, eaves[0]["nose"], min(e["drop"] for e in eaves), max(e["sheet"] for e in eaves),
                   "; over %s, which it comes down to with no overhang, the roof runs on %.0f mm so the drip stands clear of the wall's "
                   "cladding and laps it" % (" and ".join(over), max(e.get("run") or 0.0 for e in eaves)) if over else ""), len(eaves))
        for u in i.get("eaves_under") or []:
            add("Eaves", "pass", "%s: under the eaves of %s (no overhang) for %.2f m. Every layer stops level, %.0f mm below the line the two "
                "meet on: the %.0f mm top offset under the roof's plywood where that is lowest, at the outside of this wall's cladding. "
                "The gap is the wall cavity's vent: an insect mesh closes it (not drawn)"
                % (i["elevation"], u["with"], u["length"] / 1000.0, -u["stop"], u["gap"]), u["length"])
    # A verge's last seam stands 150 to 370 mm in from the tray's edge.
    for i in infos:
        verges = i.get("verges") or []
        if not verges:
            continue
        lo, hi = VERGE_SEAM
        side = {"l": "left", "r": "right"}
        off = [v for v in verges if v.get("last_seam") is not None and not (lo - 0.5 <= v["last_seam"] <= hi + 0.5)]
        if off:
            add("Verge", "warn", "%s: the last seam is %s from the tray's edge at the verge; the detail takes %.0f to %.0f mm. "
                "Move the setting-out (the offset) or change the tray width" % (
                    i["elevation"], " and ".join("%.0f mm (%s)" % (v["last_seam"], side[v["side"]]) for v in off), lo, hi),
                off[0]["last_seam"])
        else:
            add("Verge", "pass", "%s: a verge strip on the %s, the last seam %s from the tray's edge (%.0f to %.0f mm)" % (
                i["elevation"], " and ".join(side[v["side"]] for v in verges),
                " and ".join("%.0f mm" % v["last_seam"] for v in verges if v.get("last_seam") is not None) or "none", lo, hi),
                len(verges))
    # Each corner end carries its own detail, so count the ends per detail.
    per = {}
    for c in (i.get("corner") or {} for i in infos):
        for side, detail in zip(("left", "right"), c.get("details") or (c.get("detail"),) * 2):
            if any(c.get(side) or ()):
                per[detail] = per.get(detail, 0) + 1
    if per:
        add("Corners", "pass", "; ".join("%d corner end(s), %s" % (n, {
            "mitre": "mitred: the whole buildup wraps on the bisector plane",
            "lap": "a seam stood on the corner: one face's trays turn up, the other's run on past as the second "
                   "upstand, double-locked on the face that carries it" if p["seam"]
                   else "master: one board masters the corner and the other stops a joint gap clear of it",
            "profile": "a corner flashing strip over the trays, which meet on the bisector (detail to be agreed with "
                       "the roofing contractor)" if p["seam"]
                       else "profiled: an aluminium outer corner profile, the panels butting its nose" if p["cladding_type"] == "panel"
                       else "profiled: a Marley-type external corner profile, a %.0f mm nose with %.0f mm flanges flush "
                       "with the planks' backs, the planks butting its nose" % (TRIM_SIZE, FLANGE_P),
            "butt": "square: both boards stop at the wall corner"}[d]) for d, n in sorted(per.items())))
    # Corner profiles: the flanges want a batten under them, and the profile is sized to
    # the panel, so a panel thickness it is not made for needs the manufacturer's word.
    for i in infos:
        for w in i.get("profile_warnings") or []:
            add("Corner profile", "warn", w)
    if p["cladding_type"] == "panel" and any(i.get("profiles") for i in infos) and p["panel_t"] not in PROFILE_SIZES:
        add("Corner profile", "warn", "Panels are %.0f mm thick: confirm the profile size for this panel thickness "
            "with the manufacturer (the profile suits 6, 8 or 10 mm)" % p["panel_t"], p["panel_t"])
    if p["cladding_type"] == "plank" and not p["seam"] and any(i.get("profiles") for i in infos) \
            and p["plank_t"] > TRIM_LEG:
        add("Corner profile", "warn", "Planks are %.0f mm thick: the corner profile's nose stands %.0f mm out from the "
            "planks' backs, so they would stand proud of it; choose a profile made for this plank" %
            (p["plank_t"], TRIM_LEG), p["plank_t"])
    # A cut panel has to come out of a stock board, turned if rotation is allowed.
    if p["cladding_type"] == "panel":
        trim = 2 * p.get("board_trim", 0.0)                 # squared-up edges come off first
        bw, bh = p["board_w"] - trim, p["board_h"] - trim
        fits = lambda w, h: (w <= bw and h <= bh) or (p["rotate"] and w <= bh and h <= bw)  # noqa: E731
        big = sorted({(w, h) for i in infos for w in (i.get("panel_widths") or []) for h in (i.get("rows") or [])
                      if not fits(w, h)}
                     | {tuple(q["size"]) for i in infos for q in (i.get("panel_joints") or []) if not fits(*q["size"])})
        if big:
            add("Board size", "fail", "%d cut size(s) larger than the %.0f x %.0f board%s either way round, e.g. %.0f x %.0f"
                % (len(big), bw, bh, " left after edge trim" if trim else "", big[0][0], big[0][1]), len(big))
    # Where each reveal lining runs back to: the window or door frame in the model, a
    # position typed on the opening, or the chain's default (cladding_edges.opening_frames).
    ops = [d for i in infos for d in (i.get("opening_details") or []) if "frame_from" in d]
    if ops and p.get("reveals", True):
        notes = [d["frame_note"] for d in ops if d.get("frame_note")]
        count = {k: sum(1 for d in ops if d["frame_from"] == k) for k in ("model", "set", "default")}
        add("Window frames", "warn" if notes else "pass",
            "; ".join(notes) if notes else "Linings run to the frame: %d from the model, %d set on the opening, %d at the "
            "chain's frame position" % (count["model"], count["set"], count["default"]), len(ops))
    # Openings closed with cavity barriers on all four sides: the battens over and under
    # each stop short, or the bays there would be shut pockets.
    closed = [i["barriers"] for i in infos if i.get("barriers")]
    if closed:
        n, cut = sum(b["openings"] for b in closed), sum(b["cut"] for b in closed)
        add("Cavity barriers", "pass" if cut else "warn",
            "%d opening%s closed with solid timber cavity barriers on all four sides, a damp proof membrane on each%s" % (
                n, "" if n == 1 else "s",
                "; %d batten%s over and under stop%s %.0f mm short of the head and sill barriers, so those bays still drain "
                "and vent sideways into the bays beside the opening" % (cut, "" if cut == 1 else "s", "s" if cut == 1 else "",
                                                                        max(b["gap"] for b in closed))
                if cut else ": no batten runs over or under them, so check the bays there are open to those beside"), n)
    bad = sum(i.get("unsupported_joints", 0) for i in infos)
    if bad:
        add("End joints", "warn", "%d board end joints fall between battens — shorten max length or adjust centres" % bad, bad)
    elif infos:
        add("End joints", "pass", "All board end joints land on a batten")
    return checks
