"""
CladForge — buildup and board setting-out for one extracted elevation.

Works entirely in the elevation's local frame: u runs along the wall, v runs
up it, depth runs outward from the wall face. Elements are emitted untrimmed
(full rectangles); cladding_booleans.py cuts them at openings and splash zones.
"""

import math

from cladding_constants import _prism, _rect, MAX_BATTEN_SPAN, EPDM_T, EPDM_LAP, SEAM_AIR, SEAM_HEAD_D, SEAM_STANDOFF, DPM_T
from cladding_booleans import clip_region, strip_intervals, iter_polygons, polygon_to_rings
from cladding_constants import frame_to_world, frame_v, z_to_v
from cladding_edges import (classify_edges, mitre_pullback, offset_region, opening_details, reveal_treatment,
                            opening_key, opening_frames, settings as edge_settings)
import cladding_corners as profiles
from cladding_primitives import (centred_positions, stacked_positions, batten_positions, dedupe,
                                 dedupe_priority, subdivide, panel_bays, bays_between, split_run,
                                 base_level, corner_ends, openings, buildup_depth, clip_bounds,
                                 clip_bounds_v, panel_rows, row_height, bays_on_posts, end_master)


def build_elevation(p, elev, layout=None):
    """Return (meshes, dims, info) for one elevation record.
    *layout* is (chain start, run length) measured along the cladding face."""
    name = elev.get("name", "Elevation")
    W, H = float(elev["width"]), float(elev["height"])
    frame = elev["frame"]
    # Chains: coursing is centred on the whole run and shifted by the chain offset,
    # so modules carry on around corners. Each elevation occupies [start, start + W]
    # of the run, reversed where the corner flips the u direction.
    lo, hi = clip_bounds(elev)            # the clad part of the face, cut back at corners
    v_lo, v_hi = clip_bounds_v(elev)      # and cut to the top and bottom picked for the chain
    start, run = layout or (0.0, hi - lo)
    along = run / 2.0 + float(elev.get("offset", 0.0)) - start
    centre = (hi - along) if elev.get("chain_reversed") else (lo + along)   # in this elevation's u
    offset = centre - W / 2.0
    # A plank course typed on a dimension overrides the parameter, for this elevation or
    # for every member of its chain — whichever scope was chosen. Panel rows are a list
    # carried on the elevation the same way (elev["panel_rows"]), read by _panels.
    over = {k: float(elev[k]) for k in ("cover",) if elev.get(k)}
    if over:
        p = dict(p, **over)
    meshes, dims = [], []
    info = {"elevation": name, "width": W, "height": H, "battens": p["battens"],
            "has_cb": p["has_cb"], "unsupported_joints": 0}
    depth = 0.0

    # Layers against the wall follow the region outline, openings included, and run
    # down past the splash zone.
    for key, present, t in (("sheathing", p["sheathing"], p["sheathing_t"]),
                            ("insulation", p["insulation"], p["insulation_t"])):
        if not present:
            continue
        for i, poly in enumerate(elev.get("polygons", [])):
            meshes.append(_prism(poly["exterior"], depth, t, frame, key,
                                 "%s %s %d" % (name, key.title(), i + 1), name,
                                 holes=poly.get("holes")))
        depth += t

    # Coursing runs between the picked levels: a picked bottom is where the boards
    # start, so it takes over from the splash zone above the elevation base.
    splash_v0 = base_level(elev, p["splash"])
    v0 = max(splash_v0, v_lo)
    H = min(H, v_hi)                      # coursing stops at the picked top
    info["base_level"] = v0
    info["clad_top"] = H                  # "height" stays the face, as "width" does
    bw, bd = p["batten_w"], p["batten_d"]

    outline = clip_region(elev, p["splash"])
    # Runs are set out on the area the boards are trimmed to: stood back from the edges.
    region = offset_region(elev, outline)
    layers = depth                       # depth of the face the cavity starts from
    if p["cladding_type"] == "panel":
        depth = _panels(p, elev, meshes, dims, info, depth, W, H, v0, offset, region)
    elif p["boards_run"] == "horizontal":
        depth = _horizontal_planks(p, elev, meshes, dims, info, depth, W, H, v0, offset, region)
    else:
        depth = _vertical_planks(p, elev, meshes, dims, info, depth, W, H, v0, offset, region)
    dims += _client_batten_dims(name, meshes, info, H)
    # Openings are closed and lined whatever the cladding is: the closer is solid
    # timber filling the cavity, so it does not depend on the board above it.
    board = p["panel_t"] if p["cladding_type"] == "panel" else p["plank_t"]
    holes = openings(elev)
    prof = {"strips": [], "warnings": [], "count": 0}     # corner profiles, for the DXF and checks
    edges = classify_edges(elev, outline)
    # Under standing seam the closer stops at the back of the deck, which runs to the jamb
    # over it: carried on to the trays it ran through the deck, and showed through them.
    over = (p["deck_t"] + SEAM_STANDOFF) if p["seam"] else 0.0
    _openings_extras(p, elev, meshes, holes, layers, depth - board - layers - over, depth, board, prof, v0)
    zones = barrier_zones(p, elev)
    if zones:
        info["barriers"] = _barriers(p, elev, meshes, zones, layers, depth - board - layers - over)
    info["openings"] = len(holes)
    frames = opening_frames(elev, holes, depth)
    info["opening_details"] = [dict(d, key=opening_key(r), rect=list(r), frame_depth=frames[r]["depth"],
                                    frame_from=frames[r]["from"], frame_name=frames[r]["name"],
                                    frame_note=frames[r]["note"])
                               for r, d in opening_details(elev, p)]

    # Each end takes its own corner's detail: a lap at one end and a mitre at the other
    # treat the layers behind the boards differently, so the ends are applied apart.
    left, right, (d_left, d_right) = corner_ends(elev, p)
    # behind a standing seam's corner seam the deck and battens mitre, as behind a mitre
    behind = lambda d: "mitre" if p["seam"] and d == "lap" else d   # noqa: E731
    _apply_corner(meshes, {lo: left}, behind(d_left), skip=("reveal", "closer"))
    _apply_corner(meshes, {hi: right}, behind(d_right), skip=("reveal", "closer"))
    k_ends = (elev.get("corner_lo"), elev.get("corner_hi"))          # run order, like the details
    k_l, k_r = k_ends[::-1] if elev.get("chain_reversed") else k_ends
    ends = ((lo, left, d_left, "l", float(k_l or 0.0)), (hi, right, d_right, "r", float(k_r or 0.0)))
    if not p["seam"]:
        _reentrant_layers(p, elev, meshes, ends)
        _plank_ends(p, elev, meshes, ends, edge_settings(elev)["gap"])
    # A mitre leaves the chain's mitre gap between the two boards, straight across the
    # joint: each board's end is pulled back half of it. The layers behind still meet.
    gap = edge_settings(elev)["gap"]
    for u_end, (k, _ext), detail, side in ((lo, left, d_left, "l"), (hi, right, d_right, "r")):
        if detail != "mitre" or not k or gap <= 0:
            continue
        for m in meshes:
            c = m.get("corner") or {}
            if m["ifc_type"] in ("panel", "plank") and ("k_" + side) in c and abs(c.get("u_" + side, -1e9) - u_end) < 0.6:
                m["corner"] = dict(c, **{"ext_" + side: c.get("ext_" + side, 0.0) - mitre_pullback(gap, k)})
    if p["seam"]:
        _seam_corners(p, elev, meshes, info, depth, ((lo, left, d_left, "l"), (hi, right, d_right, "r")))
    else:
        _profiled_corners(p, elev, meshes, prof, edges, depth, ((lo, left, d_left, "l"), (hi, right, d_right, "r")))
    _across_corners(meshes, ((lo, left, "l"), (hi, right, "r")))
    if p["cladding_type"] == "panel":
        _gaskets(p, elev, meshes, depth - board)
    info["profile_strips"], info["profile_warnings"], info["profiles"] = prof["strips"], prof["warnings"], prof["count"]
    k_run = (elev.get("corner_lo"), elev.get("corner_hi"))          # run order, like the details
    k_left, k_right = k_run[::-1] if elev.get("chain_reversed") else k_run
    used = {d for d, k in ((d_left, k_left), (d_right, k_right)) if k}
    info["corner"] = {"detail": used.pop() if len(used) == 1 else ("mixed" if used else d_left),
                      "details": [d_left, d_right], "left": list(left), "right": list(right)}
    # The band at the foot: a splash zone above an abutment, or the level that was picked.
    # Typing over the splash sets the splash zone; over a picked base or the top, the
    # chain's picked level.
    if v0 > 0:
        splash = v0 <= splash_v0 + 0.5
        dims.append(_dim(name, [0, 0], [0, v0], ("Splash %.0f" if splash else "Base %.0f") % v0, 300, [-1, 0],
                         "splash" if splash else "level_base", p["splash"] if splash else v0))
    dims.append(_dim(name, [0, 0], [0, H], "Top %.0f" % H, 900, [-1, 0], "level_top", H))
    info["total_depth"] = depth
    info["edges"] = edges                             # coloured in the 2D view
    info["clad_area"] = round(outline.area / 1e6, 3)  # m², net of openings and splash zones
    info["n_boards"] = sum(1 for m in meshes if m["ifc_type"] in ("plank", "panel"))
    return meshes, dims, info


SEAM_FLASHING = 50.0     # mm – each leg of a standing seam's corner flashing strip


def _seam_end(p, elev, face, k, detail, side):
    """(k, ext) of a tray's end at an external chain corner: it sits at the corner line
    plus ext + k × depth. Square for a corner seam (the master's stops SEAM_AIR short of the
    other face's metal and turns up; the other's runs on to the master's metal), else on
    the bisector, pulled back half the mitre gap where it is left as a mitre."""
    back = face - p["plank_t"]
    if detail == "lap":
        return 0.0, (back - SEAM_AIR) if end_master(elev, side) else face
    return k, (-mitre_pullback(edge_settings(elev)["gap"], k) if detail == "mitre" else 0.0)


def _tray_reach(p, elev, depth):
    """How far the trays run past each end of the clad face, (left, right), at their backs:
    round an external chain corner, or out over a verge strip. The setting-out counts it,
    so the tray at the end is never wider than a tray. With them, the least and most that
    end tray may be, ((least, most) left, (least, most) right): at a verge the last seam
    stands 150 to 370 mm in from the tray's edge (cladding_verge.VERGE_SEAM); at a corner
    the tray is at least SEAM_MIN_CLOSE, and clear of the turn."""
    from cladding_verge import verge_sides, VERGE_TRAY, VERGE_SEAM
    left, right, (d_left, d_right) = corner_ends(elev, p)
    verges = verge_sides(elev, p)
    reach, limits = [], []
    for (k, _ext), detail, side in ((left, d_left, "l"), (right, d_right, "r")):
        if k and k > 0 and detail != "butt":
            k_end, ext = _seam_end(p, elev, depth + p["plank_t"], k, detail, side)
            reach.append(max(0.0, ext + k_end * depth))
            limits.append((max(SEAM_MIN_CLOSE, reach[-1] + 20.0), p["plank_w"]))
        elif side in verges and not k:
            reach.append(VERGE_TRAY)
            limits.append((VERGE_SEAM[0], min(VERGE_SEAM[1], p["plank_w"])))
        else:
            reach.append(0.0)
            limits.append((0.0, p["plank_w"]))
    return tuple(reach), tuple(limits)


