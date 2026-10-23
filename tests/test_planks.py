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


class _Recorder:
    """Stands in for the DXF writer: keeps what is drawn."""
    def __init__(self):
        self.rings, self.texts = [], []

    def add_ring(self, pts, layer, ox=0.0, oy=0.0):
        self.rings.append((layer, [(x + ox, y + oy) for x, y in pts]))

    def add_text(self, text, pos, height, layer="0", rotation=0.0):
        self.texts.append((text, pos, rotation))

    def add_line(self, a, b, layer="0"):
        pass


def test_the_cutting_plan_stands_plank_lengths_up_side_by_side():
    """Each stock length is drawn upright, a plank wide and its length tall, the lengths
    left to right along the row, pieces from the bottom, labels running up the page."""
    pieces = [_piece("A Plank C1-%d" % i, L, u=i * 1000.0) for i, L in enumerate([3000, 2500, 1000, 900])]
    plan = nesting.pack_lengths(pieces, 3600, 150, kerf=3)
    rec = _Recorder()
    for i, b in enumerate(plan["boards"]):
        nesting._draw_length(rec, plan, b, i + 1, i * 550.0, 0.0)
    boards = [pts for layer, pts in rec.rings if layer == "BOARD"]
    assert len(boards) == plan["n_boards"] >= 2
    for k, pts in enumerate(boards):
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        assert (max(xs) - min(xs), max(ys) - min(ys)) == (150, 3600)       # upright
        assert min(xs) == k * 550.0 and min(ys) == 0.0                       # side by side
    pieces_drawn = [pts for layer, pts in rec.rings if layer == "PIECE"]
    assert all(max(p[1] for p in pts) - min(p[1] for p in pts) in (3000, 2500, 1000, 900) for pts in pieces_drawn)
    assert all(rot == 90.0 for _t, _p, rot in rec.texts)
    assert any(t.startswith("Length 1  -  waste") for t, _p, _r in rec.texts)


# ── keeping a length's cuts together ──────────────────────────────────────

def _course(name, u, v, length, w=150.0):
    """A horizontal plank piece on the wall, *length* long, sitting at (u, v)."""
    return dict(_piece(name, length, w, u, v), w=length, h=w)


def _spread(bars):
    return sum(nesting._spread(ps) for _free, ps in bars)


def test_pieces_from_one_length_are_regrouped_side_by_side():
    """Two 1700s at each end of a long wall, one course above the other. Paired across the
    wall they span 10 m; regrouped, each length's two pieces sit one above the other."""
    a, b = _course("A", 0, 0, 1700), _course("B", 10000, 0, 1700)
    c, d = _course("C", 0, 160, 1700), _course("D", 10000, 160, 1700)
    cap = 3600 + 3
    bars = [[cap - 2 * 1703, [a, b]], [cap - 2 * 1703, [c, d]]]
    out = nesting.keep_together(bars, cap, 3)
    assert len(out) == 2 and _spread(out) < _spread(bars)
    assert sorted(sorted(q["name"] for q in ps) for _f, ps in out) == [["A", "C"], ["B", "D"]]
    assert _spread(out) == 2 * 10                                         # the 10 mm gap between courses


def test_abutting_pieces_cost_nothing():
    assert nesting._spread([_course("A", 0, 0, 1700), _course("B", 1700, 0, 1700)]) == 0
    assert nesting._spread([_course("A", 0, 0, 1700), _course("B", 0, 150, 1700)]) == 0


def test_an_offcut_still_goes_to_the_closest_place_it_fits():
    """Lengths are full nearby, so the short piece can't sit beside its neighbours, but a
    swap still brings it to the nearest place there's room rather than across the wall."""
    full = [[3, [_course("F1", 0, 0, 1800), _course("F2", 1800, 0, 1797)]]]   # used to the last mm
    far = _course("S", 30000, 0, 600)
    near_long = _course("L", 3600, 0, 2900)
    mid = _course("M", 4000, 160, 600)
    cap = 3603
    bars = full + [[cap - 2903 - 603, [near_long, far]], [cap - 603, [mid]]]
    out = nesting.keep_together(bars, cap, 3)
    assert len(out) <= len(bars) and _spread(out) < _spread(bars)
    group = next(ps for _f, ps in out if any(q["name"] == "L" for q in ps))
    assert "S" not in [q["name"] for q in group]


