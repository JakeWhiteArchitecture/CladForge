"""Building elevations for the DXF: the model seen square from N, E, S and W, hidden lines removed."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cladding_constants import _prism  # noqa: E402
from cladding_elevations import elevation  # noqa: E402

FLAT = {"origin": [0.0, 0.0, 0.0], "u": [1.0, 0.0, 0.0], "v": [0.0, 0.0, 1.0], "n": [0.0, -1.0, 0.0]}   # a wall facing south


def _box(x0, x1, z0, z1, y_front, depth, kind="plank"):
    """A box x0..x1 wide, z0..z1 high, its south face at y = y_front, *depth* deep (north)."""
    return _prism([[x0, z0], [x1, z0], [x1, z1], [x0, z1]], -y_front - depth, depth, FLAT, kind, "Box")


def _xs(lines):
    return [x for pts, _k in lines for x, _z in pts]


def test_a_box_behind_a_bigger_one_does_not_show():
    near = _box(0, 1000, 0, 1000, 0, 100)
    far = _box(200, 800, 200, 800, 500, 100, "batten")
    lines = elevation([near, far], "south")
    assert lines and all(kind == "plank" for _pts, kind in lines)
    # from the north the small box is in front: its edges show, and the big one's outline still does round it
    back = elevation([near, far], "north")
    assert {kind for _pts, kind in back} == {"plank", "batten"}


def test_a_box_beside_shows_and_part_hidden_is_cut_where_it_goes_behind():
    near = _box(0, 1000, 0, 1000, 0, 100)
    far = _box(500, 1500, 100, 500, 500, 100, "batten")
    lines = elevation([near, far], "south")
    batten = [pts for pts, kind in lines if kind == "batten"]
    assert batten
    assert min(x for pts in batten for x, _z in pts) >= 1000.0 - 0.5        # nothing of it drawn behind the near box


def test_the_views_read_as_seen():
    """From the south east is on the right, from the north on the left."""
    a = _box(0, 100, 0, 100, 0, 100)
    b = _box(900, 1000, 0, 100, 0, 100)
    south, north = elevation([a, b], "south"), elevation([a, b], "north")
    assert max(_xs(south)) == 1000.0 and max(_xs(north)) == 0.0


def test_the_translucent_layers_are_left_out():
    assert not elevation([_box(0, 1000, 0, 1000, 0, 100, "insulation")], "south")


def test_the_dxf_has_the_four_building_elevations():
    from test_gablet import gablet
    from dxf_generator import meshes_to_dxf_string
    _found, params, out = gablet(seam_metal="steel")
    dxf = meshes_to_dxf_string(out["geometry"], params, out["info"])
    for view in ("NORTH", "EAST", "SOUTH", "WEST"):
        assert "%s ELEVATION" % view in dxf
