import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from synthetic import payload, wall_face_with_door, box_tris, world, N, U, ORIGIN  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_constants import _parse, frame_to_world  # noqa: E402
from cladding_primitives import buildup_depth, openings  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402

WINDOW = (2000.0, 3200.0, 900.0, 2100.0)   # the synthetic wall's structural opening


@pytest.fixture(scope="module")
def elevation():
    return extract_elevation(payload())


def _params(elevation, **kw):
    return dict({"elevations": [dict(elevation, offset=0)], "cladding_type": "panel",
                 "trim": False, "sheathing": True, "insulation": True}, **kw)


def test_only_windows_count_as_openings(elevation):
    """The 1200 x 1200 window is an opening; the 100 x 100 pipe is a penetration."""
    found = openings(elevation)
    assert len(found) == 1
    assert all(abs(a - b) < 1 for a, b in zip(found[0], WINDOW))


def test_setting_out_starts_from_the_structural_opening(elevation):
    out = generate_preview(_params(elevation, panel_w=1200, panel_gap=10))
    edges = sorted({round(m["profile"][0][0], 1) for m in out["geometry"] if m["ifc_type"] == "panel"}
                   | {round(m["profile"][1][0], 1) for m in out["geometry"] if m["ifc_type"] == "panel"})
    assert WINDOW[0] in edges and WINDOW[1] in edges, edges
    # no bay is wider than the maximum panel, and the jambs are hard joints
    widths = out["info"][0]["panel_widths"]
    assert max(widths) <= 1200 + 1e-6
    # the only edges within the window's width are the joint gap's far side on each jamb line
    assert [e for e in edges if WINDOW[0] < e < WINDOW[1]] == [WINDOW[0] + 10.0, WINDOW[1] - 10.0], "a bay crossed the window"
    # switching it off returns to a centred array that ignores the window
    centred = generate_preview(_params(elevation, set_out_from_openings=False))
    starts = sorted({round(m["profile"][0][0], 1) for m in centred["geometry"] if m["ifc_type"] == "panel"})
    assert WINDOW[0] not in starts


def test_the_joint_gap_on_a_jamb_line_is_taken_under_and_over_the_window(elevation):
    """Beside the window a panel runs to the jamb and meets the reveal lining. Above the
    head and below the sill that line is a joint between two panels, and they stand the
    joint gap apart: the gap comes out of the panel within the window's width."""
    out = generate_preview(_params(elevation, panel_w=1200, panel_gap=10, trim=True))
    panels = [m for m in out["geometry"] if m["ifc_type"] == "panel"]
    span = lambda m: (round(min(q[0] for q in m["profile"]), 1), round(max(q[0] for q in m["profile"]), 1))   # noqa: E731
    height = lambda m: (min(q[1] for q in m["profile"]), max(q[1] for q in m["profile"]))                      # noqa: E731
    under = [m for m in panels if span(m) == (WINDOW[0] + 10.0, WINDOW[1] - 10.0)]
    assert under and all(height(m)[1] <= WINDOW[2] + 0.5 or height(m)[0] >= WINDOW[3] - 0.5 for m in under)
    left = [m for m in panels if span(m)[1] == WINDOW[0]]
    right = [m for m in panels if span(m)[0] == WINDOW[1]]
    assert left and right                                           # the panels beside it still run to the jambs
    assert not [m for m in panels if span(m)[0] == WINDOW[0] or span(m)[1] == WINDOW[1]]
    # with no gap asked for, nothing moves
    tight = generate_preview(_params(elevation, panel_w=1200, panel_gap=0))
    assert any(span(m) == (WINDOW[0], WINDOW[1]) for m in tight["geometry"] if m["ifc_type"] == "panel")