def _seam_corners(p, elev, meshes, info, face, ends):
    """A standing seam chain's external right-angled corners, where the corner is not left
    mitred (the deck and battens behind mitre either way).

    Seam ("lap"): a seam stood on the corner like the one on a window's reveal corner. The
    face that masters the corner carries it: its trays stop SEAM_AIR short of the other
    face's metal and turn up, the other face's trays run on past the corner to this
    face's metal and on out as the seam's second upstand, and the two are locked in a
    double lock lying on the master face, its head turned away from the corner. Swapping
    the master puts the seam on the other face (left or right of the corner).

    Flashing ("profile"): the trays meet on the bisector as if mitred, and a flashing strip
    covers the corner, a leg SEAM_FLASHING wide on each face, mitred to the other's."""
    name, frame = elev["name"], elev["frame"]
    t = p["plank_t"]
    back = face - t                                     # the back of the metal, as depth
    gap = edge_settings(elev)["gap"]
    made = {"seam": 0, "flashing": 0}
    for u_end, (k, _ext), detail, side in ends:
        if not k or k < 0 or detail == "butt":
            continue
        toward = 1.0 if side == "r" else -1.0
        seam = detail == "lap"
        master = seam and end_master(elev, side)
        # The metal's end: square for a corner seam (this face's stops SEAM_AIR short of the
        # other's metal and turns up; the other's runs on to this face's metal), else on the
        # bisector, pulled back half the mitre gap where it is left as a mitre.
        k_end, ext = _seam_end(p, elev, face, k, detail, side)
        trays = []
        for m in meshes:
            c = m.get("corner") or {}
            if m["ifc_type"] in ("plank", "seam") and ("k_" + side) in c and abs(c["u_" + side] - u_end) < 0.6:
                m["corner"] = dict(c, **{"k_" + side: k_end, "ext_" + side: ext})   # ends on the corner: horizontal trays
                if m["ifc_type"] == "plank":
                    trays.append(m)
            elif m["ifc_type"] == "plank" and not m.get("holes") and not c:
                # Vertical trays are set out across the face and run on past its end: the one
                # that reaches the corner is cut there and given the same end.
                us, vs = [q[0] for q in m["profile"]], [q[1] for q in m["profile"]]
                near, far = (min(us), max(us)) if toward > 0 else (max(us), min(us))
                if (far - u_end) * toward < -0.5 or (near - u_end) * toward > -1.0:
                    continue
                lo_u, hi_u = sorted((near, u_end))
                m["profile"] = _rect(lo_u, min(vs), hi_u, max(vs))
                m["corner"] = {"k_" + side: k_end, "ext_" + side: ext, "u_" + side: u_end}
                trays.append(m)
        if not trays or detail == "mitre":
            continue
        if detail == "profile":
            v0 = min(q[1] for m in trays for q in m["profile"])
            v1 = max(q[1] for m in trays for q in m["profile"])
            a, b = sorted((u_end, u_end - toward * SEAM_FLASHING))
            leg = _prism(_rect(a, v0, b, v1), face, t, frame, "seam", "%s Corner Flashing" % name, name)
            leg["opacity"] = 1.0
            leg["corner"] = {"k_" + side: k, "ext_" + side: 0.0, "u_" + side: u_end}
            leg["flashing"] = True
            meshes.append(leg)
            made["flashing"] += 1
            continue
        if not master:
            continue
        line = u_end + toward * (back - SEAM_AIR / 2.0)
        for m in trays:
            vs = [q[1] for q in m["profile"]]
            n = len(meshes)
            _seam(p, meshes, frame, m["name"], name, face, True, line, min(vs), max(vs), mirror=toward > 0)
            for q in meshes[n:]:
                # past the wall's corner line on purpose: trimmed in height only, like a corner timber
                q["clip_v_at"] = sorted((u_end - toward * 2.0, u_end - toward * 0.5))
                q["corner_seam"] = True
            made["seam"] += 1
    info["corner_seams"], info["corner_flashings"] = made["seam"], made["flashing"]


def _profiled_corners(p, elev, meshes, prof, edges, face, ends):
    """At a chain corner set to Profile the panels stop the nose (D, the panel thickness)
    and the chain's profile gap short of the outer corner line, square. The battens meet
    in a solid L of timber (_corner_timbers); the layers behind them stay mitred. The face
    before the corner in the run carries the profile itself, over the clad height of the
    corner less the top and bottom offsets; both faces show its strip, and where there is
    no L to carry them, check their flange lies over a batten.

    With planks it is the Marley-type profile (cladding_corners.trim_section): its flanges
    lie on the batten face, flush with the planks' backs, the nose's outer faces TRIM_LEG
    out from there; the planks stop TRIM_BACK and the profile gap short of where the two
    batten faces meet, square, against the side of the nose. The same L of timber goes
    behind it, right up to the flanges."""
    name, frame = elev["name"], elev["frame"]
    plank = p["cladding_type"] != "panel"
    D = p["plank_t"] if plank else p["panel_t"]
    kind = "plank" if plank else "panel"
    s = edge_settings(elev)
    for u_end, (k, _ext), detail, side in ends:
        if detail != "profile":
            continue
        # where the boards stop, along u from the wall's corner line
        stop = k * (face - D) - profiles.TRIM_BACK - s["pgap"] if plank else k * face - D - s["pgap"]
        toward = 1.0 if side == "r" else -1.0
        limit = u_end + toward * stop
        for m in list(meshes):
            c = m.get("corner") or {}
            if m["ifc_type"] == kind and ("k_" + side) in c and abs(c.get("u_" + side, -1e9) - u_end) < 0.6:
                m["corner"] = dict(c, **{"k_" + side: 0.0, "ext_" + side: stop})
            elif plank and m["ifc_type"] == "plank" and not m.get("holes"):
                # Vertical planks are set out across the face and run on past its end:
                # the ones reaching into the trim's place stop at its end, or go. Past the
                # wall's corner line they are trimmed in height only, like the corner
                # timbers, so they run out to the trim rather than stopping on the line.
                us, vs = [q[0] for q in m["profile"]], [q[1] for q in m["profile"]]
                near, far = (min(us), max(us)) if toward > 0 else (max(us), min(us))
                if (far - u_end) * toward <= 0.5:
                    continue
                if (near - limit) * toward >= -1.0:
                    meshes.remove(m)
                    continue
                if (far - limit) * toward > 0.5:
                    lo, hi = sorted((near, limit))
                    m["profile"] = _rect(lo, min(vs), hi, max(vs))
                m["clip_v_at"] = sorted((u_end - toward * 2.0, u_end - toward * 0.5))
        at = [q[1] for e in edges if e["kind"] == "corner" and abs(e["p1"][0] - u_end) < 1.0 for q in (e["p1"], e["p2"])]
        if not at:
            continue
        v_from, v_to = min(at) + s["bottom"], max(at) - s["top"]
        before = (side == "r") != bool(elev.get("chain_reversed"))
        if not plank or p["battens"] == "vertical":
            timbers = _corner_timbers(p, meshes, u_end, k, side, before, toward, u_end + toward * stop)
        else:
            timbers = _vertical_corner_timbers(p, elev, meshes, u_end, k, side, before, toward, min(at), max(at))
        if plank:
            out = face - D + profiles.TRIM_LEG                    # the nose's outer face, as depth
            u_c = u_end + toward * k * out                         # its outer corner line
            label, width = "Corner Profile", profiles.TRIM_SIZE
            span = sorted((u_c - toward * width, u_c - toward * (width + profiles.FLANGE_P)))
            plane = face - D
        else:
            out, u_c = face, u_end + toward * k * face             # the outer corner line
            label, width = "Corner Profile", D
            fl = profiles.FLANGE_A if before else profiles.FLANGE_B
            span, plane = sorted((u_c - toward * D, u_c - toward * (D + fl))), face - D
        if before:
            m = profiles.vertical_profile(frame, u_c, toward, v_from, v_to, out, D, "%s %s" % (name, label), name, trim=plank)
            if m:
                meshes.append(m)
                prof["count"] += 1
        prof["strips"].append(profiles.strip(*sorted((u_c, u_c - toward * width)), v_from, v_to))
        if not timbers and not profiles.flange_supported(meshes, name, span, (v_from, v_to), plane):
            prof["warnings"].append("%s: the corner %s at the %s end is not over a batten"
                                    % (name, "profile's %.0f mm flange" % (profiles.FLANGE_P if plank else fl),
                                       "right" if side == "r" else "left"))


BOARDS = ("panel", "plank")
MIN_CLOSER = 3.0     # mm – a closing plank narrower than this at a corner is left out


def _reentrant_layers(p, elev, meshes, ends):
    """The layers behind the boards at a re-entrant corner set to Master or Square.

    Round an external corner square-ended layers leave the corner empty, which is how a
    master-lap is built. In a re-entrant corner they would share it: both faces' battens
    in the same place. So the face that masters the corner is built right into it, and
    the other's layers stop against the face of its cladding (its boards a joint gap
    short of that, cladding_primitives.corner_ends): its horizontal layers are cut back,
    and a batten standing in the way is moved clear."""
    depth, bw = buildup_depth(p), p["batten_w"]
    for u_end, (k_b, ext_b), detail, side, k in ends:
        if k >= 0 or detail not in ("lap", "butt") or end_master(elev, side):
            continue
        toward = 1.0 if side == "r" else -1.0
        back = -ext_b - (p["panel_gap"] if p["cladding_type"] == "panel" else p["plank_gap"] if p["plank_lap"] <= 0 else 0.0)
        back = max(back, depth)                     # how far its layers stop from the wall's corner line
        for m in list(meshes):
            if m["ifc_type"] in BOARDS + ("reveal", "closer", "corner_profile", "corner_trim") or m not in meshes:
                continue
            us = [q[0] for q in m["profile"]]
            near, far = (max(us), min(us)) if toward > 0 else (min(us), max(us))      # near the corner, and away from it
            if (u_end - near) * toward > back - 0.5:
                continue                            # clear of the other face already
            c = {key: v for key, v in (m.get("corner") or {}).items() if not key.endswith("_" + side)}
            if abs(far - near) <= 2.5 * max(bw, p["cb_w"]):
                # a batten up the wall: moved whole, to stand against the other face's cladding
                shift = -toward * (back - (u_end - near) * toward)
                m["profile"] = [[q[0] + shift, q[1]] for q in m["profile"]]
                moved = sorted(q[0] for q in m["profile"])
                m["gasket_to"] = u_end - toward * back      # its gasket stops there too, not lapping past
                for other in list(meshes):
                    ou = [q[0] for q in other["profile"]]
                    if other is not m and other["ifc_type"] == m["ifc_type"] and abs(float(other["depth"]) - float(m["depth"])) < 0.5 \
                            and abs(max(ou) - min(ou)) <= 2.5 * max(bw, p["cb_w"]) and min(moved[-1], max(ou)) - max(moved[0], min(ou)) > 0.5:
                        meshes.remove(other)        # one was there already: the moved one takes its place
            elif abs(near - u_end) < 0.6:
                c.update({"k_" + side: k_b, "ext_" + side: -back, "u_" + side: u_end})
            if c:
                m["corner"] = c
            else:
                m.pop("corner", None)


def _plank_ends(p, elev, meshes, ends, mitre_gap):
    """Vertical planks at a chain corner (every detail but Profile, which has its own).

    They are set out across the face and on past its ends, so the cladding that wraps an
    external corner has boards too: here each end is finished. A plank that lies wholly
    beyond where the boards stop goes; the one that crosses it is cut there, on the
    corner's slope, and is the closing plank, never wider than a plank; one past the
    wall's corner line is trimmed in height only, so the cut at the line leaves it be.
    At a re-entrant corner the boards stop before the line and the same cut is made."""
    if p["cladding_type"] != "plank" or p["seam"] or p["boards_run"] != "vertical":
        return
    for u_end, (k_b, ext_b), detail, side, k in ends:
        if detail == "profile" or not k:
            continue
        if detail == "mitre" and mitre_gap > 0:
            ext_b = ext_b - mitre_pullback(mitre_gap, k_b)
        toward = 1.0 if side == "r" else -1.0
        for m in list(meshes):
            if m["ifc_type"] != "plank" or m.get("holes"):
                continue
            us, vs = [q[0] for q in m["profile"]], [q[1] for q in m["profile"]]
            near, far = (min(us), max(us)) if toward > 0 else (max(us), min(us))
            limit = u_end + toward * (ext_b + k_b * float(m["depth"]))       # where the board's back stops
            c = {key: v for key, v in (m.get("corner") or {}).items() if not key.endswith("_" + side)}
            if (near - limit) * toward >= -MIN_CLOSER:
                meshes.remove(m)
                continue
            if (far - limit) * toward > 0.5:
                a, b = sorted((near, limit))
                m["profile"] = _rect(a, min(vs), b, max(vs))
                c.update({"k_" + side: k_b, "ext_" + side: -k_b * float(m["depth"]), "u_" + side: limit})
                far = limit
            if (far - u_end) * toward > 0.5:
                m["clip_v_at"] = sorted((u_end - toward * 2.0, u_end - toward * 0.5))
            if c:
                m["corner"] = c
            else:
                m.pop("corner", None)


