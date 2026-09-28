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
