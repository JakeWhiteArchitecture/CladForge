"""Panel joints on staggers: where the clad outline steps and a client batten stands close
by, a joint can be put on the step instead of the panels being notched round it."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contextlib  # noqa: E402
import io  # noqa: E402

import pytest  # noqa: E402

from synthetic import box_tris, rect_tris, N  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from cladding_geometry import post_key, STAGGER_REACH  # noqa: E402
from cladding_constants import _parse  # noqa: E402

POSTS = [150.0, 750.0, 1350.0, 1950.0, 2550.0, 3150.0, 3750.0, 4350.0, 4950.0, 5550.0, 6150.0, 6750.0, 7350.0, 7850.0]
STEP = 3210.0            # the base steps up here: 60 from the client batten at 3150


def _wall(step=STEP, posts=POSTS):
    """A wall 8000 x 3000 whose base is 600 higher to the right of *step*, the client's
    battens standing on it."""
    faces = rect_tris(0.0, 0.0, step, 3000.0) + rect_tris(step, 600.0, 8000.0, 3000.0)
    context = [{"type": "IfcColumn", "name": "Batten %d" % i, "tris": box_tris(u - 22.5, u + 22.5, 700.0, 2950.0, 0.0, 38.0)}
               for i, u in enumerate(posts)]
    with contextlib.redirect_stdout(io.StringIO()):
        return extract_elevation({"name": "Elevation A", "faces": faces, "outward": list(N), "context": context, "options": {}})


def _build(elev, **kw):
    params = dict({"elevations": [dict(elev, offset=0.0)], "cladding_type": "panel", "trim": True, "splash": 0}, **kw)
    with contextlib.redirect_stdout(io.StringIO()):
        return generate_preview(params)


def _panel_edges(out):
    es = set()
    for m in out["geometry"]:
        if m["ifc_type"] == "panel":
            us = [q[0] for q in m["profile"]]
            es |= {round(min(us), 1), round(max(us), 1)}
    return sorted(es)


def test_a_stagger_near_a_client_batten_is_offered_and_off_to_start_with():
    elev = _wall()
    assert len(elev["posts"]) == len(POSTS)
    out = _build(elev)
    (st,) = out["info"][0]["staggers"]
    assert st["u"] == STEP and st["offered"] and not st["on"] and st["near"] == 3150.0
    assert st["v1"] - st["v0"] == pytest.approx(600.0, abs=25)
    # off: the joints are on the client's battens and a panel is notched round the step
    assert not any(abs(e - STEP) < 10 for e in _panel_edges(out))
    assert any(m["ifc_type"] == "panel" and len(m["profile"]) > 4 for m in out["geometry"])


def test_switched_on_the_joint_is_on_the_stagger_with_a_batten_behind_it():
    elev = _wall()
    out = _build(dict(elev, stagger_joints={post_key(STEP): True}))
    assert out["info"][0]["staggers"][0]["on"]
    gap = _parse({"cladding_type": "panel"})["panel_gap"]
    edges = _panel_edges(out)
    assert any(abs(e - (STEP - gap / 2)) < 0.6 for e in edges) and any(abs(e - (STEP + gap / 2)) < 0.6 for e in edges)
    # no panel is notched round the step now: every panel is a plain rectangle
    assert all(len(m["profile"]) == 4 for m in out["geometry"] if m["ifc_type"] == "panel")
    # a batten of ours on the stagger line, beside the client's at 3150
    centres = sorted({round(sum(q[0] for q in m["profile"]) / len(m["profile"])) for m in out["geometry"]
                      if m["ifc_type"] == "batten"})
    assert round(STEP) in centres and 3150 in centres
    # and the joints either side are still on the client's battens
    inner = [e for e in edges if 100 < e < 7900 and abs(e - STEP) > 10]
    assert all(min(abs(e - q) for q in POSTS) <= gap / 2 + 0.6 for e in inner)


def test_a_stagger_far_from_any_client_batten_is_not_offered():
    far = 3150.0 + 300.0
    elev = _wall(step=far)
    assert STAGGER_REACH < 300
    out = _build(dict(elev, stagger_joints={post_key(far): True}))
    (st,) = out["info"][0]["staggers"]
    assert not st["offered"] and not st["on"] and st["near"] is None
    assert not any(abs(e - far) < 10 for e in _panel_edges(out))
    # and with no client battens at all there is nothing to offer
    assert _build(_wall(posts=[]))["info"][0]["staggers"] == []
