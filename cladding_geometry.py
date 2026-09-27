"""
CladForge — buildup and board setting-out for one extracted elevation.

Works entirely in the elevation's local frame: u runs along the wall, v runs
up it, depth runs outward from the wall face. Elements are emitted untrimmed
(full rectangles); cladding_booleans.py cuts them at openings and splash zones.
"""

import math

from cladding_constants import _prism, _rect, MAX_BATTEN_SPAN
from cladding_booleans import clip_region, strip_intervals
from cladding_constants import frame_to_world
from cladding_primitives import (centred_positions, stacked_positions, batten_positions, dedupe,
                                 dedupe_priority, subdivide, panel_bays, bays_between, split_run,
                                 base_level, corner_ends, openings, buildup_depth, clip_bounds,
                                 clip_bounds_v, panel_rows)


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

    region = clip_region(elev, p["splash"])
    layers = depth                       # depth of the face the cavity starts from
    if p["cladding_type"] == "panel":
        depth = _panels(p, elev, meshes, dims, info, depth, W, H, v0, offset)
    elif p["battens"] == "vertical":
        depth = _horizontal_planks(p, elev, meshes, dims, info, depth, W, H, v0, offset, region)
    else:
        depth = _vertical_planks(p, elev, meshes, dims, info, depth, W, H, v0, offset, region)
    # Openings are closed and lined whatever the cladding is: the closer is solid
    # timber filling the cavity, so it does not depend on the board above it.
    board = p["panel_t"] if p["cladding_type"] == "panel" else p["plank_t"]
    holes = openings(elev)
    _openings_extras(p, elev, meshes, holes, layers, depth - board - layers, depth, board)
    info["openings"] = len(holes)

    # Each end takes its own corner's detail: a lap at one end and a mitre at the other
    # treat the layers behind the boards differently, so the ends are applied apart.
    left, right, (d_left, d_right) = corner_ends(elev, p)
    _apply_corner(meshes, {lo: left}, d_left, skip=("reveal", "closer"))
    _apply_corner(meshes, {hi: right}, d_right, skip=("reveal", "closer"))
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
    info["n_boards"] = sum(1 for m in meshes if m["ifc_type"] in ("plank", "panel"))
    return meshes, dims, info


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


def _horizontal_planks(p, elev, meshes, dims, info, depth, W, H, v0, offset, region=None):
    """Horizontal boards on vertical battens (optionally on horizontal counter-battens)."""
    name, frame = elev["name"], elev["frame"]
    if p["has_cb"]:  # forced by override — flagged by check_rules, drainage is compromised
        vs = dedupe(stacked_positions(v0 + p["cb_w"] / 2, H, p["cb_centres"]) + [H - p["cb_w"] / 2], p["cb_w"])
        depth = _horizontal_battens(p, vs, meshes, name, frame, depth, W, "counter_batten", p["cb_w"], p["cb_d"])
    battens = batten_positions(W, p["batten_w"], p["batten_centres"], offset)
    depth = _vertical_battens(p, battens, meshes, name, frame, depth, H)
    cover, face = p["cover"], p["plank_w"]
    gap = p["plank_gap"] if p["plank_lap"] <= 0 else 0.0
    courses = _courses(elev, v0, H, cover)
    for j, v in enumerate(courses):
        # Set each course out along the part of the face it actually crosses, so a run
        # broken by a gable or an opening gets no seam it does not need.
        lo = max(v, v0)          # a course set out from the chain datum is cut at the base
        if v + face - lo < 1.0:
            continue
        runs = strip_intervals(region, lo, v + face) if region is not None else [(0.0, W)]
        piece = 0
        for a, b in runs:
            segs, bad = split_run(a, b, p["plank_len"], battens, j % 2 == 1, gap)
            info["unsupported_joints"] += len(bad)
            for s, e in segs:
                piece += 1
                meshes.append(_prism(_rect(s, lo, e, v + face), depth, p["plank_t"], frame, "plank",
                                     "%s Plank C%d-%d" % (name, j + 1, piece), name))
    info.update(n_courses=len(courses), cover=cover, batten_centres=p["batten_centres"],
                closing_cut_top=(H - courses[-1]) if courses else 0.0,
                closing_cut_left=0.0, closing_cut_right=0.0)
    dims += _batten_dims(name, battens, p["batten_centres"], True, W)
    full = [v for v in courses if v >= v0 - 0.5]
    if full:
        dims.append(_dim(name, [W, full[0]], [W, full[0] + cover], "Course %.0f" % cover, 300, [1, 0],
                         "course", cover))
        dims.append(_dim(name, [W, courses[-1]], [W, H], "Cut %.0f" % (H - courses[-1]), 600, [1, 0]))
    return depth + p["plank_t"]