@pytest.mark.parametrize("detail", ["mitre", "lap"])
def test_a_panel_running_past_the_sill_keeps_one_straight_edge(elevation, detail):
    """The panel beside the window is 150-1350 high and the sill is at 900: its edge on the
    jamb line is beside the opening for part of its height only. It is still one board with
    one straight edge, cut the same from end to end, not stepped at the sill."""
    from cladding_primitives import ring_at
    elev = dict(elevation, opening_details={"2000,900": {"jamb": detail}})
    out = generate_preview(dict(_params(elev, panel_w=1200, panel_gap=10, trim=True)))
    span = lambda m: (round(min(q[0] for q in m["profile"]), 1), round(max(q[0] for q in m["profile"]), 1))   # noqa: E731
    beside = [m for m in out["geometry"] if m["ifc_type"] == "panel" and span(m)[1] == WINDOW[0]
              and min(q[1] for q in m["profile"]) < WINDOW[2] - 50 < WINDOW[2] + 50 < max(q[1] for q in m["profile"])]
    assert beside
    under = [m for m in out["geometry"] if m["ifc_type"] == "panel" and WINDOW[0] < span(m)[0] < WINDOW[0] + 30]
    for m in beside:
        on_line = [q for q in m["profile"] if abs(q[0] - WINDOW[0]) < 0.5]
        assert len(on_line) == 2, on_line                                 # its two ends: no vertex at the sill or the head
        for depth in (m["depth"], m["depth"] + m["thickness"]):
            right = sorted(q[0] for q in ring_at(m, 0, depth))[-2:]
            assert abs(right[0] - right[1]) < 1e-6                      # the edge is straight, top to bottom
        # and the panel under the window still stands the joint gap off it, at its face and its back
        face = max(q[0] for q in ring_at(m, 0, m["depth"] + m["thickness"]))
        assert all(min(q[0] for q in u["profile"]) - face >= (10.0 if detail == "lap" else 7.0) - 0.05 for u in under) and under


def test_cavity_closer_on_both_vertical_sides(elevation):
    p = _parse(_params(elevation, closer_w=50))
    out = generate_preview(_params(elevation, closer_w=50))
    closers = [m for m in out["geometry"] if m["ifc_type"] == "closer"]
    assert len(closers) == 2, "one closer per vertical side of the opening"
    left, right = sorted(closers, key=lambda m: m["profile"][0][0])
    assert [q[0] for q in left["profile"][:2]] == [WINDOW[0] - 50, WINDOW[0]]
    assert [q[0] for q in right["profile"][:2]] == [WINDOW[1], WINDOW[1] + 50]
    for m in closers:
        vs = [q[1] for q in m["profile"]]
        assert (min(vs), max(vs)) == (WINDOW[2], WINDOW[3]), "full height of the opening"
        # solid timber filling the cavity: from the layers out to the back of the panel
        assert abs(m["depth"] - (p["sheathing_t"] + p["insulation_t"])) < 1e-6
        assert abs(m["depth"] + m["thickness"] - (buildup_depth(p) - p["panel_t"])) < 1e-6


def test_reveal_linings_are_mitred_to_the_face_panel(elevation):
    """Both jambs and the head are lined, and every face board edge along them is mitred
    to the lining, holes left by trimming included, with the mitre gap in the joint."""
    import math
    p = _parse(_params(elevation))
    depth = buildup_depth(p)
    pull = 10.0 / 2 * math.sqrt(2)                     # the default 10 mm gap, straight across
    out = generate_preview(_params(elevation, trim=True))  # the head edge exists once trimmed
    reveals = [m for m in out["geometry"] if m["ifc_type"] == "reveal"]
    assert len(reveals) == 3
    for m in reveals:
        assert m["corner"]["k_l"] == 1.0 and abs(m["corner"]["ext_l"] + p["panel_t"] + pull) < 1e-9
        assert abs(m["thickness"] - p["panel_t"]) < 1e-6
        # the lining runs from the cladding face back to the frame: no frame in this
        # model, so the chain's default, 50 mm behind the wall face
        assert [q[0] for q in m["profile"][:2]] == [0.0, depth + 50.0]
    # the face boards along the jambs and head carry the mitre on those vertices
    shifted = [m for m in out["geometry"] if m["ifc_type"] == "panel" and m.get("vshift")]
    assert shifted, "no face panel mitred at the reveal"
    kinds = set()
    for m in shifted:
        for ring, shifts in zip([m["profile"]] + m["holes"], m["vshift"]):
            for (u, v), (du, duk, dv, dvk) in zip(ring, shifts):
                if duk:
                    assert abs(abs(du) - (depth - p["panel_t"] + pull)) < 1e-6 and abs(duk) == 1.0 and u in (2000.0, 3200.0)
                    kinds.add("jamb")
                if dvk:
                    assert abs(dv - (depth - p["panel_t"] + pull)) < 1e-6 and dvk == -1.0 and v == 2100.0
                    kinds.add("head")
    assert kinds == {"jamb", "head"}, kinds
    # with no lining there is nothing to mitre to, so the panel stays square
    bare = generate_preview(_params(elevation, reveals=False, trim=True))
    assert not [m for m in bare["geometry"] if m["ifc_type"] == "reveal" or m.get("vshift")]


