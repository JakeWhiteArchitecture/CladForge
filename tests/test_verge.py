"""The verge: the free side edge of a roof slope in standing seam. The last tray runs out
over a verge strip and its last seam stands 150 to 370 mm from the tray's edge (VMZINC's
verge junction G3)."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import ifcopenshell  # noqa: E402

from test_ridge import _slope, _roof, SEAM  # noqa: E402
from test_pitched import _extract  # noqa: E402
from cladding_checks import check_rules  # noqa: E402
from cladding_constants import frame_to_world, world_to_frame  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from cladding_verge import VERGE_TRAY, VERGE_NOSE, VERGE_LAP, VERGE_CLEAR, VERGE_DROP, verge_sides  # noqa: E402
from dxf_generator import meshes_to_dxf_string  # noqa: E402
from ifc_generator import meshes_to_ifc  # noqa: E402


def _one(elev=None, **kw):
    a = dict(dict(_slope("Roof A"), offset=0), **(elev or {}))
    params = dict({"elevations": [a], "trim": True, "splash": 0}, **dict(SEAM, **kw))
    return params, generate_preview(params)


def _strips(out):
    return [m for m in out["geometry"] if m["ifc_type"] == "verge_strip"]


def test_a_roof_slope_gets_a_strip_up_each_free_side():
    params, out = _one()
    left, right = sorted(_strips(out), key=lambda m: m["name"])
    assert (left["name"], right["name"]) == ("Roof A Verge Strip L", "Roof A Verge Strip R")
    assert left["thickness"] == 3000.0 and right["thickness"] == 3000.0          # the slope long
    a = params["elevations"][0]
    deck = next(m for m in out["geometry"] if m["ifc_type"] == "deck")
    deck_top = deck["depth"] + deck["thickness"]
    for strip, edge, out_sign in ((left, 0.0, -1.0), (right, 8000.0, 1.0)):
        pts = [world_to_frame(a["frame"], frame_to_world(strip["frame"], x, y, 0.0)) for x, y in strip["profile"]]
        across = [out_sign * (q[0] - edge) for q in pts]                         # out from the roof's edge
        depth = [q[2] for q in pts]
        t = strip["verge_info"]["thickness"]
        assert abs(min(across) + VERGE_LAP) < 0.01                               # 70 back on the deck
        assert abs(max(across) - VERGE_NOSE - t / 2) < 0.01                      # its nose 36 out
        assert deck_top <= max(depth) <= deck_top + 2.0                          # lying on the deck, under the tray
        assert abs(min(depth) - (deck_top + 1.0 - VERGE_DROP) + t / 2) < 0.01    # the drop
        drop = [x for x, d in zip(across, depth) if d < deck_top - 10.0]
        assert VERGE_CLEAR - 1.5 <= min(drop) and max(drop) <= VERGE_CLEAR + 1.5 # 10 clear of the edge's face


def test_the_last_tray_runs_out_over_the_strip_and_is_as_wide_in_the_cutting_list():
    _params, out = _one()
    _p0, plain = _one(elev={"verge": "none"})
    us = [q[0] for m in out["geometry"] if m["ifc_type"] == "plank" for q in m["profile"]]
    assert (min(us), max(us)) == (-VERGE_TRAY, 8000.0 + VERGE_TRAY)
    us0 = [q[0] for m in plain["geometry"] if m["ifc_type"] == "plank" for q in m["profile"]]
    assert (min(us0), max(us0)) == (0.0, 8000.0) and not _strips(plain)
    # nothing else moves: the deck and the battens stop at the roof's edge
    for kind in ("deck", "batten"):
        us = [q[0] for m in out["geometry"] if m["ifc_type"] == kind for q in m["profile"]]
        assert min(us) >= 0.0 and max(us) <= 8000.0


@pytest.mark.parametrize("want, sides", [("both", "LR"), ("start", "L"), ("end", "R"), ("none", "")])
def test_the_chain_says_which_sides_are_verges(want, sides):
    _params, out = _one(elev={"verge": want})
    assert "".join(sorted(m["name"][-1] for m in _strips(out))) == sides
    assert [v["side"] for v in out["info"][0].get("verges", [])] == [c.lower() for c in sides]


def test_start_and_end_are_the_same_ends_of_the_roof_on_both_slopes_of_a_ridge():
    """The second slope of a ridge is turned round, so its left is the first slope's right:
    "start" must still be one end of the roof, not one on each."""
    _link, params, out = _roof(elev={"verge": "start"}, **SEAM)
    a, b = params["elevations"]
    strips = {m["elevation"]: m for m in _strips(out)}
    assert strips["Roof A"]["name"].endswith("L") and strips["Roof B"]["name"].endswith("R")
    ends = [frame_to_world(m["frame"], 0.0, 0.0, 0.0) for m in strips.values()]
    from synthetic import U
    along = [sum((p[i] - ends[0][i]) * U[i] for i in range(3)) for p in ends]
    assert abs(along[1]) < 0.01                                                 # both at the one gable
    # at the ridge each strip stops square, under the cap, the foot of its drop clear of the other slope's
    k = math.tan(math.radians(31.4))
    assert 2960.0 < strips["Roof A"]["thickness"] < 3000.0
    foot = strips["Roof A"]["thickness"] - 3000.0 - k * min(
        world_to_frame(a["frame"], frame_to_world(strips["Roof A"]["frame"], x, y, 0.0))[2] for x, y in strips["Roof A"]["profile"])
    assert foot <= -15.0 * math.sqrt(1 + k * k) + 0.5


def test_only_trays_running_with_the_fall_on_a_roof_get_a_verge():
    roof = dict(_slope("Roof A"), offset=0)
    seam = {"seam": True, "plank_orient": "vertical"}
    assert verge_sides(roof, seam) == ("l", "r")
    assert verge_sides(dict(roof, chain_reversed=True, verge="start"), seam) == ("r",)
    assert verge_sides(roof, {"seam": True, "plank_orient": "horizontal"}) == ()
    assert verge_sides(roof, {"seam": False, "plank_orient": "vertical"}) == ()
    assert verge_sides(dict(_extract(0.0), offset=0), seam) == ()                # a wall
    assert verge_sides(dict(_extract(8.0), offset=0), seam) == ()                # a wall leaning back
    assert verge_sides(dict(_extract(-30.0), offset=0), seam) == ()              # leaning out
    params, out = _one(cladding_type="panel")
    assert not _strips(out) and "verges" not in out["info"][0]


@pytest.mark.parametrize("offset, status", [(0.0, "warn"), (150.0, "pass")])
def test_the_last_seam_is_checked_against_150_to_370(offset, status):
    """Centred, 530 trays leave 25 mm closers on an 8000 slope: 65 mm from the tray's edge
    with the 40 the tray runs out. Moved 150, they are 175 and 405 on the roof: 215 at one
    verge and outside at the other, so one side only is kept to make the pass."""
    elev = {"offset": offset, "verge": "both" if not offset else "start"}
    params, out = _one(elev=elev)
    (row,) = [c for c in check_rules(params, out["info"]) if c["name"].endswith("Verge")]
    assert row["status"] == status, row["message"]
    seams = [v["last_seam"] for v in out["info"][0]["verges"]]
    if status == "warn":
        assert seams == [65.0, 65.0] and "65 mm (left) and 65 mm (right)" in row["message"] and "150 to 370" in row["message"]
    else:
        assert seams == [215.0] and "215 mm" in row["message"]


def test_the_verge_exports_to_ifc_and_dxf():
    params, out = _one()
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    strips = [e for e in ifc.by_type("IfcCovering") if "Verge Strip" in (e.Name or "")]
    assert len(strips) == 2 and all(e.PredefinedType == "ROOFING" and e.ObjectType == "Verge strip" for e in strips)
    assert len([e for e in ifc.by_type("IfcElement") if not e.is_a("IfcElementAssembly")]) == len(out["geometry"])
    assert len(out["info"][0]["verge_strips"]) == 2
    assert "ROOF A" in meshes_to_dxf_string(out["geometry"], params, out["info"])
