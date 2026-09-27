"""The general frame (as in FallWright): a wall frame is unchanged, a horizontal frame
round-trips, and a right-handed frame's v is what the IFC placement's y works out to."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from synthetic import payload  # noqa: E402
from cladding_constants import frame_to_world, world_to_frame  # noqa: E402
from fabric_extract import extract_elevation, fit_plane, make_frame  # noqa: E402


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _round_trip(f, points):
    for q in points:
        back = world_to_frame(f, frame_to_world(f, *q))
        assert all(abs(a - b) < 0.01 for a, b in zip(q, back)), (q, back)


def test_wall_frame_v_is_world_z():
    f = extract_elevation(payload())["frame"]
    assert f["v"] == [0.0, 0.0, 1.0]
    # n × u is world Z for a wall frame, so a frame with no v reads the same
    assert all(abs(a - b) < 1e-6 for a, b in zip(_cross(f["n"], f["u"]), (0, 0, 1)))
    old = {k: v for k, v in f.items() if k != "v"}
    for q in ((0, 0, 0), (1234.5, 678.9, 55.0), (8000, 3000, -20)):
        assert frame_to_world(old, *q) == frame_to_world(f, *q)
    _round_trip(f, ((0, 0, 0), (8000, 3000, 0), (1234.567, 2001.5, 187.25), (-50, -300, -20)))


def test_horizontal_frames_round_trip():
    # lying face up (FallWright's roof frame), turned off the axes
    u = (math.cos(0.3), math.sin(0.3), 0.0)
    up = make_frame((0, 0, 1), 50.0, 100.0, 200.0, mode="roof", u=u)
    assert up["n"] == [0.0, 0.0, 1.0] and abs(up["origin"][2] - 50.0) < 1e-9
    # lying face down, as a head lining does: u along the wall, n down, v = n × u outward
    uw = (math.cos(0.5), math.sin(0.5), 0.0)
    n = (0.0, 0.0, -1.0)
    down = {"origin": [10000.0, 5000.0, 2100.0], "u": list(uw), "v": list(_cross(n, uw)), "n": list(n)}
    assert abs(down["v"][2]) < 1e-12                      # v lies level, out of the wall
    for f in (up, down):
        assert all(abs(a - b) < 1e-9 for a, b in zip(_cross(f["u"], f["v"]), f["n"]))    # right-handed
        _round_trip(f, ((0, 0, 0), (1200, 150, 0), (1234.567, 4321.001, 187.25), (-50, 7000.5, -300)))
    # the IFC placement (axis n, ref u) puts the profile's y along n × u, which is v
    assert all(abs(a - b) < 1e-9 for a, b in zip(_cross(down["n"], down["u"]), down["v"]))


def test_wall_mode_is_the_default():
    wall = [[[0, 0, 0], [1000, 0, 0], [1000, 0, 1000]]]
    assert fit_plane(wall, [0, -1, 0]) == fit_plane(wall, [0, -1, 0], mode="wall")
    assert make_frame((0, -1, 0), 0.0, 0.0, 0.0) == make_frame((0, -1, 0), 0.0, 0.0, 0.0, mode="wall")