def test_reveal_lining_turns_into_the_opening(elevation):
    """The lining's own frame faces into the opening and starts at the cladding face."""
    p = _parse(_params(elevation))
    depth = buildup_depth(p)
    out = generate_preview(_params(elevation))
    left = min((m for m in out["geometry"] if m["ifc_type"] == "reveal"),
               key=lambda m: m["frame"]["origin"][0] * U[0] + m["frame"]["origin"][1] * U[1])
    frame = left["frame"]
    # outward normal points across the opening, u runs back into the buildup
    assert all(abs(a - b) < 1e-6 for a, b in zip(frame["n"], U))
    assert all(abs(a + b) < 1e-6 for a, b in zip(frame["u"], N))
    # its origin is the arris: at the left jamb, on the cladding face
    origin = frame["origin"]
    local = [(origin[i] - ORIGIN[i]) for i in range(3)]
    u = local[0] * U[0] + local[1] * U[1]
    s = local[0] * N[0] + local[1] * N[1]
    assert abs(u - WINDOW[0]) < 1e-3 and abs(s - depth) < 1e-3


def test_openings_export_as_their_own_ifc_types(elevation):
    import ifcopenshell
    from ifc_generator import meshes_to_ifc
    params = _params(elevation)
    out = generate_preview(params)
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    names = {e.Name: e for e in ifc.by_type("IfcMember") + ifc.by_type("IfcCovering")}
    closers = [e for n, e in names.items() if "Cavity Closer" in n]
    linings = [e for n, e in names.items() if "Reveal" in n]
    assert len(closers) == 2 and all(e.is_a("IfcMember") for e in closers)
    assert len(linings) == 3 and all(e.is_a("IfcCovering") for e in linings)     # both jambs and the head
    assert {e.ObjectType for e in closers} == {"Cavity closer"}
    assert {e.ObjectType for e in linings} == {"Reveal lining"}
    assert ifc.by_type("IfcFacetedBrep"), "the mitred reveal and panel need explicit solids"


def test_face_is_cut_back_at_a_corner(elevation):
    """A wall that runs past a corner is clad only up to it, so nothing projects
    through into the other face."""
    clipped = dict(elevation, clip_lo=500.0, clip_hi=6000.0, offset=0)
    out = generate_preview({"elevations": [clipped], "cladding_type": "plank", "trim": True,
                            "reveals": False})
    assert out["geometry"]
    for m in out["geometry"]:
        if m["ifc_type"] == "closer":
            continue
        for u, _v in m["profile"]:
            assert 500.0 - 1 <= u <= 6000.0 + 1, (m["name"], u)
    # the sheathing outline follows the clip too
    sheet = generate_preview({"elevations": [clipped], "sheathing": True, "trim": False})
    face = [m for m in sheet["geometry"] if m["ifc_type"] == "sheathing"]
    assert face and all(500.0 - 1 <= q[0] <= 6000.0 + 1 for m in face for q in m["profile"])


def test_planks_only_seam_when_a_run_needs_two_boards(elevation):
    """A course shorter than one board is a single piece. A course broken by a gable
    or an opening is set out along each part, not across the whole face."""
    out = generate_preview({"elevations": [dict(elevation, offset=0)], "cladding_type": "plank",
                            "plank_len": 6000, "trim": True, "reveals": False})
    courses = {}
    for m in out["geometry"]:
        if m["ifc_type"] != "plank":
            continue
        v = round(min(q[1] for q in m["profile"]), 1)
        courses.setdefault(v, []).append((min(q[0] for q in m["profile"]),
                                          max(q[0] for q in m["profile"])))
    assert courses
    for v, pieces in courses.items():
        pieces.sort()
        for (a0, a1), (b0, b1) in zip(pieces, pieces[1:]):
            # two pieces in one course may only meet where the face is broken, never
            # as a seam in a run a single board could have covered
            if b0 - a1 < 20:
                assert (a1 - a0) + (b1 - b0) > 6000, (v, pieces)
    # a short face never seams at all
    narrow = generate_preview({"elevations": [dict(elevation, clip_lo=0, clip_hi=1500.0, offset=0)],
                               "cladding_type": "plank", "plank_len": 3600, "trim": True,
                               "reveals": False})
    by_course = {}
    for m in narrow["geometry"]:
        if m["ifc_type"] == "plank":
            by_course.setdefault(round(min(q[1] for q in m["profile"]), 1), []).append(m)
    assert by_course and all(len(v) == 1 for v in by_course.values()), "a 1500mm run was seamed"