def test_keeping_together_never_adds_a_length_or_overfills_one():
    import random
    rnd = random.Random(7)
    pieces = [_course("P%03d" % i, rnd.uniform(0, 20000), 160 * rnd.randrange(30), rnd.choice([600, 900, 1200, 1700, 2400, 3000]))
              for i in range(150)]
    quick = nesting.pack_lengths(pieces, 3600, 150, kerf=3)
    plan = nesting.pack_lengths(pieces, 3600, 150, kerf=3, deep=True)
    assert plan["n_boards"] <= quick["n_boards"] and plan["waste"] <= quick["waste"] + 1e-9
    assert plan["spread"] < quick["spread"]
    for b in plan["boards"]:
        xs = sorted((q["x"], q["x"] + q["w"]) for q in b["pieces"])
        assert xs[-1][1] <= 3600 + 1e-6
    assert sorted(q["name"] for b in plan["boards"] for q in b["pieces"]) == sorted(p["name"] for p in pieces)
    assert plan == nesting.pack_lengths(pieces, 3600, 150, kerf=3, deep=True)    # deterministic


# ── neighbouring boards stagger their joints ──────────────────────────────

STOPS = [25.0 + 400 * k for k in range(8)]          # batten centres up a 2400 wall


def _joint_at(segs):
    return [round((e + s) / 2) for (_s, e), (s, _e) in zip(segs, segs[1:])]


def test_a_board_keeps_its_joints_clear_of_the_board_beside_it():
    """Splices at 70% (1710 target) with a 900 minimum: alone, every board would splice on
    the one batten that leaves 900 both sides. Beside a board jointed there, the next has
    no clear batten, so it goes up in one length rather than in line."""
    first, _ = split_run(0.0, 2400.0, 3600.0, STOPS, False, 0.0, 900.0, 1710.0)
    second, _ = split_run(0.0, 2400.0, 3600.0, STOPS, True, 0.0, 900.0, 1710.0)
    assert _joint_at(first) == _joint_at(second) == [1225]                 # in line, unchecked
    second, _ = split_run(0.0, 2400.0, 3600.0, STOPS, True, 0.0, 900.0, 1710.0, avoid=[1225.0], pitch=400.0)
    assert _joint_at(second) == [] and _lengths(second) == [2400]


def test_joints_go_two_battens_clear_where_they_can():
    tall = [25.0 + 400 * k for k in range(20)]
    segs, _ = split_run(0.0, 6000.0, 3600.0, tall, True, 0.0, 600.0, 2400.0)
    beside, _ = split_run(0.0, 6000.0, 3600.0, tall, False, 0.0, 600.0, 2400.0, avoid=_joint_at(segs), pitch=400.0)
    assert all(abs(a - b) >= 799 for a in _joint_at(beside) for b in _joint_at(segs))
    assert all(e - s >= 600 for s, e in beside) and all(e - s <= 3600 for s, e in beside)
    # with nothing beside it a board splits exactly as before
    assert split_run(0.0, 6000.0, 3600.0, tall, False, 0.0, 600.0, 2400.0) == \
        split_run(0.0, 6000.0, 3600.0, tall, False, 0.0, 600.0, 2400.0, avoid=[], pitch=400.0)


def test_neighbouring_planks_never_splice_on_the_same_batten():
    """A whole wall of vertical planks at 70% splices and a 900 minimum."""
    from cladding_geometry import _vertical_planks
    import shapely.geometry as sg
    p = _parse({"cladding_type": "plank", "plank_orient": "vertical", "plank_len": 3600, "plank_min": 900, "plank_splice": 70})
    meshes, info = [], {"unsupported_joints": 0}
    elev = {"name": "A", "frame": FRAME, "width": 4000.0, "height": 2400.0}
    _vertical_planks(p, elev, meshes, [], info, 0.0, 4000.0, 2400.0, 0.0, 0.0, sg.box(0, 0, 4000, 2400))
    cols = {}
    for m in (m for m in meshes if m["ifc_type"] == "plank"):
        us, vs = [q[0] for q in m["profile"]], [q[1] for q in m["profile"]]
        cols.setdefault(round(min(us)), []).append((min(vs), max(vs)))
    joints = [sorted(round((a[1] + b[0]) / 2) for a, b in zip(sorted(c), sorted(c)[1:])) for _u, c in sorted(cols.items())]
    assert any(joints) and all(not set(a) & set(b) for a, b in zip(joints, joints[1:]))