def _across_corners(meshes, ends):
    """A vertical batten set out on a joint close to an external chain corner can straddle
    the wall's corner line. Past that line the cavity carries on in front of the other
    face's insulation, so trimming it to the wall there would leave it off the joint it
    backs (and its gasket off it): it is trimmed in height only, as long as it stays on
    this face's side of the corner at its back face."""
    for u_end, (k, _ext), side in ends:
        if not k or k <= 0:
            continue                                      # re-entrant: the other face is there
        toward = 1.0 if side == "r" else -1.0
        for m in meshes:
            if m["ifc_type"] != "batten" or m.get("clip_v_at") or m.get("corner"):
                continue
            us = [q[0] for q in m["profile"]]
            inside, outside = (min(us), max(us)) if toward > 0 else (max(us), min(us))
            past = (outside - u_end) * toward
            if (inside - u_end) * toward < -0.5 and 0.5 < past <= k * float(m["depth"]) + 0.5:
                m["clip_v_at"] = sorted((u_end - toward * 2.0, u_end - toward * 0.5))


def _corner_timbers(p, meshes, u_end, k, side, before, toward, board_end):
    """The battens at a profiled corner as a solid L of timber, square, not mitred
    (Rockpanel H.03): the face before the corner has a batten twice the batten width,
    running on past the corner until it is flush with the other face's batten face, where
    flange A lies on it; the face after the corner has one of the batten width, tight
    behind it, and flange B lies over the wide one's end. Both run past their own
    outline, so they are trimmed in height only, to the clad height at the corner
    ("clip_v_at", cladding_booleans). Returns how many battens were made so."""
    bw, done = p["batten_w"], 0
    battens = [m for m in meshes if m["ifc_type"] == "batten" and not m.get("corner_timber")]
    at = [m for m in battens if ("k_" + side) in (m.get("corner") or {})
          and abs(m["corner"]["u_" + side] - u_end) <= 0.6]
    if not at and battens:
        # No batten reached the corner (one close by took its place): make one from the
        # nearest batten's section.
        near = min(battens, key=lambda m: min(abs(q[0] - u_end) for q in m["profile"]))
        at = [dict(near, corner={}, name="%s Corner Batten" % near["elevation"])]
        meshes.append(at[0])
    for m in at:
        c = m.get("corner") or {}
        d0 = float(m["depth"])
        reach, width = (k * (d0 + float(m["thickness"])), 2 * bw) if before else (k * d0, bw)
        outer = u_end + toward * reach
        a, b = sorted((outer, outer - toward * width))
        vs = [q[1] for q in m["profile"]]
        m["profile"], m["holes"] = _rect(a, min(vs), b, max(vs)), []
        c = {key: v for key, v in c.items() if not key.endswith("_" + side)}
        if c:
            m["corner"] = c
        else:
            m.pop("corner", None)
        m["clip_v_at"] = sorted((u_end - toward * 2.0, u_end - toward * 0.5))
        m["corner_timber"] = "wide" if before else "narrow"
        # its gasket stops where the board does, on the corner side: the narrow one's runs
        # on over the wide one's end, under flange B
        m["gasket_to"] = board_end
        # a batten the L now covers would clash with it: the L takes its place
        for other in battens:
            ou = [q[0] for q in other["profile"]]
            if other is not m and other in meshes and min(b, max(ou)) - max(a, min(ou)) > 0.5 and \
                    abs(float(other["depth"]) - d0) < 0.5:
                meshes.remove(other)
        done += 1
    return done


def _vertical_corner_timbers(p, elev, meshes, u_end, k, side, before, toward, v_lo, v_hi):
    """With vertical planks the battens run across the wall, so at a trimmed corner the
    trim and the last plank's edge would only meet a batten every few hundred mm. The
    same solid L of timber as _corner_timbers goes in the batten layer instead, standing
    up the corner over its clad height: two batten widths on the face before the corner,
    running on until flush with the other face's batten face, one behind it on the face
    after. The horizontal battens stop against it. Vertical, so it does not dam the
    cavity. Returns 1 when it is made."""
    battens = [m for m in meshes if m["ifc_type"] == "batten" and not m.get("corner_timber")]
    if not battens or v_hi - v_lo < 1.0:
        return 0
    bw, d0, t = p["batten_w"], float(battens[0]["depth"]), float(battens[0]["thickness"])
    reach, width = (k * (d0 + t), 2 * bw) if before else (k * d0, bw)
    outer = u_end + toward * reach
    inner = outer - toward * width
    a, b = sorted((outer, inner))
    for m in battens:
        us, vs = [q[0] for q in m["profile"]], [q[1] for q in m["profile"]]
        if abs(float(m["depth"]) - d0) > 0.5 or (max(us) - min(us)) < (max(vs) - min(vs)):
            continue                                      # not a horizontal batten of this layer
        near, far = (min(us), max(us)) if toward > 0 else (max(us), min(us))
        if (far - inner) * toward <= 0.5:
            continue                                      # stops short of the timber already
        if (near - inner) * toward >= -1.0:
            meshes.remove(m)                              # wholly where the timber is
            continue
        lo, hi = sorted((near, inner))
        m["profile"] = _rect(lo, min(vs), hi, max(vs))
        c = {key: v for key, v in (m.get("corner") or {}).items() if not key.endswith("_" + side)}
        if c:
            m["corner"] = c
        else:
            m.pop("corner", None)
    timber = _prism(_rect(a, v_lo, b, v_hi), d0, t, elev["frame"], "batten",
                    "%s Corner Timber" % elev["name"], elev["name"])
    timber["corner_timber"] = "wide" if before else "narrow"
    timber["clip_v_at"] = sorted((u_end - toward * 2.0, u_end - toward * 0.5))
    meshes.append(timber)
    return 1


def _gaskets(p, elev, meshes, board_back):
    """A UV- and weather-resistant EPDM gasket on every vertical timber the panels touch
    (battens, the corner L, jamb closers), 15 mm wider than the timber each side
    (Rockpanel: aR1 >= 15 mm), 2 mm thick. It is drawn in the back 2 mm of the board, so
    nothing moves and the cavity keeps its depth. A side that meets a corner keeps the
    timber's end instead of lapping past it."""
    out = []
    for m in meshes:
        if m.get("elevation") != elev["name"] or m["ifc_type"] not in ("batten", "closer"):
            continue
        us, vs = [q[0] for q in m["profile"]], [q[1] for q in m["profile"]]
        if max(vs) - min(vs) <= max(us) - min(us):
            continue                                      # horizontal: not a vertical timber
        if m["ifc_type"] == "closer" and abs(float(m["depth"]) + float(m["thickness"]) - board_back) > 1.0:
            continue                                      # not out to the boards
        if m.get("dpm"):
            continue                                      # a cavity barrier's timber: it has its membrane
        c = m.get("corner") or {}
        lo, hi = min(us) - (0.0 if "k_l" in c else EPDM_LAP), max(us) + (0.0 if "k_r" in c else EPDM_LAP)
        if "gasket_to" in m:                              # a corner timber: its corner side
            to = m["gasket_to"]
            lo, hi = (to, hi) if abs(to - min(us)) < abs(to - max(us)) else (lo, to)
        g = _prism(_rect(lo, min(vs), hi, max(vs)), board_back, EPDM_T, elev["frame"], "gasket",
                   "%s EPDM Gasket %d" % (elev["name"], len(out) + 1), elev["name"])
        g["on"] = m["name"]                               # the timber it is fixed to
        if c:
            g["corner"] = dict(c)
        if m.get("clip_v_at"):
            g["clip_v_at"] = list(m["clip_v_at"])
        if m.get("keep_clear"):                           # cut short of a cavity barrier with its batten
            g["keep_clear"] = [list(c) for c in m["keep_clear"]]
        out.append(g)
    meshes.extend(out)


def _apply_corner(meshes, treatments, detail, types=None, skip=(), tol=0.6):
    """Mark the elements whose end lands on one of *treatments*, {u: (k, ext)}.
    Consumers move the vertices there to u -/+ (ext + k x depth): k shears the end onto
    a bisector plane, ext runs it past square. A mitre cuts the whole buildup; a
    master-lap is a board detail, so the layers behind it stay square at the corner.
    An element is only ever treated at its own outermost edges, so a panel is never
    marked on an opening it merely spans."""
    live = {u: t for u, t in treatments.items() if any(t)}
    if not live:
        return
    for m in meshes:
        if (types is not None and m["ifc_type"] not in types) or m["ifc_type"] in skip:
            continue
        if detail == "lap" and types is None and m["ifc_type"] not in ("panel", "plank"):
            continue
        us = [q[0] for q in m["profile"]]
        lo, hi = min(us), max(us)
        corner = dict(m.get("corner") or {})
        for u_pos, (k, ext) in live.items():
            if abs(lo - u_pos) < tol:
                corner["k_l"], corner["ext_l"], corner["u_l"] = k, ext, u_pos
            if abs(hi - u_pos) < tol:
                corner["k_r"], corner["ext_r"], corner["u_r"] = k, ext, u_pos
        if corner:
            m["corner"] = corner


def _dim(elev, p1, p2, label, offset, norm, kind=None, value=None, **extra):
    """*kind* names what the dimension measures, so the UI can offer to edit it, and
    *extra* carries what it needs to write the value back (a panel row's index)."""
    d = {"elevation": elev, "p1": [float(p1[0]), float(p1[1])],
         "p2": [float(p2[0]), float(p2[1])], "label": label,
         "offset": float(offset), "norm": [float(norm[0]), float(norm[1])]}
    if kind:
        d["kind"], d["value"] = kind, float(value)
    d.update(extra)
    return d


def _datum(elev):
    """The chain's course datum in this elevation's v, or None outside a chain."""
    datum = elev.get("course_datum_z")
    return None if datum is None else z_to_v(elev["frame"], datum)


def _courses(elev, v0, H, pitch):
    """Course starts up the face. Within a chain every face is set out from one level —
    the lowest clad start in the chain — so horizontal joints line up round the corners
    even where the faces' bottoms differ, as on a dormer sitting on a sloping roof. The
    first course may start below this face's own base and is cut there."""
    local = _datum(elev)
    if local is None:
        return stacked_positions(v0, H, pitch)
    start = local + math.floor((v0 - local) / pitch + 1e-9) * pitch
    return stacked_positions(start, H, pitch)


LOCK_CENTRES = "The panel bay sets the batten centres: change the panel width"
LOCK_OPENING = "The structural opening sets this width"
LOCK_BAYS = "Set out from the structural openings: the bays follow the jambs"
LOCK_POSTS = "Set out from the model's columns: the battens follow the client's"
LOCK_MATCHED = "Matched to the battens of %s %s: change them there, or switch matching off for this chain"
LOCK_BASE_ROW = "Cut at this face's base: set this row on the face the chain's datum comes from"
LOCK_UNDER_DATUM = "Under the chain's datum: rows here carry on down at the panel height"
FIXED_TOP_ROW = "The top row is whatever is left under the top of the cladding, so its height is not typed: split it, or change the rows below"


def _batten_dims(name, battens, pitch, along_u, H_or_W, lock=None):
    """The c/c dimension between two regular battens, and the end distance to the edge
    batten when it differs. Measuring c/c from the edge batten reported the end
    distance under the name of the centres, so it seemed to change with the slider.
    The centres are editable unless *lock* says what drives them."""
    out = []
    pairs = list(zip(battens, battens[1:]))
    regular = next(((a, b) for a, b in pairs if abs((b - a) - pitch) < 1.0), None)
    def dim(a, b, label, off, **kw):
        if along_u:
            return _dim(name, [a, 0], [b, 0], label, off, [0, -1], **kw)
        return _dim(name, [0, a], [0, b], label, off, [-1, 0], **kw)
    if regular:
        extra = {"lock": lock} if lock else {}
        out.append(dim(regular[0], regular[1], "%.0f c/c" % pitch, 300 if along_u else 600,
                       kind="centres", value=pitch, **extra))
    if pairs and abs((pairs[0][1] - pairs[0][0]) - pitch) >= 1.0:
        out.append(dim(pairs[0][0], pairs[0][1], "End %.0f" % (pairs[0][1] - pairs[0][0]), 300 if along_u else 600))
    return out