def test_a_door_that_breaks_the_outline_is_still_an_opening():
    """A door reaches the foot of the wall, so the void is a bite out of the outline
    rather than an interior hole. It still has to be closed and lined."""
    pay = payload()
    pay["faces"] = wall_face_with_door()
    pay["context"] = []
    elev = extract_elevation(pay)
    assert elev["ok"], elev["warnings"]
    assert elev["notches"] == [[5000.0, 5900.0, 0.0, 2100.0]]
    assert (5000.0, 5900.0, 0.0, 2100.0) in openings(elev)
    assert elev["n_holes"] == 2
    out = generate_preview({"elevations": [dict(elev, offset=0)], "cladding_type": "plank",
                            "trim": True, "reveals": True})
    jambs = sorted(round(min(q[0] for q in m["profile"]), 1)
                   for m in out["geometry"] if m["ifc_type"] == "closer")
    assert len(jambs) == 4, jambs                      # both jambs of the window and the door
    assert 4950.0 in jambs and 5900.0 in jambs, jambs  # the door's, either side of the reveal


def test_a_gable_is_not_mistaken_for_an_opening():
    """The wall under a pitched roof loses two triangles from its bounding box, and a
    stepped wall loses a corner. Neither is an opening."""
    assert extract_elevation(payload(pitched=True))["notches"] == []
    assert extract_elevation(payload())["notches"] == []


def test_the_click_position_limits_the_region_to_its_own_patch():
    """A slab cut clean through a face leaves it in two pieces. Only the piece the
    click landed on is clad: the other side of the junction is a different wall."""
    pay = payload()
    # A slab band right across the face, so the region splits above and below it.
    pay["context"] = [{"type": "IfcSlab", "name": "Floor",
                       "tris": [[list(q) for q in t]
                                for t in box_tris(-500.0, 8500.0, 1400.0, 1700.0, -300.0, 300.0)]}]
    both = extract_elevation(dict(pay))
    assert both["ok"] and len(both["polygons"]) == 2, both["warnings"]

    low = extract_elevation(dict(pay, seeds=[list(world(4000.0, 600.0))]))
    assert len(low["polygons"]) == 1, low["warnings"]
    assert low["height"] < 1450.0, low["height"]          # only the band below the slab
    assert any("left out" in w for w in low["warnings"])

    high = extract_elevation(dict(pay, seeds=[list(world(4000.0, 2500.0))]))
    assert len(high["polygons"]) == 1
    assert high["height"] < 1400.0 and high["frame"]["origin"][2] > 1600.0

    # Two clicks, one in each piece: both are kept, as before seeding.
    pair = extract_elevation(dict(pay, seeds=[list(world(4000.0, 600.0)), list(world(4000.0, 2500.0))]))
    assert len(pair["polygons"]) == 2 and not any("left out" in w for w in pair["warnings"])


def test_top_and_bottom_levels_cut_the_cladding(elevation):
    """Two picked heights bound the cladding. Nothing is generated outside them, and
    the layers that follow the outline are cut to the band too."""
    full = generate_preview({"elevations": [dict(elevation, offset=0)], "cladding_type": "plank",
                             "sheathing": True, "trim": True})
    band = generate_preview({"elevations": [dict(elevation, offset=0, clip_v_lo=800.0, clip_v_hi=2200.0)],
                             "cladding_type": "plank", "sheathing": True, "trim": True})
    assert band["geometry"] and len(band["geometry"]) < len(full["geometry"])
    for m in band["geometry"]:
        if m["frame"].get("v", [0, 0, 1]) != [0, 0, 1]:
            continue            # the head lining lies flat in its own frame; its v runs along the wall
        vs = [q[1] for q in m["profile"]]
        assert min(vs) >= 800.0 - 1.0 and max(vs) <= 2200.0 + 1.0, (m["ifc_type"], min(vs), max(vs))
    assert band["info"][0]["base_level"] >= 800.0


