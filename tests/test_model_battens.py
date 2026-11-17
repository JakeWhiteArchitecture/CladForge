"""Battens set out from the client's model: the columns it has standing on the face are
the designer's battens, placed to suit the frame behind, and ours go on the same lines."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contextlib  # noqa: E402
import io  # noqa: E402

from synthetic import payload, box_tris  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402

POSTS = [150.0, 750.0, 1350.0, 2000.0, 2600.0, 3300.0, 3850.0, 4450.0, 5050.0, 5700.0, 6300.0, 6900.0, 7500.0, 7850.0]


def _column(name, u, w=45.0, v0=0.0, v1=2950.0, d0=0.0, d1=38.0, kind="IFCCOLUMN"):
    return {"type": kind, "name": name, "tris": box_tris(u - w / 2, u + w / 2, v0, v1, d0, d1)}


def _elevation(posts=POSTS, distractors=True):
    pl = payload()
    pl["context"] = list(pl["context"]) + [_column("Batten %d" % i, u) for i, u in enumerate(posts)]
    if distractors:
        pl["context"] += [_column("Frame post", 4100.0, d0=-200.0, d1=-50.0),          # inside the wall
                          _column("Block", 4700.0, v1=300.0),                         # too short
                          _column("Pier", 6000.0, w=400.0),                           # too wide
                          _column("Rail", 3000.0, kind="IFCBEAM")]                    # not a column
    with contextlib.redirect_stdout(io.StringIO()):
        return extract_elevation(pl)


def _preview(elev, kind="plank", **kw):
    params = dict({"elevations": [elev], "cladding_type": kind, "trim": False}, **kw)
    with contextlib.redirect_stdout(io.StringIO()):
        return generate_preview(params)


def _us(out, ifc_type, elevation="Elevation A"):
    return sorted({round(sum(q[0] for q in m["profile"]) / len(m["profile"]), 1)
                   for m in out["geometry"] if m["ifc_type"] == ifc_type and m["elevation"] == elevation})


def test_columns_standing_on_the_face_are_found_and_nothing_else():
    plain, elev = _elevation(posts=[], distractors=False), _elevation()
    assert [q["u"] for q in elev["posts"]] == POSTS
    assert all(abs(q["w"] - 45.0) < 0.5 and q["v1"] - q["v0"] > 2900 for q in elev["posts"])
    # standing in front of the face, they punch no holes in it
    assert elev["area"] == plain["area"] and elev["n_holes"] == plain["n_holes"]


def test_a_batten_modelled_a_storey_at_a_time_is_one_batten():
    pl = payload()
    pl["context"] = [_column("Lower", 1000.0, v1=1500.0), _column("Upper", 1001.0, v0=1500.0, v1=2950.0)]
    with contextlib.redirect_stdout(io.StringIO()):
        elev = extract_elevation(pl)
    assert len(elev["posts"]) == 1 and elev["posts"][0]["v1"] - elev["posts"][0]["v0"] > 2900


def test_horizontal_planks_go_on_battens_on_the_columns_and_the_slider_moves_nothing():
    elev = _elevation()
    out = _preview(dict(elev, offset=0.0))
    battens = _us(out, "batten")
    assert set(POSTS) <= set(battens) and len(battens) == len(POSTS) + 2     # and an edge batten at each end
    assert _us(_preview(dict(elev, offset=150.0)), "batten") == battens
    assert out["info"][0]["model_posts"] == len(POSTS)
    # switched off, the regular grid comes back
    assert _us(_preview(elev, battens_from_model=False), "batten") != battens


def test_vertical_planks_put_their_counter_battens_on_the_columns():
    out = _preview(_elevation(), plank_orient="vertical")
    assert set(POSTS) <= set(_us(out, "counter_batten"))
    assert out["info"][0]["model_posts"] == len(POSTS)


def test_panel_joints_land_on_the_columns_and_line_through_at_the_reveals():
    elev = _elevation()
    out = _preview(elev, kind="panel", panel_w=1200, panel_gap=10)
    info = out["info"][0]
    panels = sorted({(round(min(q[0] for q in m["profile"]), 1), round(max(q[0] for q in m["profile"]), 1))
                     for m in out["geometry"] if m["ifc_type"] == "panel"})
    joints = [round((a[1] + b[0]) / 2, 1) for a, b in zip(panels, panels[1:])]
    battens = _us(out, "batten")
    # every joint on a batten, every panel within the maximum
    assert all(any(abs(j - b) < 1 for b in battens) for j in joints)
    assert all(e - s <= 1200 + 1e-6 for s, e in panels)
    # the reveal lines are panel edges, carried straight through: 2000 has a column, 3200
    # does not and gets a batten of its own; on both the batten runs above and below the
    # window (900-2100), not beside it where the closer is
    assert any(abs(e - 3200) < 1 for _s, e in panels) and any(abs(s - 2000) < 1 for s, _e in panels)
    assert info["reveal_battens"] == 1 and info["post_extra"] == 0
    for line in (2000.0, 3200.0):
        at = [m for m in out["geometry"] if m["ifc_type"] == "batten"
              and abs(sum(q[0] for q in m["profile"]) / len(m["profile"]) - line) < 1]
        spans = sorted((min(q[1] for q in m["profile"]), max(q[1] for q in m["profile"])) for m in at)
        assert spans == [(0.0, 900.0), (2100.0, 3000.0)], (line, spans)
    # the slider moves nothing here either
    assert _preview(dict(elev, offset=200.0), kind="panel", panel_w=1200, panel_gap=10)["geometry"] == out["geometry"]


def test_a_joint_with_no_column_in_reach_gets_a_batten_of_its_own():
    from cladding_primitives import bays_on_posts
    panels, joints, extra = bays_on_posts([0.0, 4000.0], [600.0, 1100.0, 3900.0], 1200.0, 10.0)
    assert joints == [1100.0, 2310.0, 3520.0] and extra == [2310.0, 3520.0]     # full 1200 panels past the last column
    assert all(e - s <= 1200.0 + 1e-6 for s, e, _f in panels) and panels[-1][1] == 4000.0
