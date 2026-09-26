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
    assert not [e for e in edges if WINDOW[0] < e < WINDOW[1]], "a bay crossed the window"
    # switching it off returns to a centred array that ignores the window
    centred = generate_preview(_params(elevation, set_out_from_openings=False))
    starts = sorted({round(m["profile"][0][0], 1) for m in centred["geometry"] if m["ifc_type"] == "panel"})
    assert WINDOW[0] not in starts


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
    p = _parse(_params(elevation))
    depth = buildup_depth(p)
    out = generate_preview(_params(elevation))
    reveals = [m for m in out["geometry"] if m["ifc_type"] == "reveal"]
    assert len(reveals) == 2
    for m in reveals:
        assert m["corner"]["k_l"] == -1.0 and m["corner"]["ext_l"] == 0.0
        assert abs(m["thickness"] - p["panel_t"]) < 1e-6
        # the lining runs from the cladding face back to the wall face
        assert [q[0] for q in m["profile"][:2]] == [0.0, depth]
    # the face panels meeting the jambs are mitred to them, whatever the corner detail
    face = [m for m in out["geometry"] if m["ifc_type"] == "panel" and m.get("corner")]
    assert face, "no face panel mitred at the reveal"
    for m in face:
        for side in ("l", "r"):
            if m["corner"].get("ext_" + side) is not None:
                assert m["corner"]["k_" + side] == -1.0
                assert abs(m["corner"]["ext_" + side] - depth) < 1e-6
    # with no lining there is nothing to mitre to, so the panel stays square
    bare = generate_preview(_params(elevation, reveals=False))
    assert not [m for m in bare["geometry"] if m["ifc_type"] in ("panel", "reveal") and m.get("corner")]


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
    assert len(linings) == 2 and all(e.is_a("IfcCovering") for e in linings)
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