def _post_lock(info):
    """Why the battens of a face set out on given lines cannot be dragged: the client's
    columns, or the chain above or below (cladding_match)."""
    to = info.get("matched_to")
    return LOCK_MATCHED % (to.get("chain") or to.get("name") or "the chain", to.get("side") or "beside") if to else LOCK_POSTS


def _client_batten_dims(name, meshes, info, H):
    """On a face set out on the client's battens: a running string across the top from one
    client batten in use (on, drawn green in the 2D view) to the next, and, inside the panel
    layout, a dimension from each of our own vertical battens (drawn blue: at the ends, where
    a joint has no client batten in reach, on an opening's reveal line) to the nearest green
    one, so where ours sit is read off theirs."""
    posts = info.get("posts") or []
    greens = sorted(q["u"] for q in posts if q["state"] == "on")
    if not greens:
        return []
    lock = _post_lock(info)
    out = _widths_string(name, list(zip(greens, greens[1:])), H, lock)
    ours = set()
    for m in meshes:
        if m.get("elevation") != name or m.get("ifc_type") not in ("batten", "counter_batten"):
            continue
        us, vs = [q[0] for q in m["profile"]], [q[1] for q in m["profile"]]
        w = max(us) - min(us)
        if max(vs) - min(vs) < 3 * w:
            continue                                   # vertical ones only
        u = (min(us) + max(us)) / 2.0
        if all(abs(u - q["u"]) > w / 2.0 for q in posts):
            ours.add(round(u, 1))
    for k, u in enumerate(sorted(ours)):
        g = min(greens, key=lambda x: abs(x - u))
        if abs(u - g) < 1.0:
            continue
        v = H - (300.0 if k % 2 == 0 else 520.0)       # inside the layout, stepped
        out.append(_dim(name, [min(g, u), v], [max(g, u), v], "%.0f" % abs(u - g), 0.0, [0, 1],
                        lock=lock, ours=round(u, 1)))
    return out


def _widths_string(name, spans, H, lock=LOCK_POSTS):
    """A running string of dimensions across the top of the face, one per span (a, b),
    stepped up and down alternately so narrow ones' labels do not run into each other."""
    spans = [(a, b) for a, b in sorted(spans) if b - a > 1.0]
    return [_dim(name, [a, H], [b, H], "%.0f" % (b - a), 480 if k % 2 == 0 else 640, [0, 1], lock=lock)
            for k, (a, b) in enumerate(spans)]


def _vertical_battens(p, us, meshes, name, frame, depth, H, ifc_type="batten", w=None, d=None):
    w = w or p["batten_w"]
    d = d or p["batten_d"]
    for i, u in enumerate(us):
        meshes.append(_prism(_rect(u - w / 2, 0, u + w / 2, H), depth, d, frame, ifc_type,
                             "%s %s %d" % (name, ifc_type.replace("_", " ").title(), i + 1), name))
    return depth + d


def _horizontal_battens(p, vs, meshes, name, frame, depth, W, ifc_type="batten", w=None, d=None):
    w = w or p["batten_w"]
    d = d or p["batten_d"]
    for i, v in enumerate(vs):
        meshes.append(_prism(_rect(0, v - w / 2, W, v + w / 2), depth, d, frame, ifc_type,
                             "%s %s %d" % (name, ifc_type.replace("_", " ").title(), i + 1), name))
    return depth + d


def _joints(segs):
    """Where a run's pieces meet: the middle of each joint gap."""
    return [(e + s) / 2.0 for (_s, e), (s, _e) in zip(segs, segs[1:])]


POST_STATES = ("on", "fix", "off")


def post_key(u):
    """How a model column is known from one build to the next: its centreline on the face,
    to the millimetre (the 2D view's toggles are kept by it, elev["post_states"])."""
    return str(int(math.floor(float(u) + 0.5)))      # half up, as the 2D view's Math.round


def post_state(elev, u, default="on"):
    """A model column's use, as toggled in the 2D view: "on" (a batten a joint may land on,
    the default), "fix" (a batten for fixing only: no joint on it), "off" (not used: the
    default for something batten-shaped that is not a column or member, until turned on)."""
    default = default if default in POST_STATES else "on"
    st = (elev.get("post_states") or {}).get(post_key(u), default)
    return st if st in POST_STATES else default


def _posts(p, elev, info):
    """Centrelines for the vertical battens from the columns the client's model has standing
    on the face (fabric_extract._post_of), when the option is on and there are any: the
    designer set them out to suit the frame behind, so ours go on the same lines and the
    offset slider has nothing to move. A column toggled "off" in the 2D view is left out;
    one toggled "fix" is a batten but takes no joint (_post_fixings). None if none are left.
    Every column and its state go to info["posts"] for the 2D view."""
    cols = sorted(elev.get("posts") or [], key=lambda q: q["u"]) if p.get("battens_from_model", True) else []
    # No column of the client's in use: the lines of the chain directly above or below
    # (cladding_match), which come the same way. The client's own come first.
    matched = elev.get("matched_posts") or []
    follow = bool(matched) and not any(post_state(elev, q["u"], q.get("default", "on")) != "off" for q in cols)
    if follow:
        cols = sorted(cols + list(matched), key=lambda q: q["u"])
    info["posts"] = [{"u": round(float(q["u"]), 1), "v0": q.get("v0"), "v1": q.get("v1"), "w": q.get("w"),
                      "state": post_state(elev, q["u"], q.get("default", "on")), "default": q.get("default", "on"),
                      "name": q.get("name", "")} for q in cols]
    us = [q["u"] for q in info["posts"] if q["state"] != "off"]
    info["model_posts"] = 0 if follow else len(us)
    count = sum(1 for q in matched if q.get("matched") and post_state(elev, q["u"], q.get("default", "on")) != "off")
    info["matched_to"] = dict(elev["matched_to"], count=count) if follow and count and elev.get("matched_to") else None
    return us or None


def _post_fixings(info):
    """The model columns toggled "fix": battens for fixing only, which no joint may land on."""
    return [q["u"] for q in info.get("posts") or [] if q["state"] == "fix"]


SEAM_STOPS = 50.0     # mm – a tray's cross joint can go anywhere on the deck: these are its steps


def _seam_deck(p, elev, meshes, depth, region):
    """A standing seam's continuous deck (softwood boarding or WBP ply) on the battens,
    following the clad outline (openings as holes). Returns the depth where the trays lie:
    SEAM_STANDOFF off its face."""
    name, frame = elev["name"], elev["frame"]
    for i, pg in enumerate(iter_polygons(region)):
        ext, holes = polygon_to_rings(pg)
        meshes.append(_prism(ext, depth, p["deck_t"], frame, "deck", "%s Deck %d" % (name, i + 1), name, holes=holes))
    return depth + p["deck_t"] + SEAM_STANDOFF


def _tray(p, ring, depth, frame, label, elevation):
    """A standing seam tray: an opaque metal pan, unlike a see-through board."""
    m = _prism(ring, depth, p["plank_t"], frame, "plank", label, elevation)
    m["opacity"] = 1.0
    return m


def _shared(s, e, runs, least=5.0):
    """The parts of (s, e) that the neighbouring tray's *runs* also cover: where a seam joins
    two trays. Beside an opening the tray turns into the reveal, so no seam runs there."""
    return [(max(s, a), min(e, b)) for a, b in runs if min(e, b) - max(s, a) > least]


# The closed double lock sheet by sheet, across the seam line (x, + toward the head side) and
# up from the tray face (z): (part, whose tray, x0, x1, z0, z1), each a sheet t thick. Rolled,
# not doubled back: the first fold takes the overcloak (the tray on the - side) over the
# undercloak's top; the second turns that head down 180° the same way. So the overcloak runs
# outermost all the way: up, over the top, down the outside, round the foot of the head and
# back up inside. The undercloak (this tray) runs up, over inside it and down between. Every
# sheet keeps the same air *a* to the one parallel to it (the clip runs in it between the
# upstands), so the head is 5t + 4a across; it hangs *d* from the top.
def _double_lock(t, h, a=SEAM_AIR, d=SEAM_HEAD_D):
    x0 = -a / 2.0 - t                    # the overcloak, up the stem
    xu = a / 2.0                         # the undercloak, up the stem
    lip = xu + t + a                     # then the overcloak's lip, the undercloak's flange
    fl = lip + t + a
    x1 = fl + t + a + t                  # the overcloak's outer face
    bend = h - t - a                     # the top of the undercloak's bend, under the cap
    foot = h - d                         # the overcloak's turn round the foot of the head
    return (("over_up", "-", x0, x0 + t, 0.0, h - t),
            ("over_cap", "-", x0, x1, h - t, h),
            ("over_outer", "-", x1 - t, x1, foot + t, h - t),
            ("over_turn", "-", lip, x1, foot, foot + t),
            ("over_lip", "-", lip, lip + t, foot + t, bend - t - a),
            ("under_up", "+", xu, xu + t, 0.0, bend - t),
            ("under_bend", "+", xu, fl + t, bend - t, bend),
            ("under_flange", "+", fl, fl + t, foot + t + a, bend - t))


SEAM_PARTS = {"over_up": "overcloak", "over_cap": "overcloak bend", "over_outer": "overcloak outer",
              "over_turn": "overcloak turn", "over_lip": "overcloak lip",
              "under_up": "undercloak", "under_bend": "undercloak bend", "under_flange": "undercloak flange"}


def _seam(p, meshes, frame, name, elevation, face, along_v, line, a, b, mirror=False):
    """The closed double-lock seam on a tray's edge at *line* (u for vertical trays, v for
    horizontal), from *a* to *b* along it, sheet by sheet: the tray on the - side (left, or
    below) turns up the overcloak, this tray the undercloak, rolled together with the head
    turned down on this tray's side (_double_lock). *mirror* turns the head the other way,
    as on the left jamb of an opening, so it stays on the face. No clips."""
    for part, side, c0, c1, z0, z1 in _double_lock(p["seam_t"], p["seam_h"]):
        if mirror:
            c0, c1, side = -c1, -c0, ("+" if side == "-" else "-")
        ring = _rect(line + c0, a, line + c1, b) if along_v else _rect(a, line + c0, b, line + c1)
        m = _prism(ring, face + z0, z1 - z0, frame, "seam", "%s Seam %s" % (name, SEAM_PARTS[part]), elevation)
        m.update(seam_part=part, seam_side=side, seam_v=along_v, seam_line=line, seam_face=face)   # for finish_seams
        meshes.append(m)


def _lengths(p, a, b, stops, odd, gap, beside):
    """The pieces a run a..b is laid in, and its joints that miss a support: split to the
    stock length (split_run), or with full-length standing seam trays the whole run in
    one piece, so there is no cross joint."""
    if p.get("seam_full"):
        return ([(a, b)] if b - a > 1.0 else []), []
    return split_run(a, b, p["plank_len"], stops, odd, gap, p["plank_min"], p["plank_target"], beside, p["batten_centres"])