def test_the_base_splash_can_be_switched_off(elevation):
    """The elevation base is an assumption, not a detected abutment, so the wizard can
    turn its splash zone off — while a detected slab keeps its own."""
    def lowest(splash_on):
        abuts = [dict(a, enabled=(a["source"] != "base") or splash_on)
                 for a in elevation["abutments"]]
        out = generate_preview({"elevations": [dict(elevation, abutments=abuts, offset=0)],
                                "cladding_type": "plank", "trim": True})
        return min(min(q[1] for q in m["profile"]) for m in out["geometry"] if m["ifc_type"] == "plank")

    assert lowest(True) >= 150.0            # lifted clear of the ground
    assert lowest(False) < 150.0            # boards run to the foot of the face


def test_a_course_height_can_be_set_per_elevation(elevation):
    """A course height typed on a dimension overrides the parameter for the elevations
    it was applied to, and the dimension carries the value so the UI can prefill it."""
    base = generate_preview({"elevations": [dict(elevation, offset=0)], "cladding_type": "plank",
                             "plank_w": 150, "plank_gap": 8, "trim": False})
    assert base["info"][0]["cover"] == 158.0
    course = next(d for d in base["dimensions"] if d.get("kind") == "course")
    assert course["value"] == 158.0 and course["elevation"] == elevation["name"]

    wide = generate_preview({"elevations": [dict(elevation, offset=0, cover=300.0)],
                             "cladding_type": "plank", "plank_w": 150, "plank_gap": 8, "trim": False})
    assert wide["info"][0]["cover"] == 300.0
    assert wide["info"][0]["n_courses"] < base["info"][0]["n_courses"]

    # panels take a list of row heights instead (see test_rows.py)
    panels = generate_preview({"elevations": [dict(elevation, offset=0, panel_rows=[1500.0])],
                               "cladding_type": "panel", "trim": False})
    row = next(d for d in panels["dimensions"] if d.get("kind") == "row")
    assert row["value"] == 1500.0 and row["row"] == 0


# ── openings at the edges of the clad band, and full-height openings ──

def _kinds_along(out, u, v_lo, v_hi):
    """The edge kinds along the vertical line u between v_lo and v_hi."""
    return {e["kind"] for e in out["info"][0]["edges"]
            if abs(e["p1"][0] - u) < 1 and abs(e["p2"][0] - u) < 1 and max(e["p1"][1], e["p2"][1]) > v_lo
            and min(e["p1"][1], e["p2"][1]) < v_hi}


def test_a_window_crossing_the_clad_top_is_still_a_window(elevation):
    """The chain's top is picked through the window: the window becomes a bite out of the
    clad band's top, not a hole. Its sides are still jambs (closed, lined and mitred),
    and it has no head lining, since its head is above the cladding."""
    out = generate_preview(_params(dict(elevation, clip_v_hi=1500.0), trim=True))
    assert _kinds_along(out, WINDOW[0], 900, 1500) == {"jamb"} and _kinds_along(out, WINDOW[1], 900, 1500) == {"jamb"}
    geo = out["geometry"]
    linings = {m["name"][-2:]: m for m in geo if m["ifc_type"] == "reveal"}
    assert set(linings) == {"1L", "1R"}                                   # no head lining
    for m in linings.values():
        vs = [q[1] for q in m["profile"]]
        assert (min(vs), max(vs)) == (900.0, 1500.0)                      # up to the clad top
    closers = [m for m in geo if m["ifc_type"] == "closer"]
    assert len(closers) == 2 and all(max(q[1] for q in m["profile"]) == 1500.0 for m in closers)
    assert any(m.get("vshift") for m in geo if m["ifc_type"] == "panel")   # the jambs are mitred


def test_a_window_crossing_the_clad_bottom_keeps_its_head(elevation):
    out = generate_preview(_params(dict(elevation, clip_v_lo=1500.0), trim=True))
    assert _kinds_along(out, WINDOW[0], 1500, 2100) == {"jamb"}
    linings = {m["name"][-2:]: m for m in out["geometry"] if m["ifc_type"] == "reveal"}
    assert set(linings) == {"1L", "1R", "1H"}
    assert min(q[1] for q in linings["1L"]["profile"]) == 1500.0


