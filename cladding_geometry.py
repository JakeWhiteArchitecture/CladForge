"""
CladForge — buildup and board setting-out for one extracted elevation.

Works entirely in the elevation's local frame: u runs along the wall, v runs
up it, depth runs outward from the wall face. Elements are emitted untrimmed
(full rectangles); cladding_booleans.py cuts them at openings and splash zones.
"""

from cladding_constants import _prism, _rect, COUNTER_BATTEN_CENTRES, MAX_BATTEN_SPAN
from cladding_constants import frame_to_world
from cladding_primitives import (centred_positions, stacked_positions, batten_positions, dedupe,
                                 dedupe_priority, subdivide, panel_bays, bays_between, split_run,
                                 base_level, corner_ends, openings, buildup_depth)


def build_elevation(p, elev, layout=None):
    """Return (meshes, dims, info) for one elevation record.
    *layout* is (chain start, run length) measured along the cladding face."""
    name = elev.get("name", "Elevation")
    W, H = float(elev["width"]), float(elev["height"])
    frame = elev["frame"]
    # Chains: coursing is centred on the whole run and shifted by the chain offset,
    # so modules carry on around corners. Each elevation occupies [start, start + W]
    # of the run, reversed where the corner flips the u direction.
    start, run = layout or (0.0, W)
    centre = run / 2.0 + float(elev.get("offset", 0.0)) - start        # in this elevation's u
    if elev.get("chain_reversed"):
        centre = W - centre
    offset = centre - W / 2.0
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

    v0 = base_level(elev, p["splash"])
    info["base_level"] = v0
    bw, bd = p["batten_w"], p["batten_d"]

    if p["cladding_type"] == "panel":
        depth = _panels(p, elev, meshes, dims, info, depth, W, H, v0, offset)
    elif p["battens"] == "vertical":
        depth = _horizontal_planks(p, elev, meshes, dims, info, depth, W, H, v0, offset)
    else:
        depth = _vertical_planks(p, elev, meshes, dims, info, depth, W, H, v0, offset)

    left, right, detail = corner_ends(elev, p)
    _apply_corner(meshes, {0.0: left, W: right}, detail, skip=("reveal", "closer"))
    info["corner"] = {"detail": detail, "left": list(left), "right": list(right)}
    # Splash zone dimension at the left edge of the ground band.
    if v0 > 0:
        dims.append(_dim(name, [0, 0], [0, v0], "Splash %.0f" % v0, 300, [-1, 0]))
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


def _dim(elev, p1, p2, label, offset, norm):
    return {"elevation": elev, "p1": [float(p1[0]), float(p1[1])],
            "p2": [float(p2[0]), float(p2[1])], "label": label,
            "offset": float(offset), "norm": [float(norm[0]), float(norm[1])]}


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


def _horizontal_planks(p, elev, meshes, dims, info, depth, W, H, v0, offset):
    """Horizontal boards on vertical battens (optionally on horizontal counter-battens)."""
    name, frame = elev["name"], elev["frame"]
    if p["has_cb"]:  # forced by override — flagged by check_rules, drainage is compromised
        vs = dedupe(stacked_positions(v0 + p["cb_w"] / 2, H, COUNTER_BATTEN_CENTRES) + [H - p["cb_w"] / 2], p["cb_w"])
        depth = _horizontal_battens(p, vs, meshes, name, frame, depth, W, "counter_batten", p["cb_w"], p["cb_d"])
    battens = batten_positions(W, p["batten_w"], p["batten_centres"], offset)
    depth = _vertical_battens(p, battens, meshes, name, frame, depth, H)
    cover, face = p["cover"], p["plank_w"]
    gap = p["plank_gap"] if p["plank_lap"] <= 0 else 0.0
    courses = stacked_positions(v0, H, cover)
    for j, v in enumerate(courses):
        segs, bad = split_run(0, W, p["plank_len"], battens, j % 2 == 1, gap)
        info["unsupported_joints"] += len(bad)
        for k, (s, e) in enumerate(segs):
            meshes.append(_prism(_rect(s, v, e, v + face), depth, p["plank_t"], frame, "plank",
                                 "%s Plank C%d-%d" % (name, j + 1, k + 1), name))
    info.update(n_courses=len(courses), cover=cover, batten_centres=p["batten_centres"],
                closing_cut_top=(H - courses[-1]) if courses else 0.0,
                closing_cut_left=0.0, closing_cut_right=0.0)
    if len(battens) > 1:
        dims.append(_dim(name, [battens[0], 0], [battens[1], 0], "%.0f c/c" % (battens[1] - battens[0]), 300, [0, -1]))
    if courses:
        dims.append(_dim(name, [W, courses[0]], [W, courses[0] + cover], "Course %.0f" % cover, 300, [1, 0]))
        dims.append(_dim(name, [W, courses[-1]], [W, H], "Cut %.0f" % (H - courses[-1]), 600, [1, 0]))
    return depth + p["plank_t"]