def _horizontal_planks(p, elev, meshes, dims, info, depth, W, H, v0, offset, region=None):
    """Horizontal boards on vertical battens (optionally on horizontal counter-battens)."""
    name, frame = elev["name"], elev["frame"]
    if p["has_cb"]:  # forced by override — flagged by check_rules, drainage is compromised
        vs = dedupe(stacked_positions(v0 + p["cb_w"] / 2, H, p["cb_centres"]) + [H - p["cb_w"] / 2], p["cb_w"])
        depth = _horizontal_battens(p, vs, meshes, name, frame, depth, W, "counter_batten", p["cb_w"], p["cb_d"])
    posts = _posts(p, elev, info)
    bw = p["batten_w"]
    battens = (dedupe_priority([posts, [bw / 2, W - bw / 2]], bw) if posts
               else batten_positions(W, bw, p["batten_centres"], offset))
    depth = _vertical_battens(p, battens, meshes, name, frame, depth, H)
    fixings = _post_fixings(info)        # battens a plank end may not stop on
    stops = [u for u in battens if all(abs(u - f) > bw / 2 for f in fixings)]
    if p["seam"]:            # trays on a deck: their cross joints need no batten behind
        depth = _seam_deck(p, elev, meshes, depth, region)
        stops = [SEAM_STOPS * k for k in range(1, int(W / SEAM_STOPS))]
    cover, face = p["cover"], p["plank_w"]
    gap = p["plank_gap"] if p["plank_lap"] <= 0 else 0.0
    courses = _courses(elev, v0, H, cover)
    beside = []                  # the joints in the course below: this one keeps clear of them
    below = []                   # and its runs, which a standing seam joins
    zones = [] if p["seam"] else barrier_zones(p, elev)
    every = stops
    for j, v in enumerate(courses):
        # a batten cut short of a cavity barrier carries no end joint where it is not
        stops = [u for u in every if _clear_of_barriers(zones, u, max(v, v0), v + face, bw / 2)] if zones else every
        # Set each course out along the part of the face it actually crosses, so a run
        # broken by a gable or an opening gets no seam it does not need.
        lo = max(v, v0)          # a course set out from the chain datum is cut at the base
        if v + face - lo < 1.0:
            beside, below = [], []
            continue
        runs = strip_intervals(region, lo, v + face) if region is not None else [(0.0, W)]
        piece, mine = 0, []
        for a, b in runs:
            segs, bad = _lengths(p, a, b, stops, j % 2 == 1, gap, beside)
            mine += _joints(segs)
            info["unsupported_joints"] += len(bad)
            for s, e in segs:
                piece += 1
                label = "%s %s C%d-%d" % (name, "Tray" if p["seam"] else "Plank", j + 1, piece)
                ring = _rect(s, lo, e, v + face)
                plank = _tray(p, ring, depth, frame, label, name) if p["seam"] \
                    else _prism(ring, depth, p["plank_t"], frame, "plank", label, name)
                if p.get("section"):                 # its section's w = 0 at the course's foot
                    plank.update(section_at=v, section_axis="v")
                meshes.append(plank)
                if p["seam"]:                        # the seam with the tray below, along its bottom edge
                    for a2, b2 in _shared(s, e, below):
                        _seam(p, meshes, frame, label, name, depth + p["plank_t"], False, lo, a2, b2)
        beside, below = mine, runs
    info.update(n_courses=len(courses), cover=cover, batten_centres=p["batten_centres"],
                closing_cut_top=(H - courses[-1]) if courses else 0.0,
                closing_cut_left=0.0, closing_cut_right=0.0)
    dims += _batten_dims(name, battens, p["batten_centres"], True, W, lock=_post_lock(info) if posts else None)
    full = [v for v in courses if v >= v0 - 0.5]
    if full:
        dims.append(_dim(name, [W, full[0]], [W, full[0] + cover], "Course %.0f" % cover, 300, [1, 0],
                         "course", cover))
        dims.append(_dim(name, [W, courses[-1]], [W, H], "Cut %.0f" % (H - courses[-1]), 600, [1, 0]))
    return depth + p["plank_t"]


def _vertical_planks(p, elev, meshes, dims, info, depth, W, H, v0, offset, region=None):
    """Vertical boards on horizontal battens on vertical counter-battens. Vertical standing
    seam trays instead lie on a deck on vertical battens, so the cavity still drains."""
    name, frame = elev["name"], elev["frame"]
    if p["seam"]:
        return _vertical_trays(p, elev, meshes, dims, info, depth, W, H, v0, offset, region)
    posts = _posts(p, elev, info) if p["has_cb"] else None
    if not p["has_cb"]:
        info["model_posts"] = 0       # no vertical layer to put on the columns
    if p["has_cb"]:
        # the first layer, vertical: on the columns where the model has them
        cbs = (dedupe_priority([posts, [p["cb_w"] / 2, W - p["cb_w"] / 2]], p["cb_w"]) if posts
               else batten_positions(W, p["cb_w"], p["cb_centres"], 0.0))
        depth = _vertical_battens(p, cbs, meshes, name, frame, depth, H, "counter_batten", p["cb_w"], p["cb_d"])
    bw = p["batten_w"]
    battens = dedupe(stacked_positions(v0 + bw / 2, H - bw / 2, p["batten_centres"]) + [H - bw / 2], bw)
    depth = _horizontal_battens(p, battens, meshes, name, frame, depth, W)
    cover, face = p["cover"], p["plank_w"]
    gap = p["plank_gap"] if p["plank_lap"] <= 0 else 0.0
    # Round an external chain corner the cladding carries on past the wall's corner line by
    # up to the buildup's depth, and it wants boards there too: the planks are set out on
    # past each end that has a corner, and finished there (_plank_ends, _profiled_corners).
    lo, hi = clip_bounds(elev)
    k_ends = (elev.get("corner_lo"), elev.get("corner_hi"))
    k_l, k_r = k_ends[::-1] if elev.get("chain_reversed") else k_ends
    wrap = lambda k: (max(0.0, float(k or 0.0)) * (depth + p["plank_t"]) + cover) if (k or 0.0) > 0 else 0.0   # noqa: E731
    first = centred_positions(W, cover, offset, face)
    starts = list(first)
    while starts and starts[0] + face > lo - wrap(k_l) and wrap(k_l):
        starts.insert(0, starts[0] - cover)
    while starts and starts[-1] < hi + wrap(k_r) and wrap(k_r):
        starts.append(starts[-1] + cover)
    beside = []                  # the joints in the plank to the left: this one keeps clear of them
    for j, u in enumerate(starts):
        # at a free end, a sliver of a plank narrower than can be cut is left out
        if (not wrap(k_l) and u + face - lo < MIN_CLOSER) or (not wrap(k_r) and hi - u < MIN_CLOSER):
            continue
        # a plank wholly past the end runs as high as the face does just inside its corner
        at = (hi - 2.0, hi - 0.5) if u >= hi - 0.5 else (lo + 0.5, lo + 2.0) if u + face <= lo + 0.5 else (u, u + face)
        runs = strip_intervals(region, at[0], at[1], across=True) if region is not None else [(v0, H)]
        piece, mine = 0, []
        for a, b in runs:
            segs, bad = _lengths(p, max(a, v0), b, battens, j % 2 == 1, gap, beside)
            mine += _joints(segs)
            info["unsupported_joints"] += len(bad)
            for s, e in segs:
                piece += 1
                plank = _prism(_rect(u, s, u + face, e), depth, p["plank_t"], frame, "plank",
                               "%s Plank C%d-%d" % (name, j + 1, piece), name)
                if p.get("section"):                 # its section's w = 0 at its left edge
                    plank.update(section_at=u, section_axis="u")
                meshes.append(plank)
        beside = mine
    starts = first               # the face's own, for what is reported and dimensioned
    left_cut = (starts[0] + face) if starts and starts[0] < 0 else 0.0
    right_cut = (W - starts[-1]) if starts and starts[-1] + face > W else 0.0
    info.update(n_courses=len(starts), cover=cover, batten_centres=p["batten_centres"],
                closing_cut_top=0.0, closing_cut_left=left_cut, closing_cut_right=right_cut)
    dims += _batten_dims(name, battens, p["batten_centres"], False, H)
    if starts:
        dims.append(_dim(name, [max(0, starts[0]), H], [max(0, starts[0]) + (cover if starts[0] >= 0 else left_cut), H],
                         ("Course %.0f" % cover) if starts[0] >= 0 else ("Cut %.0f" % left_cut), 300, [0, 1],
                         "course" if starts[0] >= 0 else None, cover))
    return depth + p["plank_t"]


LOCK_SEAMS = "Set out from the structural openings: the seams are on the reveal corners"
SEAM_JAMB_MERGE = 100.0     # mm – jambs closer than this share one seam line


SEAM_MIN_CLOSE = 100.0      # mm – a closing tray at a corner or a verge is never narrower than this


def seam_columns(W, cover, offset, jambs, ends=None, reach=(0.0, 0.0), limits=None):
    """The tray columns (u0, u1) across a face *W* wide. Set out from the openings, each
    jamb is a seam line, carried straight up and down past the opening, and each span between
    jambs (and the face's ends) is split into equal trays no wider than *cover*: no narrow
    closing cut. Otherwise trays at *cover* centres, centred on the face plus *offset*.

    *ends* (lo, hi) is the clad part of the face and *reach* how far the trays run on past
    each of its ends (round a corner, over a verge strip: _tray_reach). An end with a reach
    is set out to where the tray really ends, so that tray is never wider than *cover*.
    *limits* is the least and most its end tray may be: narrower, the last two trays share
    their width equally instead; wider (a verge takes at most 370), it is halved."""
    lo, hi = ends if ends else (0.0, W)
    limits = limits or ((SEAM_MIN_CLOSE, cover), (SEAM_MIN_CLOSE, cover))
    a_dev, b_dev = lo - reach[0], hi + reach[1]
    lines = []
    for u in sorted(jambs):
        if 1.0 < u < W - 1.0 and not (lines and u - lines[-1] < SEAM_JAMB_MERGE):
            lines.append(u)
    if lines:
        first, last = (a_dev if reach[0] else 0.0), (b_dev if reach[1] else W)
        return [(a, b) for a, b, _full in bays_between([first] + lines + [last], cover, 0.0)[0]]
    cols = [(u, u + cover) for u in centred_positions(W, cover, offset, cover)]
    if not (reach[0] or reach[1]) or not cols:
        return cols
    # the seam lines between the trays, carried on at the same centres out to each developed end
    seams = [u for u, _u1 in cols[1:]]
    while reach[0] and (seams[0] if seams else cols[0][1]) - cover > a_dev + 1e-6:
        seams.insert(0, (seams[0] if seams else cols[0][1]) - cover)
    while reach[1] and (seams[-1] if seams else cols[0][0]) + cover < b_dev - 1e-6:
        seams.append((seams[-1] if seams else cols[0][0]) + cover)
    seams = [u for u in seams if (a_dev < u if reach[0] else True) and (u < b_dev if reach[1] else True)]
    if reach[0] and seams:
        least, most = limits[0]
        if seams[0] - a_dev < least - 1e-6:               # too close to that end: the two end trays share
            nxt = seams[1] if len(seams) > 1 else (b_dev if reach[1] else cols[-1][1])
            seams[0] = (a_dev + nxt) / 2.0
        elif seams[0] - a_dev > most + 1e-6:              # too far from it: one more seam, half way
            seams.insert(0, (a_dev + seams[0]) / 2.0)
    if reach[1] and seams:
        least, most = limits[1]
        if b_dev - seams[-1] < least - 1e-6:
            prev = seams[-2] if len(seams) > 1 else (a_dev if reach[0] else cols[0][0])
            seams[-1] = (b_dev + prev) / 2.0
        elif b_dev - seams[-1] > most + 1e-6:
            seams.append((b_dev + seams[-1]) / 2.0)
    first = a_dev if reach[0] else min(cols[0][0], seams[0] - cover if seams else cols[0][0])
    last = b_dev if reach[1] else max(cols[-1][1], seams[-1] + cover if seams else cols[-1][1])
    stops = [first] + seams + [last]
    return [(a, b) for a, b in zip(stops, stops[1:]) if b - a > 1.0]


