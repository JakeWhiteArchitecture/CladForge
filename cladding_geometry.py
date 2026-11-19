"""
CladForge — buildup and board setting-out for one extracted elevation.

Works entirely in the elevation's local frame: u runs along the wall, v runs
up it, depth runs outward from the wall face. Elements are emitted untrimmed
(full rectangles); cladding_booleans.py cuts them at openings and splash zones.
"""

import math

from cladding_constants import _prism, _rect, MAX_BATTEN_SPAN, EPDM_T, EPDM_LAP, SEAM_AIR, SEAM_HEAD_D, SEAM_STANDOFF
from cladding_booleans import clip_region, strip_intervals, iter_polygons, polygon_to_rings
from cladding_constants import frame_to_world
from cladding_edges import (classify_edges, mitre_pullback, offset_region, opening_details, reveal_treatment,
                            opening_key, opening_frames, settings as edge_settings)
import cladding_corners as profiles
from cladding_primitives import (centred_positions, stacked_positions, batten_positions, dedupe,
                                 dedupe_priority, subdivide, panel_bays, bays_between, split_run,
                                 base_level, corner_ends, openings, buildup_depth, clip_bounds,
                                 clip_bounds_v, panel_rows, row_height, bays_on_posts)


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
    _openings_extras(p, elev, meshes, holes, layers, depth - board - layers, depth, board, prof, v0)
    info["openings"] = len(holes)
    frames = opening_frames(elev, holes, depth)
    info["opening_details"] = [dict(d, key=opening_key(r), rect=list(r), frame_depth=frames[r]["depth"],
                                    frame_from=frames[r]["from"], frame_name=frames[r]["name"],
                                    frame_note=frames[r]["note"])
                               for r, d in opening_details(elev, p)]

    # Each end takes its own corner's detail: a lap at one end and a mitre at the other
    # treat the layers behind the boards differently, so the ends are applied apart.
    left, right, (d_left, d_right) = corner_ends(elev, p)
    _apply_corner(meshes, {lo: left}, d_left, skip=("reveal", "closer"))
    _apply_corner(meshes, {hi: right}, d_right, skip=("reveal", "closer"))
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
    return None if datum is None else float(datum) - float(elev["frame"]["origin"][2])


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
    out = _widths_string(name, list(zip(greens, greens[1:])), H)
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
                        lock=LOCK_POSTS, ours=round(u, 1)))
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


def post_state(elev, u):
    """A model column's use, as toggled in the 2D view: "on" (a batten a joint may land on,
    the default), "fix" (a batten for fixing only: no joint on it), "off" (not used)."""
    st = (elev.get("post_states") or {}).get(post_key(u), "on")
    return st if st in POST_STATES else "on"


def _posts(p, elev, info):
    """Centrelines for the vertical battens from the columns the client's model has standing
    on the face (fabric_extract._post_of), when the option is on and there are any: the
    designer set them out to suit the frame behind, so ours go on the same lines and the
    offset slider has nothing to move. A column toggled "off" in the 2D view is left out;
    one toggled "fix" is a batten but takes no joint (_post_fixings). None if none are left.
    Every column and its state go to info["posts"] for the 2D view."""
    cols = sorted(elev.get("posts") or [], key=lambda q: q["u"]) if p.get("battens_from_model", True) else []
    info["posts"] = [{"u": round(float(q["u"]), 1), "v0": q.get("v0"), "v1": q.get("v1"), "w": q.get("w"),
                      "state": post_state(elev, q["u"])} for q in cols]
    us = [q["u"] for q in info["posts"] if q["state"] != "off"]
    info["model_posts"] = len(us)
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
    for j, v in enumerate(courses):
        # Set each course out along the part of the face it actually crosses, so a run
        # broken by a gable or an opening gets no seam it does not need.
        lo = max(v, v0)          # a course set out from the chain datum is cut at the base
        if v + face - lo < 1.0:
            beside, below = [], []
            continue
        runs = strip_intervals(region, lo, v + face) if region is not None else [(0.0, W)]
        piece, mine = 0, []
        for a, b in runs:
            segs, bad = split_run(a, b, p["plank_len"], stops, j % 2 == 1, gap, p["plank_min"], p["plank_target"],
                                  beside, p["batten_centres"])
            mine += _joints(segs)
            info["unsupported_joints"] += len(bad)
            for s, e in segs:
                piece += 1
                label = "%s %s C%d-%d" % (name, "Tray" if p["seam"] else "Plank", j + 1, piece)
                ring = _rect(s, lo, e, v + face)
                meshes.append(_tray(p, ring, depth, frame, label, name) if p["seam"]
                              else _prism(ring, depth, p["plank_t"], frame, "plank", label, name))
                if p["seam"]:                        # the seam with the tray below, along its bottom edge
                    for a2, b2 in _shared(s, e, below):
                        _seam(p, meshes, frame, label, name, depth + p["plank_t"], False, lo, a2, b2)
        beside, below = mine, runs
    info.update(n_courses=len(courses), cover=cover, batten_centres=p["batten_centres"],
                closing_cut_top=(H - courses[-1]) if courses else 0.0,
                closing_cut_left=0.0, closing_cut_right=0.0)
    dims += _batten_dims(name, battens, p["batten_centres"], True, W, lock=LOCK_POSTS if posts else None)
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
    starts = centred_positions(W, cover, offset, face)
    beside = []                  # the joints in the plank to the left: this one keeps clear of them
    for j, u in enumerate(starts):
        runs = strip_intervals(region, u, u + face, across=True) if region is not None else [(v0, H)]
        piece, mine = 0, []
        for a, b in runs:
            segs, bad = split_run(max(a, v0), b, p["plank_len"], battens, j % 2 == 1, gap, p["plank_min"], p["plank_target"],
                                  beside, p["batten_centres"])
            mine += _joints(segs)
            info["unsupported_joints"] += len(bad)
            for s, e in segs:
                piece += 1
                meshes.append(_prism(_rect(u, s, u + face, e), depth, p["plank_t"], frame, "plank",
                                     "%s Plank C%d-%d" % (name, j + 1, piece), name))
        beside = mine
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