def _vertical_planks(p, elev, meshes, dims, info, depth, W, H, v0, offset):
    """Vertical boards on horizontal battens on vertical counter-battens."""
    name, frame = elev["name"], elev["frame"]
    if p["has_cb"]:
        cbs = batten_positions(W, p["cb_w"], COUNTER_BATTEN_CENTRES, 0.0)
        depth = _vertical_battens(p, cbs, meshes, name, frame, depth, H, "counter_batten", p["cb_w"], p["cb_d"])
    bw = p["batten_w"]
    battens = dedupe(stacked_positions(v0 + bw / 2, H - bw / 2, p["batten_centres"]) + [H - bw / 2], bw)
    depth = _horizontal_battens(p, battens, meshes, name, frame, depth, W)
    cover, face = p["cover"], p["plank_w"]
    gap = p["plank_gap"] if p["plank_lap"] <= 0 else 0.0
    starts = centred_positions(W, cover, offset, face)
    for j, u in enumerate(starts):
        segs, bad = split_run(v0, H, p["plank_len"], battens, j % 2 == 1, gap)
        info["unsupported_joints"] += len(bad)
        for k, (s, e) in enumerate(segs):
            meshes.append(_prism(_rect(u, s, u + face, e), depth, p["plank_t"], frame, "plank",
                                 "%s Plank C%d-%d" % (name, j + 1, k + 1), name))
    left_cut = (starts[0] + face) if starts and starts[0] < 0 else 0.0
    right_cut = (W - starts[-1]) if starts and starts[-1] + face > W else 0.0
    info.update(n_courses=len(starts), cover=cover, batten_centres=p["batten_centres"],
                closing_cut_top=0.0, closing_cut_left=left_cut, closing_cut_right=right_cut)
    if len(battens) > 1:
        dims.append(_dim(name, [0, battens[0]], [0, battens[1]], "%.0f c/c" % (battens[1] - battens[0]), 600, [-1, 0]))
    if starts:
        dims.append(_dim(name, [max(0, starts[0]), H], [max(0, starts[0]) + (cover if starts[0] >= 0 else left_cut), H],
                         ("Course %.0f" % cover) if starts[0] >= 0 else ("Cut %.0f" % left_cut), 300, [0, 1]))
    return depth + p["plank_t"]