def _vertical_planks(p, elev, meshes, dims, info, depth, W, H, v0, offset, region=None):
    """Vertical boards on horizontal battens on vertical counter-battens."""
    name, frame = elev["name"], elev["frame"]
    if p["has_cb"]:
        cbs = batten_positions(W, p["cb_w"], p["cb_centres"], 0.0)
        depth = _vertical_battens(p, cbs, meshes, name, frame, depth, H, "counter_batten", p["cb_w"], p["cb_d"])
    bw = p["batten_w"]
    battens = dedupe(stacked_positions(v0 + bw / 2, H - bw / 2, p["batten_centres"]) + [H - bw / 2], bw)
    depth = _horizontal_battens(p, battens, meshes, name, frame, depth, W)
    cover, face = p["cover"], p["plank_w"]
    gap = p["plank_gap"] if p["plank_lap"] <= 0 else 0.0
    starts = centred_positions(W, cover, offset, face)
    for j, u in enumerate(starts):
        runs = strip_intervals(region, u, u + face, across=True) if region is not None else [(v0, H)]
        piece = 0
        for a, b in runs:
            segs, bad = split_run(max(a, v0), b, p["plank_len"], battens, j % 2 == 1, gap)
            info["unsupported_joints"] += len(bad)
            for s, e in segs:
                piece += 1
                meshes.append(_prism(_rect(u, s, u + face, e), depth, p["plank_t"], frame, "plank",
                                     "%s Plank C%d-%d" % (name, j + 1, piece), name))
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


def _panels(p, elev, meshes, dims, info, depth, W, H, v0, offset):
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
    if jambs:
        panels, joints = bays_between([0.0] + jambs + [W], p["panel_w"], gap)
    else:
        panels, joints = panel_bays(W, p["panel_w"], gap, offset)
    edges = [bw / 2, W - bw / 2]
    if jambs:
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
    depth = _vertical_battens(p, battens, meshes, name, frame, depth, H)
    cavity_t = depth - cavity_start
    # The row list starts at the chain's datum, so rows line up round its corners; a face
    # that starts higher cuts the row at its base, one that starts lower carries on down.
    datum = _datum(elev)
    rows, short = panel_rows(elev.get("panel_rows"), p["panel_h"], gap, v0 if datum is None else datum, H, v0)
    # A noggin between vertical battens sits on the drainage plane and dams it, so the
    # horizontal seams are left unsupported unless a counter-batten layer holds the
    # battens off the wall and the water can run down behind them. With one, every row
    # joint gets its noggin, whatever the rows' heights.
    for j, (v, h, _i, _f) in enumerate(rows[:-1] if p["has_cb"] else []):
        vj = v + h + gap / 2
        for k, (a, b) in enumerate(zip(battens, battens[1:])):
            if b - a > bw + 1:
                meshes.append(_prism(_rect(a + bw / 2, vj - bw / 2, b - bw / 2, vj + bw / 2), depth - p["batten_d"],
                                     p["batten_d"], frame, "cross_batten", "%s Cross Batten R%d-%d" % (name, j + 1, k + 1), name))
    info["seam_noggins"] = bool(p["has_cb"]) and len(rows) > 1
    for j, (v, h, _i, _f) in enumerate(rows):
        for k, (s, e, _full) in enumerate(panels):
            meshes.append(_prism(_rect(s, v, e, v + h), depth, p["panel_t"], frame, "panel",
                                 "%s Panel R%d-%d" % (name, j + 1, k + 1), name))
    face = depth + p["panel_t"]
    fulls = [pn for pn in panels if pn[2]]
    widths = sorted({round(e - s, 1) for s, e, _f in panels})
    info.update(n_courses=len(rows), cover=p["panel_w"] + gap, batten_centres=p["batten_centres"],
                # by list index, full height (the closing row as cut): what an edit pads with
                rows=[round(h if k == len(rows) - 1 else f, 1) for k, (_v, h, i, f) in enumerate(rows) if i is not None],
                short_rows=short,
                closing_cut_left=(fulls[0][0] if fulls else W), closing_cut_right=(W - fulls[-1][1]) if fulls else 0.0,
                closing_cut_top=rows[-1][1] if rows else 0.0, n_full=len(fulls) * len(rows),
                set_out_from_openings=bool(jambs),
                panel_widths=widths, min_panel=(widths[0] if widths else 0.0))
    dims += _batten_dims(name, battens, p["batten_centres"], True, W, lock=LOCK_CENTRES)
    # The left closing cut and the first full panel set out the face: typing a cut solves
    # for the offset, typing the panel sets the panel width. Set out from the openings,
    # the bays follow the jambs instead and both are read-only.
    lock = {"lock": LOCK_BAYS} if jambs else {}
    if fulls and fulls[0][0] > 1:
        dims.append(_dim(name, [0, H], [fulls[0][0], H], "Cut %.0f" % fulls[0][0], 300, [0, 1],
                         "cut_left", fulls[0][0], bay=p["panel_w"] + gap, **lock))
    if fulls:
        dims.append(_dim(name, [fulls[0][0], H], [fulls[0][1], H], "Panel %.0f" % (fulls[0][1] - fulls[0][0]), 300, [0, 1],
                         "panel_w", fulls[0][1] - fulls[0][0], **lock))
    for o in holes:
        dims.append(_dim(name, [o[0], o[3]], [o[1], o[3]], "Opening %.0f" % (o[1] - o[0]), 250, [0, 1],
                         "opening", o[1] - o[0], lock=LOCK_OPENING))
    # One dimension per row up the right-hand side, each naming the row it edits; the
    # top row is the closing cut, which is whatever is left, so it is read-only.
    # A row cut at the base, or one under the chain's datum, is read-only here: it is set
    # on the face the datum comes from.
    for j, (v, h, i, full) in enumerate(rows):
        if j < len(rows) - 1 and i is not None and h >= full - 0.5:
            dims.append(_dim(name, [W, v], [W, v + h], "R%d %.0f" % (i + 1, h), 300, [1, 0], "row", h, row=i))
        else:
            dims.append(_dim(name, [W, v], [W, v + h], ("Cut %.0f" if h < full - 0.5 or j == len(rows) - 1 else "%.0f") % h,
                             300, [1, 0]))
    return face


