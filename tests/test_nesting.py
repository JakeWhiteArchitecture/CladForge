"""Packing panels onto stock boards: kerf, rotation, notched panels, determinism, and the
cutting plan."""
import json
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cladding_nesting as nesting  # noqa: E402
from cladding_nesting import pack, chain_pieces, plan_dxf  # noqa: E402

BOARD = (1250.0, 2500.0)


def _pieces(n, w, h, prefix="P"):
    return [{"name": "%s%d" % (prefix, i), "w": w, "h": h, "area": w * h} for i in range(n)]


def test_kerf_between_pieces_not_at_the_edges():
    # 4 x 622 + 3 saw cuts of 3 = 2497: one board
    assert pack(_pieces(4, 1250, 622), *BOARD, kerf=3)["n_boards"] == 1
    # 4 x 625 + 3 x 3 = 2509: two
    assert pack(_pieces(4, 1250, 625), *BOARD, kerf=3)["n_boards"] == 2


def test_rotation_used_when_it_saves_a_board():
    pieces = _pieces(6, 800, 1200)          # upright two to a board, turned three
    upright = pack(pieces, *BOARD, kerf=3, rotate=False)
    turned = pack(pieces, *BOARD, kerf=3, rotate=True)
    assert upright["n_boards"] == 3 and turned["n_boards"] == 2
    assert any(q["rotated"] for b in turned["boards"] for q in b["pieces"])
    assert not any(q["rotated"] for b in upright["boards"] for q in b["pieces"])
    # a rotated piece keeps its cut size; it just sits on the board the other way round
    q = next(q for b in turned["boards"] for q in b["pieces"] if q["rotated"])
    assert (q["cut_w"], q["cut_h"]) == (800, 1200) and (q["w"], q["h"]) == (1200, 800)


def test_a_notched_panel_packs_as_its_rectangle_but_counts_its_net_area():
    frame = {"origin": [0, 0, 0], "u": [1, 0, 0], "v": [0, 0, 1], "n": [0, -1, 0]}
    l_shape = [[0, 0], [1200, 0], [1200, 1000], [600, 1000], [600, 2400], [0, 2400]]
    mesh = {"type": "prism", "profile": l_shape, "holes": [], "depth": 50.0, "thickness": 9.0, "frame": frame,
            "ifc_type": "panel", "name": "Panel R1-1", "elevation": "A"}
    (piece,) = chain_pieces([mesh], {"A"})
    assert (piece["w"], piece["h"]) == (1200, 2400)                       # packed as its rectangle
    assert abs(piece["area"] - (1200 * 1000 + 600 * 1400)) < 1e-6        # counts its net area
    plan = pack([piece], *BOARD)
    assert plan["n_boards"] == 1
    assert abs(plan["waste"] - (1 - piece["area"] / (BOARD[0] * BOARD[1]))) < 1e-9
    assert plan["lower_bound"] == 1


def test_the_result_is_deterministic():
    random.seed(7)
    pieces = [{"name": "Q%03d" % i, "w": random.choice([1200, 995, 600]), "h": random.choice([2400, 1180, 300]),
               "area": 0} for i in range(60)]
    for q in pieces:
        q["area"] = q["w"] * q["h"]
    a = pack(pieces, *BOARD)
    shuffled = pieces[:]
    random.shuffle(shuffled)
    b = pack(shuffled, *BOARD)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["n_boards"] >= a["lower_bound"] == math.ceil(sum(q["area"] for q in pieces) / (BOARD[0] * BOARD[1]))


def test_pieces_never_overlap_or_leave_the_board():
    random.seed(3)
    pieces = [{"name": "Q%03d" % i, "w": random.randint(200, 1250), "h": random.randint(200, 2500), "area": 1.0}
              for i in range(80)]
    plan = pack(pieces, *BOARD, kerf=3)
    placed = 0
    for b in plan["boards"]:
        rects = [(q["x"], q["y"], q["x"] + q["w"], q["y"] + q["h"]) for q in b["pieces"]]
        placed += len(rects)
        for x0, y0, x1, y1 in rects:
            assert x0 >= -1e-6 and y0 >= -1e-6 and x1 <= BOARD[0] + 1e-6 and y1 <= BOARD[1] + 1e-6
        for i, r in enumerate(rects):
            for s in rects[i + 1:]:                       # at least a kerf apart
                assert r[2] + 3 - 1e-6 <= s[0] or s[2] + 3 - 1e-6 <= r[0] or \
                    r[3] + 3 - 1e-6 <= s[1] or s[3] + 3 - 1e-6 <= r[1], (r, s)
    assert placed == len(pieces)


