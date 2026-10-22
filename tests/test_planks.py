"""Planks: how runs are spliced into pieces, and the cutting list that packs them onto
stock lengths."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cladding_primitives import split_run  # noqa: E402
from cladding_constants import _parse  # noqa: E402
import cladding_nesting as nesting  # noqa: E402

BATTENS = [float(u) for u in range(0, 12001, 400)]     # batten centrelines every 400


def _lengths(segs):
    return [round(e - s) for s, e in segs]


# ── splices ────────────────────────────────────────────────────────────────

def test_no_splice_leaves_a_piece_shorter_than_the_minimum():
    """4000 from 3600 stock: unchecked the last piece is 400. With a 1200 minimum the
    joint moves back to a batten that leaves 1200 or more, and both stay on battens."""
    segs, bad = split_run(0.0, 4000.0, 3600.0, BATTENS, False)
    assert _lengths(segs) == [3600, 400] and not bad
    segs, bad = split_run(0.0, 4000.0, 3600.0, BATTENS, False, min_piece=1200.0)
    assert _lengths(segs) == [2800, 1200] and not bad
    assert all(e - s >= 1200 for s, e in segs)


def test_a_short_run_stays_whole():
    segs, _bad = split_run(0.0, 900.0, 3600.0, BATTENS, False, min_piece=1200.0)
    assert _lengths(segs) == [900]


def test_a_run_no_batten_can_split_fairly_stays_one_length():
    """Between 1 and 2 minimums long and within the stock: one length, no splice."""
    segs, bad = split_run(0.0, 2000.0, 3600.0, BATTENS, False, min_piece=1200.0, target=1500.0)
    assert _lengths(segs) == [2000] and not bad


def test_more_splices_shorten_the_pieces():
    """The target sets the piece length: the slider from fewest (the stock) to most
    (the minimum) gives more joints, every one still on a batten."""
    counts = []
    for splice in (0, 50, 100):
        p = _parse({"plank_len": 3600, "plank_min": 1200, "plank_splice": splice})
        segs, bad = split_run(0.0, 10000.0, p["plank_len"], BATTENS, False, min_piece=p["plank_min"], target=p["plank_target"])
        assert not bad and all(1200 <= e - s <= 3600 for s, e in segs)
        assert all(round(e) % 400 == 0 for _s, e in segs[:-1])          # joints on battens
        counts.append(len(segs))
    assert counts[0] < counts[1] < counts[2]
    assert _parse({"plank_len": 3600, "plank_min": 1200, "plank_splice": 0})["plank_target"] == 3600
    assert _parse({"plank_len": 3600, "plank_min": 1200, "plank_splice": 100})["plank_target"] == 1200


def test_stagger_and_defaults_unchanged():
    """Odd courses still start with half a length, and with the defaults (300 minimum,
    fewest splices) a run splits as before."""
    segs, _bad = split_run(0.0, 9000.0, 3600.0, BATTENS, True)
    assert _lengths(segs)[0] == 1600                                    # the last batten within 1800
    p = _parse({})
    assert p["plank_min"] == 300 and p["plank_splice"] == 0 and p["plank_target"] == p["plank_len"]


def test_the_minimum_is_never_more_than_the_stock():
    assert _parse({"plank_len": 2000, "plank_min": 3000})["plank_min"] == 2000


# ── stock lengths ──────────────────────────────────────────────────────────

def _piece(name, length, width=150.0, u=0.0, v=0.0):
    return {"name": name, "length": length, "width": width, "area": length * width, "u": u, "v": v}


def test_lengths_pack_with_a_kerf_between_pieces_only():
    two = [_piece("A", 1800), _piece("B", 1800)]
    assert nesting.pack_lengths(two, 3600, 150, kerf=0)["n_boards"] == 1
    assert nesting.pack_lengths(two, 3600, 150, kerf=3)["n_boards"] == 2     # 1800 + 3 + 1800 > 3600
    fit = [_piece("A", 1798.5), _piece("B", 1798.5)]
    plan = nesting.pack_lengths(fit, 3600, 150, kerf=3)
    assert plan["n_boards"] == 1
    a, b = plan["boards"][0]["pieces"]
    assert b["x"] - (a["x"] + a["w"]) == 3                                   # one kerf between


def test_end_trim_comes_off_both_ends():
    plan = nesting.pack_lengths([_piece("A", 3590)], 3600, 150, kerf=3, trim=5)
    assert plan["n_boards"] == 1 and plan["boards"][0]["pieces"][0]["x"] == 5
    plan = nesting.pack_lengths([_piece("A", 3595)], 3600, 150, kerf=3, trim=5)
    assert plan["oversize"] == ["A"] and plan["n_boards"] == 0


def test_offcuts_are_reused_and_nothing_overlaps():
    pieces = [_piece("P%02d" % i, L) for i, L in enumerate([2400, 1100, 2300, 1200, 600, 3000, 500, 900, 1700, 1800])]
    plan = nesting.pack_lengths(pieces, 3600, 150, kerf=3, deep=True)
    assert plan["n_boards"] >= plan["lower_bound"]
    assert plan["n_boards"] <= plan["lower_bound"] + 1
    for b in plan["boards"]:
        xs = sorted((q["x"], q["x"] + q["w"]) for q in b["pieces"])
        assert xs[-1][1] <= 3600 + 1e-6
        assert all(x1 + 3 - 1e-6 <= x2 for (_a, x1), (x2, _b) in zip(xs, xs[1:]))
    assert sorted(q["name"] for b in plan["boards"] for q in b["pieces"]) == sorted(p["name"] for p in pieces)
    assert plan == nesting.pack_lengths(pieces, 3600, 150, kerf=3, deep=True)    # deterministic


def test_a_ripped_closing_course_is_cut_from_a_full_width_length():
    plan = nesting.pack_lengths([_piece("Top", 2000, width=92)], 3600, 150, kerf=3)
    q = plan["boards"][0]["pieces"][0]
    assert q["ripped"] and q["cut_h"] == 92 and plan["board"] == [3600, 150]


FRAME = {"origin": [0, 0, 0], "u": [1, 0, 0], "v": [0, 0, 1], "n": [0, -1, 0]}


def _plank(name, elev, u0, u1, v0=0.0, w=150.0):
    return {"type": "prism", "profile": [[u0, v0], [u1, v0], [u1, v0 + w], [u0, v0 + w]], "holes": [],
            "depth": 60.0, "thickness": 20.0, "frame": FRAME, "ifc_type": "plank", "name": name, "elevation": elev}


def test_plank_cutting_plan_per_elevation_in_wall_order():
    geometry = [_plank("A Plank C1-1", "A", 0, 2000), _plank("A Plank C1-2", "A", 2000, 3500),
                _plank("A Plank C2-1", "A", 0, 1500, 160), _plank("B Plank C1-1", "B", 0, 1000)]
    p = _parse({"cladding_type": "plank", "plank_len": 3600, "plank_w": 150})
    plan = nesting.chain_plan(geometry, p, ["A", "B"], 0.0)
    assert plan["kind"] == "plank" and plan["board"] == [3600, 150]
    assert [r["elevation"] for r in plan["rows"]] == ["A", "B"]
    assert all(q["name"].startswith(b["elevation"] + " ") for b in plan["boards"] for q in b["pieces"])
    assert sum(len(b["pieces"]) for b in plan["boards"]) == 4
    assert plan["boards"][-1]["elevation"] == "B" and [b["no"] for b in plan["boards"]] == list(range(1, plan["n_boards"] + 1))
    dxf = nesting.plan_dxf([("Chain 1", plan)])
    assert "length(s) of 3600, 150 wide" in dxf and "Length 1  -  waste" in dxf
    assert "A  -  " in dxf and "length(s), waste" in dxf


def test_vertical_planks_are_measured_up_the_wall():
    geometry = [_plank("A Plank C1-1", "A", 0, 150, 0, 2400)]          # 150 wide, 2400 tall
    p = _parse({"cladding_type": "plank", "plank_orient": "vertical", "plank_len": 3600, "plank_w": 150})
    plan = nesting.chain_plan(geometry, p, ["A"], 0.0)
    q = plan["boards"][0]["pieces"][0]
    assert (q["cut_w"], q["cut_h"]) == (2400, 150)
