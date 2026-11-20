"""Windows in standing seam cladding: the sill capping and the head strip of VMZINC's
"Junctions with window" (VMZ-SN-MA-RA-07)."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import ifcopenshell  # noqa: E402

from synthetic import payload  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_checks import check_rules  # noqa: E402
from cladding_constants import frame_to_world, world_to_frame  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from cladding_window import (SILL_BOARD, SILL_COVER, SILL_CLEAR, SILL_FALL, SILL_UP, HEAD_DRIP, WELT_BACK,  # noqa: E402
                             has_window_flashings)
from dxf_generator import meshes_to_dxf_string  # noqa: E402
from ifc_generator import meshes_to_ifc  # noqa: E402

SEAM = dict(cladding_type="plank", plank_profile="seam", plank_orient="vertical")


def _wall(elev=None, **kw):
    a = extract_elevation(dict(payload("Elevation A"), options={"debug": False, "clad_depth": 150.0}))
    params = dict({"elevations": [dict(dict(a, offset=0), **(elev or {}))], "trim": True}, **dict(SEAM, **kw))
    return params, generate_preview(params)


def _named(out, text):
    return [m for m in out["geometry"] if text in m["name"]]


def _section(params, part):
    """A part run along the wall, in section: [(depth from the wall's face, height)]."""
    f = params["elevations"][0]["frame"]
    pts = [world_to_frame(f, frame_to_world(part["frame"], x, y, 0.0)) for x, y in part["profile"]]
    return [(q[2], q[1]) for q in pts]


def test_the_sill_is_a_board_falling_outward_under_a_capping_that_drops_over_the_cladding_below():
    params, out = _wall()
    (board,), (cap,), (strip,) = _named(out, "Sill Board"), _named(out, "Sill Capping"), _named(out, "Sill Strip")
    u0, u1, v0, _v1 = 2000.0, 3200.0, 900.0, 2100.0                      # the window
    trays = [m for m in out["geometry"] if m["ifc_type"] == "plank"]
    back, face = min(m["depth"] for m in trays), max(m["depth"] + m["thickness"] for m in trays)
    t, seam_h = 0.6, 25.0
    for part in (board, cap, strip):
        assert part["thickness"] == u1 - u0                              # the opening wide
        assert world_to_frame(params["elevations"][0]["frame"], part["frame"]["origin"])[0] == pytest.approx(u0)
    # the board: from the frame (50 behind the wall's face) to the front of the wall's plywood, falling 1 degree outward
    b = _section(params, board)
    assert (min(d for d, _v in b), max(d for d, _v in b)) == pytest.approx((-50.0, back))
    inner, outer = max(v for d, v in b if d < -49.0), max(v for d, v in b if d > back - 1.0)
    assert inner == pytest.approx(v0 + SILL_BOARD) and inner - outer == pytest.approx((back + 50.0) * math.tan(math.radians(SILL_FALL)), abs=0.01)
    # the capping: turned up against the frame, out over the front, and down in front of the cladding below
    c = _section(params, cap)
    assert min(d for d, _v in c) == pytest.approx(-50.0, abs=0.01) and max(v for _d, v in c) == pytest.approx(v0 + SILL_BOARD + SILL_UP, abs=0.5)
    drop = max(d for d, _v in c)
    assert drop == pytest.approx(face + seam_h + SILL_CLEAR + t, abs=0.01)                 # clear of the tips of the seams below
    below = max(q[1] for m in trays for q in m["profile"] if u0 < q[0] < u1 and q[1] <= v0)
    assert min(v for _d, v in c) == pytest.approx(below - SILL_COVER, abs=t)               # 60 over the head of the cladding below
    # the continuous strip under its front edge
    s = _section(params, strip)
    assert back - 31.0 < min(d for d, _v in s) < back and face + seam_h < max(d for d, _v in s) < drop
    info = out["info"][0]["windows"][0]
    assert info["sill"]["cover"] == 60.0 and info["sill"]["fall"] == 1.0 and info["rect"] == [u0, u1, v0, 2100.0]


def test_the_top_edge_of_the_cladding_under_the_sill_is_turned_out():
    """Each tray under the sill ends in a lip folded outward, as far as the tips of its seams,
    behind the capping's drop: the tray's own sheet."""
    params, out = _wall()
    lips = _named(out, "Turn-out")
    trays = {m["name"]: m for m in out["geometry"] if m["ifc_type"] == "plank"}
    face = max(m["depth"] + m["thickness"] for m in trays.values())
    under = [m for m in trays.values() if max(q[1] for q in m["profile"]) <= 900.0
             and min(q[0] for q in m["profile"]) < 3200.0 and max(q[0] for q in m["profile"]) > 2000.0]
    assert len(lips) == len(under) == out["info"][0]["windows"][0]["sill"]["turned_out"] and lips
    assert sum(m["thickness"] for m in lips) == pytest.approx(1200.0)                     # across the opening, tray by tray
    (cap,) = _named(out, "Sill Capping")
    drop = max(d for d, _v in _section(params, cap))
    for lip in lips:
        tray = trays[lip["of"]]
        assert lip["name"] == tray["name"] + " Turn-out 1" and lip.get("tray_sheet")
        sec = _section(params, lip)
        top = max(q[1] for q in tray["profile"])
        assert max(v for _d, v in sec) == pytest.approx(top, abs=0.01)                    # at the tray's head
        assert (min(d for d, _v in sec), max(d for d, _v in sec)) == pytest.approx((face, face + 25.0), abs=0.01)
        assert max(d for d, _v in sec) < drop - 4.0                                       # behind the capping's drop, air between


def test_the_head_has_a_drip_strip_behind_the_cladding_and_a_welt_piece_under_the_lining():
    params, out = _wall()
    (strip,), (welt,) = _named(out, "Head Strip"), _named(out, "Head Welt")
    trays = [m for m in out["geometry"] if m["ifc_type"] == "plank"]
    back = min(m["depth"] for m in trays)
    feet = min(q[1] for m in trays for q in m["profile"] if 2000.0 < q[0] < 3200.0 and q[1] >= 2100.0)
    s = _section(params, strip)
    assert min(v for _d, v in s) == pytest.approx(feet - HEAD_DRIP, abs=0.5)               # 20 below the feet of the trays over the opening
    assert max(v for _d, v in s) > feet + 30.0 and min(d for d, _v in s) < back            # and up behind them, on the plywood
    w = _section(params, welt)
    assert min(d for d, _v in w) == pytest.approx(-50.0 + WELT_BACK, abs=0.5)              # 10 out from the frame
    (lining,) = _named(out, "Reveal 1H")
    under = world_to_frame(params["elevations"][0]["frame"], lining["frame"]["origin"])[1] - lining["thickness"]
    assert max(v for _d, v in w) <= under + 0.01                                           # under the head lining
    assert out["info"][0]["windows"][0]["head"] == {"drip": HEAD_DRIP, "foot": pytest.approx(feet - HEAD_DRIP), "welt": True, "length": 1200.0}


def test_only_an_upright_standing_seam_wall_gets_them_and_the_chain_can_switch_them_off():
    _p, off = _wall(elev={"window_flashings": "none"})
    assert not _named(off, "Sill ") and not _named(off, "Head Strip") and "windows" not in off["info"][0]
    for kw in (dict(cladding_type="panel"), dict(cladding_type="plank", plank_profile="open", plank_orient="horizontal")):
        a = extract_elevation(dict(payload("Elevation A"), options={"debug": False, "clad_depth": 150.0}))
        out = generate_preview(dict({"elevations": [dict(a, offset=0)], "trim": True}, **kw))
        assert not [m for m in out["geometry"] if m["ifc_type"] in ("window_flashing", "sill_board")]
    assert has_window_flashings({"pitch": 90.0}, {"seam": True}) and has_window_flashings({}, {"seam": True})
    assert not has_window_flashings({"pitch": 35.0}, {"seam": True})                       # a slope's openings are rooflights
    assert not has_window_flashings({"pitch": 90.0}, {"seam": False})


def test_a_door_has_a_head_but_no_sill():
    params, out = _wall()
    names = [m["name"] for m in out["geometry"] if m["ifc_type"] in ("window_flashing", "sill_board")]
    made = out["info"][0]["windows"]
    # the sample wall's door is a notch in the outline, not an opening of the face: only the window is detailed
    assert [w["opening"] for w in made] == [1] and all(" 1" in n for n in names)


def test_the_check_says_so_and_they_export():
    params, out = _wall()
    (row,) = [r for r in check_rules(params, out["info"]) if r["name"].endswith("Windows")]
    assert row["status"] == "pass" and "VMZ-SN-MA-RA-07" in row["message"] and "60 mm" in row["message"] and "turned out" in row["message"]
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    kinds = {e.ObjectType for e in ifc.by_type("IfcElement")}
    assert {"Window flashing", "Sill board"} <= kinds
    names = {e.Name for e in ifc.by_type("IfcElement")}
    assert {"Elevation A Sill Capping 1", "Elevation A Sill Strip 1", "Elevation A Sill Board 1", "Elevation A Head Strip 1"} <= names
    assert not [n for n in names if "Turn-out" in n]                                       # a tray's turned-out head is in the tray's element
    assert len(out["info"][0]["window_strips"]) == 2 and "CORNER_PROFILE" in meshes_to_dxf_string(out["geometry"], params, out["info"])
