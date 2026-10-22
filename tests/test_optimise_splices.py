"""Optimise splices: the plank setting-out tried at each Splices setting, packed, ranked."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from synthetic import payload  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_preview import splice_trial, rank_splices, SPLICE_STEPS  # noqa: E402


def _params(splice, **kw):
    e = dict(extract_elevation(payload()), chain="Chain 1", chain_reversed=False, offset=0, chain_start=0)
    return dict({"elevations": [e], "cladding_type": "plank", "plank_len": 3600, "plank_min": 900,
                 "plank_splice": splice, "trim": True}, **kw)


def test_each_setting_is_a_real_rebuild_with_its_own_pieces():
    trials = [splice_trial(_params(s), ["Elevation A"], 0.0) for s in (0, 100)]
    fewer, more = trials
    assert (fewer["splice"], fewer["piece"]) == (0, 3600) and (more["splice"], more["piece"]) == (100, 900)
    assert more["n_pieces"] > fewer["n_pieces"]                    # more splices, more pieces
    assert all(t["n_boards"] >= t["lower_bound"] >= 1 for t in trials)


def test_the_stock_and_shortest_piece_stay_as_set():
    t = splice_trial(_params(50, plank_len=4200, plank_min=1200), ["Elevation A"], 0.0)
    assert t["piece"] == round(4200 - 0.5 * (4200 - 1200))


def test_ranking_fewest_lengths_then_waste_then_nearest():
    trials = [{"splice": 0, "n_boards": 10, "waste": 0.2}, {"splice": 30, "n_boards": 9, "waste": 0.25},
              {"splice": 70, "n_boards": 9, "waste": 0.18}, {"splice": 50, "n_boards": 9, "waste": 0.18}]
    assert [t["splice"] for t in rank_splices(trials, current=55)] == [50, 70, 30, 0]   # tied: the nearer first
    assert [t["splice"] for t in rank_splices(trials, current=80)] == [70, 50, 30, 0]


def test_the_slider_steps():
    assert SPLICE_STEPS == (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)


def test_a_setting_with_planks_too_long_for_the_stock_ranks_last():
    """Planks too long to cut are left out of the packing, which would flatter the
    setting: any of them puts it behind every setting without."""
    trials = [{"splice": 0, "n_boards": 30, "waste": 0.1, "too_long": 3}, {"splice": 80, "n_boards": 37, "waste": 0.2, "too_long": 0}]
    assert [t["splice"] for t in rank_splices(trials, current=0)] == [80, 0]
    real = splice_trial(_params(0), ["Elevation A"], 0.0)
    assert set(real) >= {"splice", "piece", "n_boards", "waste", "n_pieces", "lower_bound", "too_long"}


def test_fewer_pieces_break_a_tie():
    trials = [{"splice": 80, "n_boards": 63, "waste": 0.142, "n_pieces": 133, "too_long": 0},
              {"splice": 60, "n_boards": 63, "waste": 0.142, "n_pieces": 119, "too_long": 0}]
    assert [t["splice"] for t in rank_splices(trials, current=80)] == [60, 80]
