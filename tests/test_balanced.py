"""A face set out on its own ("seam_balanced"): its trays centred so the two at its ends are
alike, each half a tray or more, and a leaning side's wedge kept in its end tray."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from cladding_preview import generate_preview  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402

SEAM = dict(cladding_type="plank", plank_profile="seam", plank_orient="vertical", seam_full_length=True)
COVER = 530.0


def _wall(top_left_in=0.0, width=5000.0, height=2700.0):
    pts = [[0, 0, 0], [width, 0, 0], [width, 0, height], [top_left_in, 0, height]]
    return extract_elevation({"name": "Wall", "faces": [[pts[0], pts[1], pts[2]], [pts[0], pts[2], pts[3]]],
                              "outward": [0, -1, 0], "context": [], "options": {"debug": False}})


def _columns(out):
    trays = [m for m in out["geometry"] if m["ifc_type"] == "plank"]
    return sorted({(round(min(q[0] for q in m["profile"]), 1), round(max(q[0] for q in m["profile"]), 1)) for m in trays})


@pytest.mark.parametrize("width", [5000.0, 4870.0, 5250.0, 3100.0])
def test_a_balanced_face_has_alike_end_trays_of_half_a_tray_or_more(width):
    w = _wall(width=width)
    out = generate_preview({"elevations": [dict(w, offset=0, chain="C", settings=dict(SEAM, seam_balanced=True))], "trim": True, "splash": 0})
    cols = _columns(out)
    first, last = cols[0][1] - cols[0][0], cols[-1][1] - cols[-1][0]
    assert abs(first - last) < 2.0, cols
    assert COVER / 2.0 - 2.0 <= first <= COVER + 1.0, cols
    # left as it was, the run's own set-out can leave a sliver at an end (one of these widths does)
    plain = generate_preview({"elevations": [dict(w, offset=0, chain="C", settings=dict(SEAM))], "trim": True, "splash": 0})
    assert len(_columns(plain)) >= 1


def test_full_length_trays_have_no_cross_joints():
    w = _wall()
    out = generate_preview({"elevations": [dict(w, offset=0, chain="C", settings=dict(SEAM, seam_balanced=True))], "trim": True, "splash": 0})
    trays = [m for m in out["geometry"] if m["ifc_type"] == "plank"]
    assert len(trays) == len(_columns(out))                                       # one piece a column


def test_a_leaning_side_keeps_its_wedge_in_the_end_tray():
    """A side off plumb (the job's flank beside the canopy's leaning wall leans 212 mm over its
    height): no seam falls in the wedge between the side's foot and its head, so there is no
    sliver beside it, and the end tray is half a tray or more where the face is narrowest
    and no more than a tray where it is widest."""
    lean = 200.0
    w = _wall(top_left_in=lean)
    out = generate_preview({"elevations": [dict(w, offset=0, chain="C", settings=dict(SEAM, seam_balanced=True))], "trim": True, "splash": 0})
    cols = _columns(out)
    seams = [b for _a, b in cols[:-1]]
    assert not [u for u in seams if 0.0 < u < lean + 1.0], cols                  # none in the wedge
    assert seams[0] - lean >= COVER / 2.0 - 2.0, cols                            # half a tray or more at its narrowest
    assert seams[0] <= COVER + 1.0, cols                                          # a tray at most at its widest


def _widths(cols):
    return [round(b - a) for a, b in cols]


def test_an_end_a_little_over_a_tray_slides_the_seams_rather_than_splitting_into_two_narrow_trays():
    """The job's roof B: its trays run 82 past one verge, and the run of seams left an 8 mm
    sliver there, shared with the tray beside it as 269 + 269. Where the other end has room,
    the seams slide instead: one sheet at the verge."""
    from cladding_geometry import seam_columns
    W = 6708.07
    cols = seam_columns(W, 530.0, 721.5 - W / 2, [], (0.0, W), (82.3, 3.2), ((100.0, 530.0), (100.0, 530.0)), (True, True))
    w = _widths(cols)
    assert w[0] == 530 and all(x >= 400 for x in w), w
    assert sum(w) == pytest.approx(W + 82.3 + 3.2, abs=2)
    # an end tray 22 over (roof A: 552 at a verge run on 64 over the wall below): slid, not halved
    from cladding_geometry import _slide_ends
    assert _slide_ends([552.0, 1082.0], 0.0, 1382.0, ((100.0, 530.0), (100.0, 530.0))) == [530.0, 1060.0]


def test_with_no_room_at_the_other_end_an_end_too_wide_is_still_split():
    from cladding_geometry import _slide_ends
    seams = [530.0, 1060.0]
    # both ends 560 wide: sliding one end over its most only makes the other too wide
    assert _slide_ends(seams, -30.0, 1620.0, ((100.0, 530.0), (100.0, 530.0))) == seams