def seam_columns(W, cover, offset, jambs):
    """The tray columns (u0, u1) across a face *W* wide. Set out from the openings, each
    jamb is a seam line, carried straight up and down past the opening, and each span between
    jambs (and the face's ends) is split into equal trays no wider than *cover*: no narrow
    closing cut. Otherwise trays at *cover* centres, centred on the face plus *offset*."""
    lines = []
    for u in sorted(jambs):
        if 1.0 < u < W - 1.0 and not (lines and u - lines[-1] < SEAM_JAMB_MERGE):
            lines.append(u)
    if lines:
        return [(a, b) for a, b, _full in bays_between([0.0] + lines + [W], cover, 0.0)[0]]
    return [(u, u + cover) for u in centred_positions(W, cover, offset, cover)]


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
    cols = seam_columns(W, cover, offset, jambs)
    from_openings = bool(jambs) and len(cols) > 0 and abs(cols[0][0]) < 1e-6 and abs(cols[-1][1] - W) < 1e-6
    beside, laid = [], []        # the joints of the column to the left; every column's runs and trays
    for j, (u, u1) in enumerate(cols):
        runs = strip_intervals(region, u, u1, across=True) if region is not None else [(v0, H)]
        runs = [(max(a, v0), b) for a, b in runs if b - max(a, v0) > 1.0]
        piece, mine, trays = 0, [], []
        for a, b in runs:
            segs, bad = split_run(a, b, p["plank_len"], stops, j % 2 == 1, 0.0, p["plank_min"], p["plank_target"],
                                  beside, p["batten_centres"])
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
    dims += _batten_dims(name, battens, p["batten_centres"], True, W, lock=LOCK_POSTS if posts else None)
    if cols:
        u0, u1 = cols[0]
        lock = {"lock": LOCK_SEAMS} if from_openings else {}
        dims.append(_dim(name, [max(0, u0), H], [min(W, u1), H],
                         ("Tray %.0f" % (u1 - u0)) if u0 >= 0 else ("Cut %.0f" % left_cut), 300, [0, 1],
                         None, u1 - max(0, u0), **lock))
    return depth + p["plank_t"]


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
    reveal = []
    if posts:
        # The client's columns fix the battens: every joint goes on one, and each reveal
        # line is a joint carried straight up past the opening and on down below it, on a
        # batten above and below the opening (beside it the closer carries the joint):
        # the column's own where one is there, one of ours where not. A joint with no
        # column in reach gets a batten too.
        fixings = _post_fixings(info)
        on = [u for u in posts if all(abs(u - f) > 0.5 for f in fixings)]   # the columns a joint may use
        panels, joints, extra = bays_on_posts([0.0] + jambs + [W], on, p["panel_w"], gap)
        reveal = list(jambs)
        info["post_extra"] = len(extra)
        info["reveal_battens"] = len([u for u in jambs if all(abs(u - q) > bw / 2 for q in posts)])
    elif jambs:
        panels, joints = bays_between([0.0] + jambs + [W], p["panel_w"], gap)
    else:
        panels, joints = panel_bays(W, p["panel_w"], gap, offset)
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
    battens = [u for u in supports if all(abs(u - j) > bw / 2 for j in jambs)]
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
    dims += _batten_dims(name, battens, p["batten_centres"], True, W, lock=LOCK_POSTS if posts else LOCK_CENTRES)
    # The left closing cut and the first full panel set out the face: typing a cut solves
    # for the offset, typing the panel sets the panel width. Set out from the openings,
    # the bays follow the jambs instead and both are read-only.
    lock = {"lock": LOCK_POSTS} if posts else ({"lock": LOCK_BAYS} if jambs else {})
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
    """Frame of a head lining, in the general frame: it lies flat under the head facing
    down (n = -Z), u runs inward from the cladding face like a jamb lining's, and
    v = n × u runs along the wall. The mitre at its front end is then the same end
    shear as a jamb lining's."""
    n, uw = frame["n"], frame["u"]
    return {"origin": frame_to_world(frame, u_start, v_head, face_depth),
            "u": [-n[0], -n[1], 0.0], "v": [uw[0], uw[1], 0.0], "n": [0.0, 0.0, -1.0]}


def _reveal_frame(frame, u_jamb, face_depth, sign):
    """Frame of a reveal lining: u runs inward from the cladding face, and the outward
    normal faces into the opening (sign = +1 where the opening is at greater u)."""
    n = frame["n"]
    return {"origin": frame_to_world(frame, u_jamb, 0.0, face_depth),
            "u": [-n[0], -n[1], 0.0],
            "n": [sign * frame["u"][0], sign * frame["u"][1], 0.0]}