def test_a_full_height_opening_splits_the_face_but_is_still_an_opening(elevation):
    """A door or glazing taller than the face splits it in two: the gap between the pieces,
    open at top and bottom, is an opening, and its sides are jambs, not free ends."""
    W, H = float(elevation["width"]), float(elevation["height"])
    split = dict(elevation, polygons=[{"exterior": [[0, 0], [2000, 0], [2000, H], [0, H]], "holes": []},
                                      {"exterior": [[2900, 0], [W, 0], [W, H], [2900, H]], "holes": []}],
                 notches=[], abutments=[a for a in elevation["abutments"] if a.get("source") == "base"])
    assert (2000.0, 2900.0, 0.0, H) in openings(split)
    out = generate_preview(dict(_params(split, trim=True), splash=0))
    assert _kinds_along(out, 2000.0, 200, H - 200) == {"jamb"} and _kinds_along(out, 2900.0, 200, H - 200) == {"jamb"}
    linings = {m["name"][-2:] for m in out["geometry"] if m["ifc_type"] == "reveal"}
    assert linings == {"1L", "1R"}


# ── reveal linings run back to the window or door frame ──

def _with_window(face_depth, wall_depth=300.0, name="W1"):
    """The synthetic wall with an IfcWindow in its opening, its outer frame face
    *face_depth* from the wall face (negative is behind it), 70 deep, with a sill that
    projects 40 in front of the frame at the bottom."""
    pl = payload()
    frame = box_tris(2000.0, 3200.0, 930.0, 2100.0, face_depth - 70.0, face_depth)
    sill = box_tris(1980.0, 3220.0, 900.0, 930.0, face_depth - 70.0, face_depth + 40.0)
    pl["context"] = pl["context"] + [{"type": "IfcWindow", "name": name,
                                      "tris": [[list(p) for p in t] for t in frame + sill]}]
    pl["options"] = dict(pl.get("options") or {}, wall_depth=wall_depth)
    return extract_elevation(pl)


def _lining_reach(out, suffix="1L"):
    (m,) = [m for m in out["geometry"] if m["ifc_type"] == "reveal" and m["name"].endswith(suffix)]
    return max(q[0] for q in m["profile"])


def test_linings_run_back_to_the_window_frame_in_the_model():
    elev = _with_window(-80.0)
    (fr,) = elev["frames"]
    assert fr["face"] == -80.0                                         # the sill is not the frame
    out = generate_preview(_params(elev, trim=True))
    face = out["info"][0]["total_depth"]
    assert _lining_reach(out) == face + 80.0 and _lining_reach(out, "1R") == face + 80.0
    assert _lining_reach(out, "1H") == face + 80.0
    (d,) = out["info"][0]["opening_details"]
    assert (d["frame_from"], d["frame_depth"], d["frame_name"]) == ("model", -80.0, "W1")
    # a frame standing forward of the wall face, in the insulation zone as in Rockpanel H.02
    out = generate_preview(_params(_with_window(40.0), trim=True))
    assert _lining_reach(out) == out["info"][0]["total_depth"] - 40.0


def test_no_frame_or_one_outside_the_reveal_falls_back_to_the_chain_setting(elevation):
    out = generate_preview(_params(elevation, trim=True))                   # no window in the model
    face = out["info"][0]["total_depth"]
    assert _lining_reach(out) == face + 50.0                              # 50 mm behind the wall face
    (d,) = out["info"][0]["opening_details"]
    assert d["frame_from"] == "default" and d["frame_note"] is None
    out = generate_preview(_params(dict(elevation, frame_setback=-30.0), trim=True))
    assert _lining_reach(out) == face - 30.0                              # set forward on the chain
    # a window found beyond the back of the wall is not the frame of this reveal
    out = generate_preview(_params(_with_window(-420.0, wall_depth=300.0), trim=True))
    (d,) = out["info"][0]["opening_details"]
    assert d["frame_from"] == "default" and "420 mm behind" in d["frame_note"]
    assert _lining_reach(out) == face + 50.0


def test_a_frame_typed_on_the_opening_always_wins():
    elev = _with_window(-80.0)
    out = generate_preview(_params(dict(elev, opening_details={"2000,900": {"frame": 25}}), trim=True))
    assert _lining_reach(out) == out["info"][0]["total_depth"] + 25.0
    assert out["info"][0]["opening_details"][0]["frame_from"] == "set"