def _panels(p, elev, meshes, dims, info, depth, W, H, v0, offset):
    """Panels on vertical battens, joints on battens, noggins at horizontal joints.
    Where the elevation has openings the setting-out starts from them: panel edges land
    on the structural jambs and each span between jambs is split into equal bays no
    wider than the maximum panel."""
    name, frame = elev["name"], elev["frame"]
    bw, gap = p["batten_w"], p["panel_gap"]
    cavity_start = depth
    if p["has_cb"]:  # forced by override
        vs = dedupe(stacked_positions(v0 + p["cb_w"] / 2, H, COUNTER_BATTEN_CENTRES) + [H - p["cb_w"] / 2], p["cb_w"])
        depth = _horizontal_battens(p, vs, meshes, name, frame, depth, W, "counter_batten", p["cb_w"], p["cb_d"])
    holes = openings(elev)
    jambs = sorted({u for o in holes for u in (o[0], o[1]) if bw < u < W - bw}) if p["set_out_from_openings"] else []
    if jambs:
        panels, joints = bays_between([0.0] + jambs + [W], p["panel_w"], gap)
    else:
        panels, joints = panel_bays(W, p["panel_w"], gap, offset)
    # A cavity closer backs every jamb, so it counts as support alongside the battens.
    supports = dedupe_priority([joints + jambs, [bw / 2, W - bw / 2]], bw)
    extra = []
    for a, b in zip(supports, supports[1:]):
        extra += subdivide(a, b, MAX_BATTEN_SPAN)
    supports = dedupe_priority([supports, extra], bw)
    battens = [u for u in supports if all(abs(u - j) > bw / 2 for j in jambs)]
    depth = _vertical_battens(p, battens, meshes, name, frame, depth, H)
    cavity_t = depth - cavity_start
    courses = stacked_positions(v0, H, p["panel_h"] + gap)
    for j, v in enumerate(courses[:-1]):   # noggins behind every horizontal joint
        vj = v + p["panel_h"] + gap / 2
        for k, (a, b) in enumerate(zip(battens, battens[1:])):
            if b - a > bw + 1:
                meshes.append(_prism(_rect(a + bw / 2, vj - bw / 2, b - bw / 2, vj + bw / 2), depth - p["batten_d"],
                                     p["batten_d"], frame, "cross_batten", "%s Cross Batten C%d-%d" % (name, j + 1, k + 1), name))
    for j, v in enumerate(courses):
        for k, (s, e, _full) in enumerate(panels):
            meshes.append(_prism(_rect(s, v, e, v + p["panel_h"]), depth, p["panel_t"], frame, "panel",
                                 "%s Panel C%d-%d" % (name, j + 1, k + 1), name))
    face = depth + p["panel_t"]
    _openings_extras(p, elev, meshes, holes, cavity_start, cavity_t, face)
    fulls = [pn for pn in panels if pn[2]]
    widths = sorted({round(e - s, 1) for s, e, _f in panels})
    info.update(n_courses=len(courses), cover=p["panel_w"] + gap, batten_centres=p["batten_centres"],
                closing_cut_left=(fulls[0][0] if fulls else W), closing_cut_right=(W - fulls[-1][1]) if fulls else 0.0,
                closing_cut_top=(H - courses[-1]) if courses else 0.0, n_full=len(fulls) * len(courses),
                openings=len(holes), set_out_from_openings=bool(jambs),
                panel_widths=widths, min_panel=(widths[0] if widths else 0.0))
    if len(battens) > 1:
        dims.append(_dim(name, [battens[0], 0], [battens[1], 0], "%.0f c/c" % (battens[1] - battens[0]), 300, [0, -1]))
    if fulls and fulls[0][0] > 1:
        dims.append(_dim(name, [0, H], [fulls[0][0], H], "Cut %.0f" % fulls[0][0], 300, [0, 1]))
    if fulls:
        dims.append(_dim(name, [fulls[0][0], H], [fulls[0][1], H], "Panel %.0f" % (fulls[0][1] - fulls[0][0]), 300, [0, 1]))
    for o in holes:
        dims.append(_dim(name, [o[0], o[3]], [o[1], o[3]], "Opening %.0f" % (o[1] - o[0]), 250, [0, 1]))
    if courses:
        dims.append(_dim(name, [W, courses[0]], [W, courses[0] + p["panel_h"]], "Course %.0f" % p["panel_h"], 300, [1, 0]))
    return face


def _openings_extras(p, elev, meshes, holes, cavity_start, cavity_t, face):
    """Solid timber cavity closers at both vertical sides of every opening, and the
    reveal linings, which the face panel is always mitred to whatever the corner
    detail is. Heads and sills are not lined: a frame whose v is world Z cannot
    describe a surface that faces up or down."""
    name, frame = elev["name"], elev["frame"]
    cw = p["closer_w"]
    jamb_cut = (-1.0, buildup_depth(p))        # bisector of the arris at the reveal
    for i, (u0, u1, v0, v1) in enumerate(holes):
        for side, u in ((-1.0, u0), (1.0, u1)):
            # The closer sits in the wall side of the jamb and fills the cavity.
            a, b = (u - cw, u) if side < 0 else (u, u + cw)
            meshes.append(_prism(_rect(a, v0, b, v1), cavity_start, cavity_t, frame, "closer",
                                 "%s Cavity Closer %d%s" % (name, i + 1, "L" if side < 0 else "R"), name))
            if not p["reveals"]:
                continue
            lining = _prism(_rect(0.0, v0, buildup_depth(p), v1), 0.0, p["panel_t"],
                            _reveal_frame(frame, u, face, -side), "reveal",
                            "%s Reveal %d%s" % (name, i + 1, "L" if side < 0 else "R"), name)
            lining["corner"] = {"k_l": -1.0, "ext_l": 0.0, "u_l": 0.0}
            meshes.append(lining)
    if holes and p["reveals"]:
        # The face panel is always mitred to the reveal lining, whatever detail the
        # corners use. With no lining there is nothing to mitre to, so it stays square.
        jambs = {u: jamb_cut for o in holes for u in (o[0], o[1])}
        _apply_corner(meshes, jambs, "reveal", types=("panel",))


def _reveal_frame(frame, u_jamb, face_depth, sign):
    """Frame of a reveal lining: u runs inward from the cladding face, and the outward
    normal faces into the opening (sign = +1 where the opening is at greater u)."""
    n = frame["n"]
    return {"origin": frame_to_world(frame, u_jamb, 0.0, face_depth),
            "u": [-n[0], -n[1], 0.0],
            "n": [sign * frame["u"][0], sign * frame["u"][1], 0.0]}
