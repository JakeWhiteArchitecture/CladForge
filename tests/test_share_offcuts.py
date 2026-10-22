"""Offcuts passed on from one elevation to the next, and the rack of offcuts each
elevation ends with."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cladding_constants import _parse  # noqa: E402
import cladding_nesting as nesting  # noqa: E402

FRAME = {"origin": [0, 0, 0], "u": [1, 0, 0], "v": [0, 0, 1], "n": [0, -1, 0]}


def _prism(kind, name, elev, u0, v0, w, h):
    return {"type": "prism", "profile": [[u0, v0], [u0 + w, v0], [u0 + w, v0 + h], [u0, v0 + h]], "holes": [],
            "depth": 60.0, "thickness": 20.0, "frame": FRAME, "ifc_type": kind, "name": name, "elevation": elev}


def _planks():
    """A: twelve 2000s, each leaving 1597 on a 3600 length. B: twelve 1500s, which pair
    up two to a length alone but fit one to each of A's offcuts."""
    a = [_prism("plank", "A Plank C%d-1" % (i + 1), "A", 0, 160 * i, 2000, 150) for i in range(12)]
    b = [_prism("plank", "B Plank C%d-1" % (i + 1), "B", 0, 160 * i, 1500, 150) for i in range(12)]
    return a + b


def _check(plan, geometry):
    names = sorted(m["name"] for m in geometry)
    cut = sorted(q["name"] for b in plan["boards"] for q in b["pieces"] + b.get("later", []))
    assert cut == names                                            # every piece cut once
    L = plan["board"][0]
    for b in plan["boards"]:
        spans = sorted((q["x"], q["x"] + q["w"], q["y"], q["y"] + q["h"]) for q in b["pieces"] + b.get("later", []))
        for (x0, x1, y0, y1), (u0, u1, v0, v1) in zip(spans, spans[1:]):
            assert x1 + plan["kerf"] - 1e-6 <= u0 or y1 <= v0 or v1 <= y0      # nothing overlaps
        if plan["kind"] == "plank":
            assert spans[-1][1] <= L + 1e-6
        assert all(q["elevation"] != b["elevation"] for q in b.get("later", []))


def test_planks_cut_from_an_earlier_elevations_offcuts_save_lengths():
    geometry = _planks()
    p = _parse({"cladding_type": "plank", "plank_len": 3600, "plank_w": 150})
    alone = nesting.chain_plan(geometry, p, ["A", "B"], 0.0)
    shared = nesting.chain_plan(geometry, dict(p, share_offcuts=True), ["A", "B"], 0.0)
    assert [r["n_boards"] for r in alone["rows"]] == [12, 6]
    assert [r["n_boards"] for r in shared["rows"]] == [12, 0]
    assert shared["from_offcuts"] == 12 and shared["saved"] == 6 and shared["waste"] < alone["waste"]
    _check(shared, geometry)
    # B's pieces are drawn on A's lengths, and grouped by the offcut on A's rack
    assert all(len(b["later"]) == 1 and b["later"][0]["elevation"] == "B" for b in shared["boards"])
    assert len(shared["reuse"]) == 12 and all(g["elevation"] == "B" and len(g["pieces"]) == 1 for g in shared["reuse"])
    rack = shared["racks"][0]
    assert rack["elevation"] == "A" and len(rack["offcuts"]) == 12
    assert all(o["used_by"] == "B" and abs(o["rect"][2] - 1600) < 1 for o in rack["offcuts"])
    assert {g["id"] for g in shared["reuse"]} == {o["id"] for o in rack["offcuts"]}
    # what is left after B: 1597 less 1500 and a kerf, under the rack's size
    assert shared["racks"][1]["offcuts"] == []
    assert shared == nesting.chain_plan(geometry, dict(p, share_offcuts=True), ["A", "B"], 0.0)    # deterministic