def _tray_seams(p, meshes, frame, name, face, laid, holes):
    """The seams between vertical tray columns, *laid* left to right as (u, runs, trays),
    each tray (s, e, label). A seam runs where the trays either side of its line meet. On a
    reveal corner (a jamb, set out from the openings) it carries straight on up past the
    opening through the reveal detail, where only one tray is there, so the jamb line reads
    as one seam the full height: kept on the face, its outer sheet on the corner and its head
    turned away from the opening (mirrored on a left jamb, the opening on its + side). There
    the reveal lining is the second tray: it runs out of the reveal and on up as the seam's
    upstand on the opening side, locked into the fold with the face tray's.""" 
    # The reveal lining stands SEAM_STANDOFF into the opening off the jamb, and carries
    # straight on out past the face as the seam's upstand on the opening side.
    off = SEAM_AIR / 2.0 - SEAM_STANDOFF
    for (_u0, left, ltrays), (u, runs, trays) in zip(laid, laid[1:]):
        jamb = next((side for o in holes for side, x in ((-1, o[0]), (1, o[1])) if abs(u - x) < 1.0), 0)
        if not jamb:
            for s, e, label in trays:
                for a2, b2 in _shared(s, e, left):
                    _seam(p, meshes, frame, label, name, face, True, u, a2, b2)
            continue
        line, mirror = u + jamb * off, jamb < 0
        # trimmed in height only, by the face beside it: its reveal side stands in the opening
        strip = tuple(sorted((u + jamb * 6.0, u + jamb * 12.0)))
        pieces = [(s, e, label) for s, e, label in trays]
        for s, e, label in ltrays:                # past the opening: the tray on the other side alone
            spans = [(s, e)]
            for a, b in runs:
                spans = [q for x0, x1 in spans for q in ((x0, min(x1, a)), (max(x0, b), x1)) if q[1] - q[0] > 1.0]
            pieces += [(x0, x1, label) for x0, x1 in spans]
        for s, e, label in sorted(pieces):
            n = len(meshes)
            _seam(p, meshes, frame, label, name, face, True, line, s, e, mirror=mirror)
            for m in meshes[n:]:
                m["clip_v_at"] = strip


def _vertical_trays(p, elev, meshes, dims, info, depth, W, H, v0, offset, region=None):
    """Vertical standing seam trays: vertical battens (on the model's columns where it has
    them), the deck, then trays a seam-centre wide set out across the face like vertical
    planks (or from the openings, seams on the reveal corners), run and joined like them,
    each with its seam along its left edge."""
    name, frame = elev["name"], elev["frame"]
    posts, bw = _posts(p, elev, info), p["batten_w"]
    battens = (dedupe_priority([posts, [bw / 2, W - bw / 2]], bw) if posts
               else batten_positions(W, bw, p["batten_centres"], 0.0))
    depth = _vertical_battens(p, battens, meshes, name, frame, depth, H)
    depth = _seam_deck(p, elev, meshes, depth, region)
    stops = [v0 + SEAM_STOPS * k for k in range(1, int((H - v0) / SEAM_STOPS))]
    cover = p["plank_w"]
    jambs = [u for o in openings(elev) for u in (o[0], o[1])] if p["set_out_from_openings"] else []
    reach, limits = _tray_reach(p, elev, depth)
    cols = seam_columns(W, cover, offset, jambs, clip_bounds(elev), reach, limits)
    from_openings = bool(jambs) and len(cols) > 0 and (abs(cols[0][0]) < 1e-6 or reach[0]) and (abs(cols[-1][1] - W) < 1e-6 or reach[1])
    beside, laid = [], []        # the joints of the column to the left; every column's runs and trays
    for j, (u, u1) in enumerate(cols):
        runs = strip_intervals(region, u, u1, across=True) if region is not None else [(v0, H)]
        runs = [(max(a, v0), b) for a, b in runs if b - max(a, v0) > 1.0]
        piece, mine, trays = 0, [], []
        for a, b in runs:
            segs, bad = _lengths(p, a, b, stops, j % 2 == 1, 0.0, beside)
            mine += _joints(segs)
            info["unsupported_joints"] += len(bad)
            for s, e in segs:
                piece += 1
                label = "%s Tray C%d-%d" % (name, j + 1, piece)
                meshes.append(_tray(p, _rect(u, s, u1, e), depth, frame, label, name))
                trays.append((s, e, label))
        beside = mine
        laid.append((u, runs, trays))
    _tray_seams(p, meshes, frame, name, depth + p["plank_t"], laid, openings(elev) if from_openings else [])
    left_cut = cols[0][1] if cols and cols[0][0] < 0 else 0.0
    right_cut = (W - cols[-1][0]) if cols and cols[-1][1] > W else 0.0
    info.update(n_courses=len(cols), cover=cover, batten_centres=p["batten_centres"],
                closing_cut_top=0.0, closing_cut_left=left_cut, closing_cut_right=right_cut,
                set_out_from_openings=from_openings, tray_widths=sorted({round(b - a) for a, b in cols if a >= 0 and b <= W}))
    dims += _batten_dims(name, battens, p["batten_centres"], True, W, lock=_post_lock(info) if posts else None)
    if cols:
        u0, u1 = cols[0]
        lock = {"lock": LOCK_SEAMS} if from_openings else {}
        dims.append(_dim(name, [max(0, u0), H], [min(W, u1), H],
                         ("Tray %.0f" % (u1 - u0)) if u0 >= 0 else ("Cut %.0f" % left_cut), 300, [0, 1],
                         None, u1 - max(0, u0), **lock))
    return depth + p["plank_t"]


STAGGER_MIN = 50.0       # mm – a step in the clad outline at least this high is a stagger
STAGGER_REACH = 100.0    # mm – a joint on it is offered when a client batten is this close


def _staggers(elev, region, W, margin, holes, posts):
    """The staggers of a face: where the clad outline steps, at its base or its top (the
    upright edges of the outline that are not an end of the face, a chain corner or an
    opening's jamb). A panel joint can be put on one (elev["stagger_joints"], by post_key,
    toggled in the 2D view) rather than a panel being notched round it; that is offered
    where a client batten in use stands within STAGGER_REACH, since the joints either
    side would otherwise land on the battens and miss it. [{"u", "v0", "v1", "near",
    "offered", "on"}], *near* the closest such batten or None."""
    if region is None:
        return []
    lo, hi = clip_bounds(elev)
    found = {}
    for pg in iter_polygons(region):
        ring = list(pg.exterior.coords)
        for a, b in zip(ring, ring[1:]):
            if abs(a[0] - b[0]) > 0.5 or abs(a[1] - b[1]) < STAGGER_MIN:
                continue
            u = (a[0] + b[0]) / 2.0
            if not (margin < u < W - margin) or min(abs(u - lo), abs(u - hi)) < 1.0:
                continue
            if any(min(abs(o[0] - u), abs(o[1] - u)) < 1.0 for o in holes):
                continue
            q = found.setdefault(post_key(u), {"u": round(u, 1), "v0": min(a[1], b[1]), "v1": max(a[1], b[1])})
            q.update(v0=min(q["v0"], a[1], b[1]), v1=max(q["v1"], a[1], b[1]))
    chosen = elev.get("stagger_joints") or {}
    out = []
    for key, q in sorted(found.items(), key=lambda kv: kv[1]["u"]):
        near = min(posts or [], key=lambda x: abs(x - q["u"]), default=None)
        offered = near is not None and abs(near - q["u"]) <= STAGGER_REACH
        out.append(dict(q, v0=round(q["v0"], 1), v1=round(q["v1"], 1), near=near if offered else None,
                        offered=offered, on=bool(offered and chosen.get(key))))
    return out


def _gap_at_jamb_lines(panels, holes, gap, grow=None):
    """The joint gap on each jamb line, taken out of the bay under and over the opening.
    Beside an opening a panel runs to the jamb, where it meets the reveal lining, so no
    gap is taken on that side; but the jamb line carries on above the head and below the
    sill as an ordinary joint between two panels, and those want their gap. It is given by
    the bay that lies within the opening's width, so the panel beside the opening still
    lines through with the reveal."""
    out, grow = [], grow or {}
    for s, e, full in panels:
        inside = [o for o in holes if o[0] - 0.5 <= s and e <= o[1] + 0.5]
        left = [gap + grow.get(tuple(o), 0.0) for o in inside if abs(s - o[0]) < 0.5]
        right = [gap + grow.get(tuple(o), 0.0) for o in inside if abs(e - o[1]) < 0.5]
        s2, e2 = s + max(left, default=0.0), e - max(right, default=0.0)
        out.append((s2, e2, full) if e2 - s2 > 1.0 else (s, e, full))
    return out