def test_oversize_and_the_cap(monkeypatch):
    plan = pack(_pieces(2, 1300, 2600), *BOARD)
    assert plan["oversize"] == ["P0", "P1"] and plan["n_boards"] == 0
    monkeypatch.setattr(nesting, "MAX_PIECES", 5)
    plan = pack(_pieces(8, 600, 600), *BOARD)
    assert plan["capped"] == 3 and plan["n_pieces"] == 5


def test_a_few_hundred_pieces_well_under_a_second():
    random.seed(1)
    pieces = [{"name": "Q%03d" % i, "w": random.choice([1200, 995, 600, 450]), "h": random.choice([2400, 1180, 300, 900]),
               "area": 0} for i in range(300)]
    for q in pieces:
        q["area"] = q["w"] * q["h"]
    t = time.perf_counter()
    pack(pieces, *BOARD)
    assert time.perf_counter() - t < 0.5


def test_the_cutting_plan_drawing():
    plan = pack(_pieces(6, 800, 1200), *BOARD, kerf=3, rotate=True)
    dxf = plan_dxf([("Chain 1", plan)])
    assert "Cutting plan for setting-out. Not a quantity take-off for pricing." in dxf
    assert "(rotated)" in dxf and "P0" in dxf and "800 x 1200" in dxf
    assert "Board 1  -  waste" in dxf and "total waste" in dxf
    assert "OFFCUT" in dxf and "BOARD" in dxf


def test_board_size_check():
    from synthetic import payload
    from fabric_extract import extract_elevation
    from cladding_preview import generate_preview, check_rules
    e = extract_elevation(payload())
    params = {"elevations": [dict(e, offset=0)], "cladding_type": "panel", "board_w": 1000, "board_h": 2000}
    out = generate_preview(params)
    msgs = [c for c in check_rules(params, out["info"]) if c["name"] == "Board size"]
    assert msgs and msgs[0]["status"] == "fail"
    params["board_h"], params["board_w"] = 2500, 1250
    assert not [c for c in check_rules(params, generate_preview(params)["info"]) if c["name"] == "Board size"]


# ── the full search, edge trim, emptying a board, pooling and board-friendly sizes ──

MIX = [(595, 640), (795, 900), (795, 640), (795, 900), (380, 900), (795, 300), (595, 900), (795, 300), (595, 520),
       (380, 900), (795, 520), (380, 900), (380, 640), (795, 900), (795, 520), (380, 520), (795, 520), (380, 300),
       (380, 640), (795, 900)]


def _mix():
    return [{"name": "Q%03d" % i, "w": w, "h": h, "area": w * h} for i, (w, h) in enumerate(MIX)]


def _guillotine(rects, x0, y0, x1, y1):
    """Can these rectangles be cut out of the box by edge-to-edge saw cuts alone?"""
    if len(rects) <= 1:
        return True
    for axis in (0, 1):
        lo, hi = (x0, x1) if axis == 0 else (y0, y1)
        for c in sorted({r[axis + 2] for r in rects}):
            if not lo < c < hi:
                continue
            a = [r for r in rects if r[axis + 2] <= c + 1e-6]
            b = [r for r in rects if r[axis] >= c - 1e-6]
            if a and b and len(a) + len(b) == len(rects):
                box_a = (x0, y0, c, y1) if axis == 0 else (x0, y0, x1, c)
                box_b = (c, y0, x1, y1) if axis == 0 else (x0, c, x1, y1)
                return _guillotine(a, *box_a) and _guillotine(b, *box_b)
    return False


def _cuttable(plan):
    bw, bh = plan["board"]
    for b in plan["boards"]:
        rects = [(q["x"], q["y"], q["x"] + q["w"], q["y"] + q["h"]) for q in b["pieces"]]
        for r in rects:
            assert r[0] >= plan["trim"] - 1e-6 and r[1] >= plan["trim"] - 1e-6
            assert r[2] <= bw - plan["trim"] + 1e-6 and r[3] <= bh - plan["trim"] + 1e-6
        if not _guillotine(rects, 0, 0, bw, bh):
            return False
    return True


def test_the_full_search_finds_a_board_the_quick_check_misses():
    quick, deep = pack(_mix(), *BOARD, kerf=3), pack(_mix(), *BOARD, kerf=3, deep=True)
    assert (quick["n_boards"], deep["n_boards"]) == (4, 3) and deep["lower_bound"] == 3
    assert quick["strategies"] == 16 and deep["deep"] and deep["strategies"] > 16
    assert _cuttable(quick) and _cuttable(deep)                  # a panel saw can still cut it


