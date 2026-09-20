import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from synthetic import payload, N, U, ORIGIN  # noqa: E402
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