def _panels(p, elev, meshes, dims, info, depth, W, H, v0, offset, region=None):
    """Panels on vertical battens, joints on battens, noggins at horizontal joints only
    where a counter-batten layer is there to keep the drainage plane clear.
    Where the elevation has openings the setting-out starts from them: panel edges land
    on the structural jambs and each span between jambs is split into equal bays no
    wider than the maximum panel."""
    name, frame = elev["name"], elev["frame"]
    bw, gap = p["batten_w"], p["panel_gap"]
    cavity_start = depth
    if p["has_cb"]:  # forced by override
        vs = dedupe(stacked_positions(v0 + p["cb_w"] / 2, H, p["cb_centres"]) + [H - p["cb_w"] / 2], p["cb_w"])
        depth = _horizontal_battens(p, vs, meshes, name, frame, depth, W, "counter_batten", p["cb_w"], p["cb_d"])
    holes = openings(elev)
    jambs = sorted({u for o in holes for u in (o[0], o[1]) if bw < u < W - bw}) if p["set_out_from_openings"] else []
    posts = _posts(p, elev, info)
    reveal, steps = [], []
    info["staggers"] = []
    if posts:
        # The client's columns fix the battens: every joint goes on one, and each reveal
        # line is a joint carried straight up past the opening and on down below it, on a
        # batten above and below the opening (beside it the closer carries the joint):
        # the column's own where one is there, one of ours where not. A joint with no
        # column in reach gets a batten too.
        fixings = _post_fixings(info)
        on = [u for u in posts if all(abs(u - f) > 0.5 for f in fixings)]   # the columns a joint may use
        # A stagger given a joint is a fixed line like a jamb, but a joint for its whole
        # height: the panels either side stand the joint gap apart, on a batten of ours.
        info["staggers"] = _staggers(elev, region, W, bw, holes, on)
        steps = [q["u"] for q in info["staggers"] if q["on"] and all(abs(q["u"] - j) > bw for j in jambs)]
        panels, joints, extra = bays_on_posts(sorted([0.0] + jambs + steps + [W]), on, p["panel_w"], gap)
        if steps:
            at = lambda x: any(abs(x - u) < 1e-6 for u in steps)   # noqa: E731
            panels = [(s0 + (gap / 2.0 if at(s0) else 0.0), e0 - (gap / 2.0 if at(e0) else 0.0), full)
                      for s0, e0, full in panels]
            joints = sorted(joints + steps)
        reveal = sorted(jambs + steps)
        info["post_extra"] = len(extra)
        info["reveal_battens"] = len([u for u in jambs if all(abs(u - q) > bw / 2 for q in posts)])
    elif jambs:
        panels, joints = bays_between([0.0] + jambs + [W], p["panel_w"], gap)
        # Beside its opening a jamb line is backed by the cavity closer; over and under the
        # opening it is a panel joint like any other and wants a batten, or the battens
        # there stand a whole bay and more apart and the opening has upset their spacing.
        reveal = list(jambs)
    else:
        panels, joints = panel_bays(W, p["panel_w"], gap, offset)
    # Where the face board masters the reveal it runs a board's thickness past the jamb
    # line, for its whole length: the bay under and over the opening stands off that.
    grow = {tuple(r): (p["panel_t"] if d.get("jamb") == "lap" and d.get("jamb_master", "face") == "face" else 0.0)
            for r, d in opening_details(elev, p)} if p.get("reveals", True) else {}
    panels = _gap_at_jamb_lines(panels, holes, gap, grow)
    edges = [bw / 2, W - bw / 2]
    if posts:
        supports = dedupe_priority([posts, extra, edges], bw)
    elif jambs:
        # Set out from the openings: each span between jambs is its own bay, so the
        # spacing follows the openings. A cavity closer backs every jamb, so it counts
        # as support alongside the battens.
        supports = dedupe_priority([joints + jambs, edges], bw)
        extra = []
        for a, b in zip(supports, supports[1:]):
            extra += subdivide(a, b, MAX_BATTEN_SPAN)
        supports = dedupe_priority([supports, extra], bw)
    else:
        # One regular pitch, locked to the panel joints and carried through the end
        # spans, so sliding the set-out moves the whole grid: only the distance to the
        # edge battens changes, never the centres between.
        pitch = p["batten_centres"]
        anchor = joints[0] if joints else W / 2.0 + offset
        k0 = int(math.floor((bw / 2 - anchor) / pitch))
        grid = [anchor + k * pitch for k in range(k0, k0 + int(W / pitch) + 3)]
        grid = [u for u in grid if bw / 2 < u < W - bw / 2]
        supports = dedupe_priority([joints, grid, edges], bw)
    battens = [u for u in supports if all(abs(u - j) > bw / 2 for j in jambs + steps)]
    batten_back = depth
    depth = _vertical_battens(p, battens, meshes, name, frame, depth, H)
    for i, u in enumerate(reveal):
        # above and below the openings this line is a jamb of: beside them is the closer
        cut = sorted((o[2], o[3]) for o in holes if min(abs(o[0] - u), abs(o[1] - u)) < 1.0)
        lo = 0.0
        for k, (a, b) in enumerate(cut + [(H, H)]):
            if a - lo > 1.0:
                meshes.append(_prism(_rect(u - bw / 2, lo, u + bw / 2, a), batten_back, p["batten_d"], frame, "batten",
                                     "%s Batten R%d-%d" % (name, i + 1, k + 1), name))
            lo = max(lo, b)
    battens = sorted(battens + reveal)
    cavity_t = depth - cavity_start
    # The row list starts at the chain's datum, so rows line up round its corners; a face
    # that starts higher cuts the row at its base, one that starts lower carries on down.
    datum = _datum(elev)
    rows, short = panel_rows(elev.get("panel_rows"), p["panel_h"], gap, v0 if datum is None else datum, H, v0)
    joints, dissolved, dropped = _hjoints(elev, rows, panels, gap, region)
    # A noggin between vertical battens sits on the drainage plane and dams it, so the
    # horizontal seams are left unsupported unless a counter-batten layer holds the
    # battens off the wall and the water can run down behind them. With one, every row
    # joint gets its noggin, whatever the rows' heights.
    # A dissolved joint is no joint: no noggin behind it.
    for j, (v, h, _i, _f) in enumerate(rows[:-1] if p["has_cb"] else []):
        vj = v + h + gap / 2
        for k, (a, b) in enumerate(zip(battens, battens[1:])):
            bay = next((q for q, (s, e, _f) in enumerate(panels) if s <= (a + b) / 2 <= e), None)
            if b - a > bw + 1 and (j, bay) not in dissolved:
                meshes.append(_prism(_rect(a + bw / 2, vj - bw / 2, b - bw / 2, vj + bw / 2), depth - p["batten_d"],
                                     p["batten_d"], frame, "cross_batten", "%s Cross Batten R%d-%d" % (name, j + 1, k + 1), name))
    info["seam_noggins"] = bool(p["has_cb"]) and len(rows) > 1
    # A bay's cells joined by dissolved joints are one panel, drawn from its bottom cell:
    # lower + gap + upper, named for both cells. Every merge is within one bay, so it is
    # always a rectangle.
    for j, (v, h, _i, _f) in enumerate(rows):
        for k, (s, e, _full) in enumerate(panels):
            if (j - 1, k) in dissolved:
                continue
            t = j
            while (t, k) in dissolved:
                t += 1
            label = "R%d-%d" % (j + 1, k + 1) + ("..R%d-%d" % (t + 1, k + 1) if t > j else "")
            meshes.append(_prism(_rect(s, v, e, rows[t][0] + rows[t][1]), depth, p["panel_t"], frame, "panel",
                                 "%s Panel %s" % (name, label), name))
    face = depth + p["panel_t"]
    fulls = [pn for pn in panels if pn[2]]
    widths = sorted({round(e - s, 1) for s, e, _f in panels})
    info.update(n_courses=len(rows), cover=p["panel_w"] + gap, batten_centres=p["batten_centres"],
                # By list index from the datum, full height (the closing row as cut): what an
                # edit pads with. A row wholly under this face's base still holds its place.
                rows=_row_list(elev, p, rows),
                # and which list row each drawn row is, bottom up (None under the datum)
                row_index=[i for _v, _h, i, _f in rows],
                short_rows=short,
                closing_cut_left=(fulls[0][0] if fulls else W), closing_cut_right=(W - fulls[-1][1]) if fulls else 0.0,
                closing_cut_top=rows[-1][1] if rows else 0.0,
                n_full=len(fulls) * len(rows) - len([1 for _j, k in dissolved if panels[k][2]]),
                # the horizontal joints, one per bay, that the 2D view offers to dissolve
                hjoints=joints, panel_joints=[q for q in joints if q["dissolved"]], joints_dropped=dropped,
                set_out_from_openings=bool(jambs),
                panel_widths=widths, min_panel=(widths[0] if widths else 0.0))
    dims += _batten_dims(name, battens, p["batten_centres"], True, W, lock=_post_lock(info) if posts else LOCK_CENTRES)
    # The left closing cut and the first full panel set out the face: typing a cut solves
    # for the offset, typing the panel sets the panel width. Set out from the openings,
    # the bays follow the jambs instead and both are read-only.
    lock = {"lock": _post_lock(info)} if posts else ({"lock": LOCK_BAYS} if jambs else {})
    if fulls and fulls[0][0] > 1:
        dims.append(_dim(name, [0, H], [fulls[0][0], H], "Cut %.0f" % fulls[0][0], 300, [0, 1],
                         "cut_left", fulls[0][0], bay=p["panel_w"] + gap, **lock))
    if fulls:
        dims.append(_dim(name, [fulls[0][0], H], [fulls[0][1], H], "Panel %.0f" % (fulls[0][1] - fulls[0][0]), 300, [0, 1],
                         "panel_w", fulls[0][1] - fulls[0][0], **lock))
    for o in holes:
        dims.append(_dim(name, [o[0], o[3]], [o[1], o[3]], "Opening %.0f" % (o[1] - o[0]), 250, [0, 1],
                         "opening", o[1] - o[0], lock=LOCK_OPENING))
    # One dimension per row up the right-hand side, each naming the row it edits. The top
    # row is a row like any other (it can be split, or merged into from below), but its
    # height is whatever is left, so it is not typed.
    # Once the chain's datum is locked, a row cut at the base or under the datum is read-only
    # here: it is set on the face the datum comes from. Until then a cut bottom row can be
    # typed over, since that first edit is what locks the datum to this face's base.
    open_datum = not elev.get("course_datum_locked")
    start = v0 if datum is None else datum
    for j, (v, h, i, full) in enumerate(rows):
        top = j == len(rows) - 1
        if i is None:
            dims.append(_dim(name, [W, v], [W, v + h], "%.0f" % h, 300, [1, 0], lock=LOCK_UNDER_DATUM))
        elif j == 0 and v > start + 0.5 and not open_datum:          # cut at this face's base
            dims.append(_dim(name, [W, v], [W, v + h], "R%d %.0f" % (i + 1, h), 300, [1, 0], lock=LOCK_BASE_ROW))
        else:
            extra = {"fixed": FIXED_TOP_ROW} if top else {}
            dims.append(_dim(name, [W, v], [W, v + h], "R%d %.0f" % (i + 1, h), 300, [1, 0], "row", h, row=i, **extra))
    return face


def _hjoints(elev, rows, panels, gap, region):
    """The horizontal joints between rows, one per bay, and which of them are dissolved.

    A dissolved joint is stored on the elevation (elev["panel_joints"]) as its (row, bay)
    pair, drawn indices from 0, with the bay's span and the joint's level as it was when
    clicked: [row, bay, u0, u1, v]. It is kept only while the grid still has that joint
    in the same place, so a change underneath (row heights, panel width, offset) can
    never carry it onto another panel; the rest are dropped and counted. A joint is
    offered only where it crosses the clad area (not wholly in an opening or off the
    trimmed outline)."""
    from cladding_booleans import strip_intervals
    grid = {}
    for j in range(len(rows) - 1):
        vc = rows[j][0] + rows[j][1] + gap / 2.0
        runs = strip_intervals(region, vc - 0.5, vc + 0.5) if region is not None else [(-1e9, 1e9)]
        for k, (s, e, _f) in enumerate(panels):
            spans = [[round(max(a, s), 1), round(min(b, e), 1)] for a, b in runs if min(b, e) - max(a, s) >= 10.0]
            if spans:
                grid[(j, k)] = (s, e, vc, spans)
    dissolved, dropped = set(), 0
    for q in elev.get("panel_joints") or []:
        try:
            key, at = (int(q[0]), int(q[1])), [float(x) for x in q[2:5]]
        except (TypeError, ValueError, IndexError):
            dropped += 1
            continue
        g = grid.get(key)
        if g and len(at) == 3 and all(abs(a - b) < 0.5 for a, b in zip(g[:3], at)):
            dissolved.add(key)
        else:
            dropped += 1

    def group(j, k):                     # the rows a panel of this cell spans, bottom and top
        lo, hi = j, j
        while (lo - 1, k) in dissolved:
            lo -= 1
        while (hi, k) in dissolved:
            hi += 1
        return lo, hi
    joints = []
    for (j, k), (s, e, vc, spans) in sorted(grid.items()):
        lo, top = group(j, k)[0], group(j + 1, k)[1]
        joints.append({"row": j, "bay": k, "u0": round(s, 1), "u1": round(e, 1), "v": round(vc, 1), "spans": spans,
                       "dissolved": (j, k) in dissolved,
                       # the panel this bay makes across the joint when it is dissolved
                       "size": [round(e - s, 1), round(rows[top][0] + rows[top][1] - rows[lo][0], 1)]})
    return joints, dissolved, dropped


def _row_list(elev, p, rows):
    idxs = [i for _v, _h, i, _f in rows if i is not None]
    if not idxs:
        return []
    out = [round(row_height(elev.get("panel_rows"), k, p["panel_h"]), 1) for k in range(max(idxs) + 1)]
    if rows[-1][2] is not None:
        out[rows[-1][2]] = round(rows[-1][1], 1)            # the closing row, as cut
    return out


BARRIER_BESIDE = 25.0     # mm – an upright timber this close beside a barrier counts as joining it: no way past for air
BARRIER_STUB = 150.0      # mm – a batten end shorter than this, left between a barrier's clear zone and the edge, is left out
UPRIGHTS = ("batten", "counter_batten", "cross_batten")


def barrier_zones(p, elev):
    """Where openings are closed with solid timber cavity barriers on all four sides
    (p["barriers"] for the chain, or set on the opening itself: opening_details): one
    record for each such opening of the face.

    The jamb closers every opening has are joined by a barrier over the head and one under
    the sill, each the closer's width and running out over the jambs', so the four make a
    frame filling the cavity round the opening. That shuts the bays above and below the
    opening: air rising under the sill, or falling over the head, has to pass sideways into
    the bays that run on beside the opening. So every upright batten that would join the
    head or sill barrier **stops short of it** by p["barrier_gap"] (200 mm at least), and
    the strip left is the way across.

    Each record: "index" the opening's place among the face's; "rect" the opening; "head" and "sill" the barriers as (u0, v0, u1, v1), or
    None where the head or sill is not within the cladding; "solid" the frame's outside;
    "clear" the box no upright batten enters (the frame, the gap above and below it)."""
    cw, gap = p["closer_w"], max(200.0, float(p.get("barrier_gap") or 200.0))
    band_lo, band_hi = clip_bounds_v(elev)
    out = []
    for index, ((u0, u1, v0, v1), d) in enumerate(opening_details(elev, p)):
        if not d["barriers"]:
            continue
        head_in, sill_in = v1 < band_hi - 0.5, v0 > band_lo + 0.5
        top = min(v1 + cw, band_hi) if head_in else min(v1, band_hi)
        bot = max(v0 - cw, band_lo) if sill_in else max(v0, band_lo)
        a, b = u0 - cw, u1 + cw
        hi = top + gap if head_in else top
        lo = bot - gap if sill_in else bot
        # no stub of a batten between the gap and the top or bottom of the cladding
        hi = band_hi + 1.0 if head_in and band_hi - hi < BARRIER_STUB else hi
        lo = band_lo - 1.0 if sill_in and lo - band_lo < BARRIER_STUB else lo
        out.append({"index": index, "rect": (u0, u1, v0, v1), "head": (a, v1, b, top) if head_in and top - v1 > 1.0 else None,
                    "sill": (a, bot, b, v0) if sill_in and v0 - bot > 1.0 else None,
                    "solid": (a, bot, b, top), "clear": (a - BARRIER_BESIDE, lo, b + BARRIER_BESIDE, hi), "gap": gap})
    return out