def test_the_frame_check_says_where_linings_run_to(elevation):
    from cladding_preview import check_rules
    params = _params(elevation, trim=True)
    (c,) = [c for c in check_rules(params, generate_preview(params)["info"]) if c["name"] == "Window frames"]
    assert c["status"] == "pass" and "0 from the model, 0 set on the opening, 1 at the chain's frame position" in c["message"]
    params = _params(_with_window(-420.0), trim=True)
    (c,) = [c for c in check_rules(params, generate_preview(params)["info"]) if c["name"] == "Window frames"]
    assert c["status"] == "warn" and "W1 found 420 mm behind the wall face" in c["message"]


def test_a_door_with_a_window_beside_it_splitting_the_face_is_two_openings(elevation):
    """The job's merged wall: a full-height door with a window alongside it at the top, so
    the gap between the pieces is L-shaped, not a rectangle, and was no opening at all (no
    reveals, no profiles). Its full-height core is the door; what is left, a rectangle, the
    window."""
    W, H = float(elevation["width"]), float(elevation["height"])
    split = dict(elevation, polygons=[{"exterior": [[0, 0], [2900, 0], [2900, 1200], [2200, 1200], [2200, H], [0, H]], "holes": []},
                                      {"exterior": [[3800, 0], [W, 0], [W, H], [3800, H]], "holes": []}],
                 notches=[], abutments=[a for a in elevation["abutments"] if a.get("source") == "base"])
    found = openings(split)
    assert (2900.0, 3800.0, 0.0, H) in found              # the door, the full height
    assert (2200.0, 2900.0, 1200.0, H) in found           # the window beside it, from 1200 up


def test_a_window_head_on_the_chain_boundary_gets_its_barrier_from_the_face_above():
    """The job's windows whose heads stop where the Rockpanel does, a standing seam skirt above:
    the Rockpanel's face left the head out (its edge) and the skirt has no opening there, so the
    barriers did not go round. The face above builds the head barrier at its foot, across the
    window and its jamb closers, in its own cavity."""
    quad = lambda q: [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]   # noqa: E731
    face = lambda name, tris: extract_elevation({"name": name, "faces": tris, "outward": [0, -1, 0], "context": [],   # noqa: E731
                                                 "options": {"debug": False, "clad_depth": 150.0}})
    # a 6 m wall, 2400 high, with a window from 1000 up to its top at 2000..3000; a skirt over it
    lower = face("Wall", quad([(0, 0, 0), (6000, 0, 0), (6000, 0, 1000), (0, 0, 1000)])
                 + quad([(0, 0, 1000), (2000, 0, 1000), (2000, 0, 2400), (0, 0, 2400)])
                 + quad([(3000, 0, 1000), (6000, 0, 1000), (6000, 0, 2400), (3000, 0, 2400)]))
    upper = face("Skirt", quad([(0, 0, 2400), (6000, 0, 2400), (6000, 0, 3400), (0, 0, 3400)]))
    assert openings(lower) == [(2000.0, 3000.0, 1000.0, 2400.0)]
    params = {"elevations": [dict(lower, offset=0, chain="Wall", settings={"cladding_type": "panel", "barriers": True}),
                             dict(upper, offset=0, chain="Skirt", settings={"cladding_type": "plank", "plank_profile": "seam",
                                                                           "plank_orient": "vertical", "barriers": True})],
              "trim": True, "splash": 0, "reveals": True}
    out = generate_preview(params)
    heads = [m for m in out["geometry"] if m["ifc_type"] == "closer" and m["name"].startswith("Skirt Cavity Barrier") and m["name"].endswith("H")]
    assert len(heads) == 1
    us, vs = [q[0] for q in heads[0]["profile"]], [q[1] for q in heads[0]["profile"]]
    cw = _parse(params)["closer_w"]
    assert min(us) == pytest.approx(2000.0 - cw, abs=1.0) and max(us) == pytest.approx(3000.0 + cw, abs=1.0)
    assert min(vs) == pytest.approx(0.0, abs=1.0)                       # at the skirt's foot, over the window's head
    # the wall itself still has its jambs and sill, and no head of its own
    own = {m["name"][-2:] for m in out["geometry"] if m["ifc_type"] == "closer" and m["name"].startswith("Wall Cavity")}
    assert "1S" in own and "1H" not in own