def test_the_full_search_is_deterministic_and_never_worse():
    random.seed(11)
    for _ in range(3):
        pieces = [{"name": "P%03d" % i, "w": random.choice([1195, 795, 595, 380]),
                   "h": random.choice([2400, 1570, 900, 640, 300]), "area": 0} for i in range(40)]
        for q in pieces:
            q["area"] = q["w"] * q["h"]
        quick, deep = pack(pieces, *BOARD), pack(pieces, *BOARD, deep=True)
        assert (deep["n_boards"], round(deep["waste"], 9)) <= (quick["n_boards"], round(quick["waste"], 9))
        shuffled = pieces[:]
        random.shuffle(shuffled)
        assert json.dumps(pack(shuffled, *BOARD, deep=True), sort_keys=True) == json.dumps(deep, sort_keys=True)
        assert _cuttable(deep)


def test_the_full_search_is_bounded():
    random.seed(1)
    pieces = [{"name": "Q%03d" % i, "w": random.choice([1195, 995, 600, 450, 380]),
               "h": random.choice([2400, 1180, 300, 900, 1570, 640]), "area": 1.0} for i in range(400)]
    t = time.perf_counter()
    plan = pack(pieces, *BOARD, deep=True)
    assert time.perf_counter() - t < 4.0
    assert plan["work"] < nesting.DEEP_WORK * 1.2


def test_edge_trim_comes_off_every_edge_first():
    # 4 x 620 + 3 kerfs = 2489: fits the 2490 left by a 5 mm trim, not the 2488 left by 6
    pieces = _pieces(4, 1240, 620)
    five, six = pack(pieces, *BOARD, kerf=3, trim=5), pack(pieces, *BOARD, kerf=3, trim=6)
    assert (five["n_boards"], six["n_boards"]) == (1, 2)
    assert min(q["x"] for q in five["boards"][0]["pieces"]) == 5 and _cuttable(five)
    # the waste is still judged against the whole board, and the lower bound on what is left
    assert abs(five["waste"] - (1 - 4 * 1240 * 620 / (1250 * 2500))) < 1e-9
    assert pack(_pieces(1, 1250, 2495), *BOARD, trim=5)["oversize"] == ["P0"]     # neither way round
    assert "edge trim 5" in plan_dxf([("Job", five)])


def test_emptying_a_board_into_the_others_offcuts():
    """Two boards, the second holding one small piece that fits the first's leftover
    space: the pass moves it and the second board goes."""
    BW, BH = 1250 + 3, 2500 + 3
    boards = [{"pieces": [{"name": "A", "x": 0, "y": 0, "w": 1250, "h": 1800, "cut_w": 1250, "cut_h": 1800,
                           "rotated": False, "area": 1250 * 1800}],
               "free": [], "scrap": [(0, 1803, BW, BH - 1803)]},
              {"pieces": [{"name": "B", "x": 0, "y": 0, "w": 1000, "h": 600, "cut_w": 1000, "cut_h": 600,
                           "rotated": False, "area": 600000}],
               "free": [(1003, 0, 250, BH), (0, 603, 1003, BH - 603)], "scrap": []}]
    assert nesting._eliminate(boards, BW, BH, 3, True, nesting._Work()) == 1
    assert len(boards) == 1 and sorted(q["name"] for q in boards[0]["pieces"]) == ["A", "B"]


def test_pooling_the_job_shares_boards_between_elevations():
    frame = {"origin": [0, 0, 0], "u": [1, 0, 0], "v": [0, 0, 1], "n": [0, -1, 0]}
    mesh = lambda name, elev: {"type": "prism", "profile": [[0, 0], [1195, 0], [1195, 1200], [0, 1200]], "holes": [],  # noqa: E731
                               "depth": 50.0, "thickness": 9.0, "frame": frame, "ifc_type": "panel",
                               "name": name, "elevation": elev}
    geometry = [mesh("A Panel R1-1", "A"), mesh("B Panel R1-1", "B")]
    p = {"board_w": 1250, "board_h": 2500, "kerf": 3, "rotate": True}
    apart = [nesting.chain_plan(geometry, p, [e], 0)["n_boards"] for e in ("A", "B")]
    together = nesting.chain_plan(geometry, p, ["A", "B"], 0)
    assert sum(apart) == 2 and together["n_boards"] == 1
    assert together["fills"] == {"A Panel R1-1": round(2 * 1195 * 1200 / (1250 * 2500), 4),
                                 "B Panel R1-1": round(2 * 1195 * 1200 / (1250 * 2500), 4)}


def test_sizes_that_divide_the_board():
    assert nesting.friendly_sizes(1250, 2500, 3) == {"across": [1250, 623, 414], "along": [2500, 1248, 831, 622, 497]}
    # two of the 1248 rows and a kerf fill the 2500 exactly: nothing left over
    assert pack(_pieces(2, 1250, 1248), *BOARD, kerf=3)["n_boards"] == 1
    assert nesting.friendly_sizes(1250, 2500, 3, trim=5)["across"][0] == 1240