def _clear_of_barriers(zones, u, v_lo, v_hi, half=0.0):
    """Is the line u (a batten *half* wide each side of it) free of every barrier's clear
    box between these heights? False where a batten there has been cut away."""
    return not any(z["clear"][0] < u + half and u - half < z["clear"][2] and z["clear"][1] < v_hi and v_lo < z["clear"][3]
                   for z in zones)


def _barriers(p, elev, meshes, zones, cavity_start, cavity_t):
    """The head and sill barriers of each opening closed on all four sides (barrier_zones),
    and the battens cut clear of them: uprights out of the whole clear box, level battens
    out of the frame itself (the barrier is solid timber where they were). The cut is made
    when the parts are trimmed (cladding_booleans: "keep_clear"). Returns
    {"openings", "gap", "cut": how many battens stop short, "dpm": how many timbers carry a membrane}."""
    name, frame, cut, dpm = elev["name"], elev["frame"], set(), 0
    for z in zones:
        for key, tag in (("head", "H"), ("sill", "S")):
            if z[key]:
                meshes.append(_prism(_rect(*z[key]), cavity_start, cavity_t, frame, "closer",
                                     "%s Cavity Barrier %d%s" % (name, z["index"] + 1, tag), name))
        # A damp proof membrane on all four timbers of the frame, between each and the back
        # of the cladding (drawn in the back 1 mm of the boards, as a gasket is, so nothing
        # moves). The jamb closers are the opening's own, built already.
        tags = {"%s Cavity %s %d%s" % (name, word, z["index"] + 1, tag): tag
                for word, tag in (("Closer", "L"), ("Closer", "R"), ("Barrier", "H"), ("Barrier", "S"))}
        for m in [m for m in meshes if m["ifc_type"] == "closer" and m["name"] in tags]:
            m["dpm"] = True
            meshes.append(_prism([list(q) for q in m["profile"]], float(m["depth"]) + float(m["thickness"]), DPM_T, frame, "dpm",
                                 "%s Barrier DPM %d%s" % (name, z["index"] + 1, tags[m["name"]]), name))
            dpm += 1
        # And over the top of the two level timbers, the head's and the sill's, the full depth
        # of the cavity: a level top is where water would otherwise stand.
        for key, tag in (("head", "H"), ("sill", "S")):
            if z[key]:
                a, _lo, b, top = z[key]
                meshes.append(_prism(_rect(a, top, b, top + DPM_T), cavity_start, cavity_t + DPM_T, frame, "dpm",
                                     "%s Barrier DPM Top %d%s" % (name, z["index"] + 1, tag), name))
                dpm += 1
        # And down the window side of the two upright timbers, the jambs': the face that
        # looks into the opening, behind the reveal lining.
        for m in [m for m in meshes if m["ifc_type"] == "closer" and tags.get(m["name"]) in ("L", "R")]:
            us, vs = [q[0] for q in m["profile"]], [q[1] for q in m["profile"]]
            at = max(us) if tags[m["name"]] == "L" else min(us) - DPM_T
            meshes.append(_prism(_rect(at, min(vs), at + DPM_T, max(vs)), cavity_start, cavity_t + DPM_T, frame, "dpm",
                                 "%s Barrier DPM Side %d%s" % (name, z["index"] + 1, tags[m["name"]]), name))
            dpm += 1
        for m in meshes:
            if m.get("elevation") != name or m["ifc_type"] not in UPRIGHTS or m.get("clip_v_at"):
                continue
            us, vs = [q[0] for q in m["profile"]], [q[1] for q in m["profile"]]
            upright = max(vs) - min(vs) > max(us) - min(us)
            a, lo, b, hi = z["clear"] if upright else z["solid"]
            if min(us) < b - 0.5 and max(us) > a + 0.5 and min(vs) < hi - 0.5 and max(vs) > lo + 0.5:
                m.setdefault("keep_clear", []).append([a, lo, b, hi])
                if upright:
                    cut.add(m["name"])
    return {"openings": len(zones), "gap": zones[0]["gap"] if zones else 0.0, "cut": len(cut), "dpm": dpm}


def _openings_extras(p, elev, meshes, holes, cavity_start, cavity_t, face, board, prof, base):
    """Solid timber cavity closers at both vertical sides of every opening, and the
    reveal linings: one up each jamb and one under the head. The face boards meet them
    with the opening's corner detail — mitred by default, across the chain's mitre gap;
    Master, Profile or Square as chosen (cladding_edges.reveal_treatment). Both jambs
    share one detail, mirrored; the head has its own. The boards' side is applied after
    trimming (cladding_edges.mitre_to_linings). Sills are not lined."""
    name, frame = elev["name"], elev["frame"]
    cw = p["closer_w"]
    s = edge_settings(elev)
    pull = mitre_pullback(s["gap"], 1.0)
    # Vented at the back, the head lining drops by the air space and stops the same
    # distance short of the window frame (cladding_edges.opening_frames), so air runs over it
    # and out at the frame. Vented at the front it is tight to the head and the open
    # joint at the front edge is the vent.
    air = s["air"] if s["vent"] == "back" else 0.0
    details = dict((tuple(r), d) for r, d in opening_details(elev, p))
    frames = opening_frames(elev, holes, face)
    band_lo, band_hi = clip_bounds_v(elev)
    for i, (u0, u1, v0, v1) in enumerate(holes):
        d = details.get((u0, u1, v0, v1), {})
        # An opening can cross the clad top or bottom, or run the full height: its closers
        # and jamb linings stop at the clad band, and it has a head lining only if its head
        # is within the cladding.
        jv0, jv1 = max(v0, band_lo), min(v1, band_hi)
        head_in = v1 < band_hi - 0.5
        # The linings run from the cladding face back to the window or door frame, tight
        # to its outer face (cladding_edges.opening_frames): past the wall face where the
        # frame is set back, short of it where the frame stands forward.
        reach = max(board, face - frames[(u0, u1, v0, v1)]["depth"])
        back = 0.0 if p["cladding_type"] == "panel" else profiles.TRIM_BACK
        jamb = reveal_treatment(d.get("jamb", "mitre"), d.get("jamb_master", "face"), face, pull, board, p["panel_gap"], s["pgap"], back)[1]
        head_t = reveal_treatment(d.get("head", "mitre"), d.get("head_master", "face"), face, pull, board, p["panel_gap"], s["pgap"], back)[1]
        # Standing seam: the linings are the trays' metal, square-ended, SEAM_STANDOFF off the
        # reveal behind them; a jamb's runs on out as the upstand of the seam on its corner.
        so = SEAM_STANDOFF if p.get("seam") else 0.0
        if so:
            jamb = head_t = None
        for side, u in ((-1.0, u0), (1.0, u1)):
            # The closer sits in the wall side of the jamb and fills the cavity.
            a, b = (u - cw, u) if side < 0 else (u, u + cw)
            meshes.append(_prism(_rect(a, jv0, b, jv1), cavity_start, cavity_t, frame, "closer",
                                 "%s Cavity Closer %d%s" % (name, i + 1, "L" if side < 0 else "R"), name))
            if not p["reveals"]:
                continue
            lining = _prism(_rect(0.0, jv0, reach, jv1), 0.0, board,
                            _reveal_frame(frame, u - side * so, face, -side), "reveal",
                            "%s Reveal %d%s" % (name, i + 1, "L" if side < 0 else "R"), name)
            if jamb:
                lining["corner"] = {"k_l": jamb[0], "ext_l": jamb[1], "u_l": 0.0}
            meshes.append(lining)
        ua, ub = u0 + so + board, u1 - so - board            # between the jamb linings
        if not p["reveals"]:
            continue
        if ub - ua > 1.0 and head_in:
            head = _prism(_rect(0.0, 0.0, max(board, reach - air), ub - ua), 0.0, board,
                          _head_frame(frame, ua, v1 - air - so, face), "reveal", "%s Reveal %dH" % (name, i + 1), name)
            if head_t:
                head["corner"] = {"k_l": head_t[0], "ext_l": head_t[1], "u_l": 0.0}
            meshes.append(head)
        # Corner profiles at the arris, flange A on the elevation face. A jamb's runs up
        # to the underside of the head's; with panels the head's runs between the jamb
        # linings. With planks the Marley-type nose stands TRIM_LEG out from the boards'
        # backs (the face board's and the lining's) and its head runs on over the jambs'.
        plank = p["cladding_type"] != "panel"
        nose, setb = (profiles.TRIM_SIZE, profiles.TRIM_LEG) if plank else (board, board)
        flange = profiles.FLANGE_P if plank else profiles.FLANGE_A
        out_d = face - board + setb                           # the nose's outer face, as depth
        z_c = v1 - air - board if head_in else jv1            # the head lining's underside, or the clad top
        if head_in:
            z_c = v1 - air - setb                             # the head nose's outer face
        if d.get("jamb") == "profile":
            for side, u_c in ((1.0, u0 + setb), (-1.0, u1 - setb)):
                lo_v = max(v0, base + s["bottom"]) if v0 <= base else v0
                m = profiles.vertical_profile(frame, u_c, side, lo_v, z_c, out_d, board,
                                              "%s Jamb Profile %d%s" % (name, i + 1, "L" if side > 0 else "R"), name,
                                              trim=plank)
                if m:
                    meshes.append(m)
                    prof["count"] += 1
                    prof["strips"].append(profiles.strip(*sorted((u_c, u_c - side * nose)), lo_v, z_c))
                    span = sorted((u_c - side * nose, u_c - side * (nose + flange)))
                    if not profiles.flange_supported(meshes, name, span, (lo_v, z_c), face - board):
                        prof["warnings"].append("%s: a jamb profile's flange at opening %d is not over a batten or closer"
                                                % (name, i + 1))
        if d.get("head") == "profile" and head_in:
            ha, hb = (u0 + setb - nose, u1 - setb + nose) if plank else (ua, ub)
            m = profiles.head_profile(frame, ha, hb, z_c, out_d, board, "%s Head Profile %d" % (name, i + 1), name,
                                      trim=plank)
            if m:
                meshes.append(m)
                prof["count"] += 1
                prof["strips"].append(profiles.strip(ha, hb, z_c, z_c + nose))
                if not profiles.flange_supported(meshes, name, (ha, hb), (z_c + nose, z_c + nose + flange),
                                                 face - board):
                    prof["warnings"].append("%s: the head profile's flange at opening %d is not over a batten"
                                            % (name, i + 1))


def _head_frame(frame, u_start, v_head, face_depth):
    """Frame of a head lining, in the general frame: it lies under the head facing down
    the face (n = -v: world -Z on a wall, down the slope on a pitched face, so the lining
    is square to the face), u runs inward from the cladding face like a jamb lining's,
    and v = n × u runs along the wall. The mitre at its front end is then the same end
    shear as a jamb lining's."""
    n, uw, up = frame["n"], frame["u"], frame_v(frame)
    return {"origin": frame_to_world(frame, u_start, v_head, face_depth),
            "u": [-n[0], -n[1], -n[2]], "v": [uw[0], uw[1], uw[2]], "n": [-up[0], -up[1], -up[2]]}


def _reveal_frame(frame, u_jamb, face_depth, sign):
    """Frame of a reveal lining: u runs inward from the cladding face, and the outward
    normal faces into the opening (sign = +1 where the opening is at greater u)."""
    n = frame["n"]
    out = {"origin": frame_to_world(frame, u_jamb, 0.0, face_depth),
           "u": [-n[0], -n[1], -n[2]],
           "n": [sign * frame["u"][0], sign * frame["u"][1], sign * frame["u"][2]]}
    if frame_v(frame)[2] != 1.0:
        out["v"] = list(frame_v(frame))         # up the slope: a wall's is world Z, the default
    return out
