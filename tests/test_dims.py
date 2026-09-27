"""Dimensions carry what the 2D editor needs to write them back, or say what drives them."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from synthetic import payload  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402


@pytest.fixture(scope="module")
def elevation():
    return extract_elevation(payload())


def _dims(elevation, **kw):
    out = generate_preview(dict({"elevations": [dict(elevation, offset=kw.pop("offset", 0))], "trim": False}, **kw))
    return {d.get("kind"): d for d in out["dimensions"] if d.get("kind")}, out


def test_plank_dims_are_editable(elevation):
    dims, _ = _dims(elevation, cladding_type="plank", batten_centres=450)
    assert dims["centres"]["value"] == 450 and "lock" not in dims["centres"]
    assert dims["course"]["value"] == 158.0
    assert dims["splash"]["value"] == 150.0 and dims["splash"]["p2"] == [0.0, 150.0]
    assert dims["level_top"]["value"] == 3000.0


def test_panel_centres_and_openings_are_read_only(elevation):
    dims, _ = _dims(elevation, cladding_type="panel", set_out_from_openings=False)
    assert "panel bay" in dims["centres"]["lock"]
    assert dims["opening"]["value"] == 1200.0 and "opening" in dims["opening"]["lock"]
    # set out from the openings, the bays follow the jambs (and the battens have no
    # single pitch to dimension)
    dims, _ = _dims(elevation, cladding_type="panel")
    assert "jambs" in dims["panel_w"]["lock"] and "centres" not in dims


def test_the_left_cut_solves_for_the_offset(elevation):
    """A centred array: the left cut moves one for one with the offset, so typing a cut
    is offset += typed - shown, wrapped into one bay."""
    dims, out = _dims(elevation, cladding_type="panel", set_out_from_openings=False)
    cut, bay = dims["cut_left"]["value"], dims["cut_left"]["bay"]
    assert "lock" not in dims["cut_left"] and dims["panel_w"]["value"] == 1200.0
    assert cut == out["info"][0]["closing_cut_left"] and bay == 1210.0
    want = 300.0
    shift = (want - cut + bay / 2) % bay - bay / 2        # what the editor does
    moved, _ = _dims(elevation, cladding_type="panel", set_out_from_openings=False, offset=shift)
    assert abs(moved["cut_left"]["value"] - want) < 1e-6, (cut, shift, moved["cut_left"]["value"])


def test_a_picked_base_is_a_level(elevation):
    dims, _ = _dims(dict(elevation, clip_v_lo=400.0, clip_v_hi=2500.0), cladding_type="plank")
    assert dims["level_base"]["value"] == 400.0 and "splash" not in dims
    assert dims["level_top"]["value"] == 2500.0