def _openings_extras(p, elev, meshes, holes, cavity_start, cavity_t, face, board):
    """Solid timber cavity closers at both vertical sides of every opening, and the
    reveal linings, which the face board is always mitred to whatever the corner
    detail is. Heads and sills are not lined: a frame whose v is world Z cannot
    describe a surface that faces up or down."""
    name, frame = elev["name"], elev["frame"]
    cw = p["closer_w"]
    jamb_cut = (-1.0, face)                    # bisector of the arris at the reveal
    for i, (u0, u1, v0, v1) in enumerate(holes):
        for side, u in ((-1.0, u0), (1.0, u1)):
            # The closer sits in the wall side of the jamb and fills the cavity.
            a, b = (u - cw, u) if side < 0 else (u, u + cw)
            meshes.append(_prism(_rect(a, v0, b, v1), cavity_start, cavity_t, frame, "closer",
                                 "%s Cavity Closer %d%s" % (name, i + 1, "L" if side < 0 else "R"), name))
            if not p["reveals"]:
                continue
            lining = _prism(_rect(0.0, v0, face, v1), 0.0, board,
                            _reveal_frame(frame, u, face, -side), "reveal",
                            "%s Reveal %d%s" % (name, i + 1, "L" if side < 0 else "R"), name)
            lining["corner"] = {"k_l": -1.0, "ext_l": 0.0, "u_l": 0.0}
            meshes.append(lining)
    if holes and p["reveals"]:
        # The face panel is always mitred to the reveal lining, whatever detail the
        # corners use. With no lining there is nothing to mitre to, so it stays square.
        jambs = {u: jamb_cut for o in holes for u in (o[0], o[1])}
        _apply_corner(meshes, jambs, "reveal", types=("panel", "plank"))


def _reveal_frame(frame, u_jamb, face_depth, sign):
    """Frame of a reveal lining: u runs inward from the cladding face, and the outward
    normal faces into the opening (sign = +1 where the opening is at greater u)."""
    n = frame["n"]
    return {"origin": frame_to_world(frame, u_jamb, 0.0, face_depth),
            "u": [-n[0], -n[1], 0.0],
            "n": [sign * frame["u"][0], sign * frame["u"][1], 0.0]}