def test_sharing_never_costs_a_board_and_is_off_by_default():
    geometry = _planks() + [_prism("plank", "C Plank C1-1", "C", 0, 0, 3500, 150)]
    p = _parse({"cladding_type": "plank", "plank_len": 3600, "plank_w": 150})
    assert p["share_offcuts"] is False
    alone = nesting.chain_plan(geometry, p, ["A", "B", "C"], 0.0, deep=True)
    shared = nesting.chain_plan(geometry, dict(p, share_offcuts=True), ["A", "B", "C"], 0.0, deep=True)
    assert all(s["n_boards"] <= a["n_boards"] for s, a in zip(shared["rows"], alone["rows"]))
    assert shared["rows"][2]["n_boards"] == 1                       # nothing left long enough for 3500
    assert alone["from_offcuts"] == 0 and alone["reuse"] == []
    # unshared, each rack is the elevation's own offcuts only
    assert all(nesting_board_elev(alone, o) == r["elevation"] for r in alone["racks"] for o in r["offcuts"])
    _check(shared, geometry)


def nesting_board_elev(plan, offcut):
    return plan["boards"][offcut["k"]]["elevation"]


def test_panels_cut_from_an_earlier_elevations_offcuts():
    """A: four 1200 x 1500 panels, each on its own 1250 x 2500 board, leaving 1250 x 997
    under it. B: four 1200 x 900 panels, which fit two to a board alone, or one to each
    of A's offcuts."""
    a = [_prism("panel", "A Panel %d" % (i + 1), "A", 1300 * i, 0, 1200, 1500) for i in range(4)]
    b = [_prism("panel", "B Panel %d" % (i + 1), "B", 1300 * i, 0, 1200, 900) for i in range(4)]
    p = _parse({"cladding_type": "panel", "board_w": 1250, "board_h": 2500, "rotate": False})
    alone = nesting.chain_plan(a + b, p, ["A", "B"], 0.0)
    shared = nesting.chain_plan(a + b, dict(p, share_offcuts=True), ["A", "B"], 0.0)
    assert [r["n_boards"] for r in alone["rows"]] == [4, 2]
    assert [r["n_boards"] for r in shared["rows"]] == [4, 0]
    _check(shared, a + b)
    for b in shared["boards"]:
        (q,) = b["later"]
        assert q["y"] >= 1500 + p["kerf"] - 1e-6                     # cut from the offcut above A's piece


def test_the_plan_dxf_draws_later_pieces_on_their_board():
    geometry = _planks()
    p = _parse({"cladding_type": "plank", "plank_len": 3600, "plank_w": 150, "share_offcuts": True})
    dxf = nesting.plan_dxf([("Chain 1", nesting.chain_plan(geometry, p, ["A", "B"], 0.0))])
    assert "B Plank C1-1" in dxf and "cut from earlier offcuts" in dxf


def test_what_is_cut_from_offcuts_goes_up_together():
    """Six 1600 offcuts carried on from A, and twelve 1500s on B in two stacks 20 m apart:
    six come from the offcuts and six from three new lengths. The six from offcuts are
    one stack, not three from each end."""
    a = [_prism("plank", "A Plank C%d-1" % (i + 1), "A", 0, 160 * i, 2000, 150) for i in range(6)]
    b = [_prism("plank", "B Plank C%d-%d" % (i + 1, k + 1), "B", u, 160 * i, 1500, 150)
         for i in range(6) for k, u in enumerate((0, 20000))]
    p = _parse({"cladding_type": "plank", "plank_len": 3600, "plank_w": 150, "share_offcuts": True})
    plan = nesting.chain_plan(a + b, p, ["A", "B"], 0.0, deep=True)
    assert plan["rows"][1]["n_boards"] == 3 and plan["from_offcuts"] == 6
    from_offcuts = [q for b in plan["boards"] for q in b.get("later", [])]
    assert len({round(q["u"]) for q in from_offcuts}) == 1
    _check(plan, a + b)
