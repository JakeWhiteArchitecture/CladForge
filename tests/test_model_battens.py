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
from cladding_geometry import post_key  # noqa: E402
import pytest  # noqa: E402

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
    used = [q for q in elev["posts"] if q.get("default") != "off"]
    assert [q["u"] for q in used] == POSTS
    assert all(abs(q["w"] - 45.0) < 0.5 and q["v1"] - q["v0"] > 2900 for q in used)
    # the upright "beam" is batten-shaped but not a column: offered switched off, and said so
    assert [(q["u"], q["name"]) for q in elev["posts"] if q.get("default") == "off"] == [(3000.0, "Rail")]
    assert any("Rail" in w and "switched off" in w for w in elev["warnings"])
    assert not any("Frame post" in w or "Block" in w or "Pier" in w for w in elev["warnings"])
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
    # every joint on a batten, every panel within the maximum. On a reveal line the gap is
    # taken under and over the window, so it sits half a gap off the batten's centre.
    on_reveal = lambda j: min(abs(j - 2005.0), abs(j - 3195.0)) < 1      # noqa: E731
    assert all(any(abs(j - b) < (5.5 if on_reveal(j) else 1) for b in battens) for j in joints)
    assert all(e - s <= 1200 + 1e-6 for s, e in panels)
    # the reveal lines are panel edges, carried straight through: 2000 has a column, 3200
    # does not and gets a batten of its own; on both the batten runs above and below the
    # window (900-2100), not beside it where the closer is
    assert any(abs(s - 3200) < 1 for s, _e in panels) and any(abs(e - 2000) < 1 for _s, e in panels)
    assert (2010.0, 3190.0) in panels                # under and over the window: the gap off both lines
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


def test_a_batten_of_our_own_never_leaves_a_sliver_at_the_end():
    """The job's J1: its last usable column 1234 mm from the corner, too far for one panel.
    A batten of ours at a full sheet's width left a 24 mm strip at the corner; the stretch
    is shared between two panels instead."""
    from cladding_primitives import bays_on_posts
    panels, joints, extra = bays_on_posts([0.0, 7483.0], [714.0, 1314.0, 2499.0, 3179.0, 4379.0, 5059.0, 6244.0], 1200.0, 10.0)
    assert min(e - s for s, e, _f in panels) >= 300.0, panels
    assert all(e - s <= 1200.0 + 1e-6 for s, e, _f in panels)
    assert extra == [joints[-1]] and joints[-1] == pytest.approx((6249.0 + 7483.0) / 2.0)


# ── found more tolerantly, and saying why not ──────────────────────────────

def _extract(context):
    pl = payload()
    pl["context"] = list(pl["context"]) + context
    with contextlib.redirect_stdout(io.StringIO()):
        return extract_elevation(pl)


def test_members_are_battens_too():
    elev = _extract([_column("Batten %d" % i, u, kind="IfcMember") for i, u in enumerate(POSTS)])
    assert [q["u"] for q in elev["posts"]] == POSTS and not elev["posts_passed"]


@pytest.mark.parametrize("d0, d1, found", [(0.0, 38.0, True), (12.0, 50.0, True), (-20.0, 30.0, True),
                                           (-40.0, 30.0, False),      # 43% in front: sunk in
                                           (-30.0, 50.0, True),       # 62% in front: a batten let into the face
                                           (140.0, 190.0, True), (160.0, 210.0, False)])
def test_a_batten_sunk_into_the_face_counts_while_half_of_it_stands_proud(d0, d1, found):
    elev = _extract([_column("Batten %d" % i, u, d0=d0, d1=d1) for i, u in enumerate(POSTS)])
    assert (len(elev["posts"]) == len(POSTS)) == found
    if not found:        # and the face says what it saw, once for all of them
        (line,) = elev["posts_passed"]
        assert line.startswith("%d batten-shaped elements not used" % len(POSTS))
        assert ("sunk 40 mm" in line and "43%" in line) if d0 < 0 else "160 mm in front" in line
        assert line in elev["warnings"]


def test_something_batten_shaped_is_offered_switched_off_and_can_be_turned_on():
    proxies = [_column("OBJT-%d" % i, u, kind="IfcBuildingElementProxy") for i, u in enumerate(POSTS)]
    elev = _extract(proxies)
    assert [q["u"] for q in elev["posts"]] == POSTS and {q.get("default") for q in elev["posts"]} == {"off"}
    assert "IfcBuildingElementProxy" in elev["posts_passed"][0] and "click one to use it" in elev["posts_passed"][0]
    out = _preview(dict(elev, offset=0.0))
    assert out["info"][0]["model_posts"] == 0 and {q["state"] for q in out["info"][0]["posts"]} == {"off"}
    on = {post_key(u): "on" for u in POSTS}
    out = _preview(dict(elev, offset=0.0, post_states=on))
    assert out["info"][0]["model_posts"] == len(POSTS) and set(POSTS) <= set(_us(out, "batten"))
    # barely proud of the face (a stud showing through), it is no batten and nothing is said
    flush = _extract([_column("Stud %d" % i, u, d0=-100.0, d1=20.0) for i, u in enumerate(POSTS)])
    assert not flush["posts"] and not flush["posts_passed"]
    # called a batten, it is used from the start
    named = _extract([_column("Counter batten 50x38", u, kind="IfcBuildingElementProxy") for u in POSTS])
    assert not any(q.get("default") for q in named["posts"]) and not named["posts_passed"]
    # a column or member on the same line as a look-alike wins
    both = _extract(proxies[:1] + [_column("Batten", POSTS[0])])
    assert len(both["posts"]) == 1 and "default" not in both["posts"][0]


def test_on_battens_at_600_with_a_pair_100_apart_the_most_1200_panels_and_a_700_over_the_pair():
    """The job's client battens: 600 centres, now and then two 100 apart, then 600 again.
    The fewest vertical joints, as many 1200 panels as can be, and the pair taken in one
    700 panel (600 + 100, an extra fixing) rather than a joint more."""
    from cladding_primitives import bays_on_posts
    posts = [600.0, 1200.0, 1800.0, 1900.0, 2500.0, 3100.0, 3700.0, 4300.0]
    panels, joints, extra = bays_on_posts([0.0, 4900.0], posts, 1200.0, 10.0)
    widths = [round(e - s) for s, e, _f in panels]
    assert not extra and len(panels) == 5 and all(j in posts for j in joints)
    assert sum(1 for w in widths if w >= 1185) == 3, widths          # three full sheets
    assert any(685 <= w <= 700 for w in widths), widths               # the pair in one ~700 panel
    # greedy from the left would have stopped at 1200 and 1900 and 3100 and 4300: the same here,
    # but with the pair first it must look ahead: 0 | 100 pair at 600/700 | ...
    panels, joints, extra = bays_on_posts([0.0, 3700.0], [600.0, 700.0, 1300.0, 1900.0, 2500.0, 3100.0], 1200.0, 10.0)
    widths = [round(e - s) for s, e, _f in panels]
    assert len(panels) == 4 and sum(1 for w in widths if w >= 1185) >= 2, widths


def test_a_joint_may_stand_up_to_10_off_its_batten_to_get_a_full_sheet():
    """Client battens at about 606: from the corner, two bays are 1212, too wide for a 1200
    sheet with its joint on the centre. Up to JOINT_SLIP off a batten's centre, it fits."""
    from cladding_primitives import bays_on_posts, JOINT_SLIP
    posts = [605.0, 1212.0, 1818.0, 2424.0]
    panels, joints, extra = bays_on_posts([0.0, 3030.0], posts, 1200.0, 10.0)
    widths = [round(e - s) for s, e, _f in panels]
    assert not extra and sum(1 for w in widths if w >= 1185) >= 2, widths
    assert all(min(abs(j - q) for q in posts) <= JOINT_SLIP + 1e-6 for j in joints)


def test_a_chain_s_split_height_puts_a_joint_at_that_level_on_every_face():
    """The job's band: a horizontal joint at the sill of two windows (z 1774 in the model's
    own coordinates) round the building, whatever each face's base and the model's offset."""
    from cladding_geometry import _split_rows
    frame = {"origin": [0.0, 0.0, -3698.0], "u": [1.0, 0.0, 0.0], "v": [0.0, 0.0, 1.0], "n": [0.0, -1.0, 0.0]}
    p = {"panel_split_z": 1774.0, "panel_h": 2400.0, "context": {"offset": [0.0, 0.0, 3669.0]}}
    rows = _split_rows(p, {"frame": frame}, 10.0, 2421.0, 10.0)
    # the joint's centre at z 1774 - 3669 = -1895 in the engine, v 1803 on this face
    assert rows == [pytest.approx(1803.0 - 10.0 - 5.0)]
    assert _split_rows(dict(p, panel_split_z=None), {"frame": frame}, 10.0, 2421.0, 10.0) is None
    assert _split_rows(dict(p, panel_split_z=4000.0), {"frame": frame}, 10.0, 2421.0, 10.0) is None    # above the face


@pytest.mark.parametrize("drop, expect", [(120.0, 120.0), (45.0, 45.0), (400.0, 0.0), (0.0, 0.0)])
def test_the_cladding_comes_down_to_battens_that_run_below_the_wall(drop, expect):
    """The job's ground floor: walls modelled 120 (or 45) mm short of the battens on them,
    which run down to the level the cladding is meant to start at. The face's foot comes down
    to the battens' (within BASE_REACH: a batten running far below is something else)."""
    plain = _elevation(posts=[], distractors=False)
    pl = payload()
    pl["context"] = list(pl["context"]) + [_column("Beam %d" % i, u, v0=-drop, kind="IFCBEAM") for i, u in enumerate(POSTS)]
    with contextlib.redirect_stdout(io.StringIO()):
        elev = extract_elevation(pl)
    assert elev["height"] == pytest.approx(plain["height"] + expect)
    assert elev["area"] == pytest.approx(plain["area"] + expect * plain["width"], rel=1e-6)
    assert {round(q["v0"], 1) for q in elev["posts"]} == {round(expect - drop, 1)}
    assert any("brought down to them" in w for w in elev["warnings"]) == (expect > 0)


def test_one_batten_below_the_wall_does_not_move_its_foot():
    pl = payload()
    pl["context"] = list(pl["context"]) + [_column("Beam %d" % i, u, v0=-120.0 if i == 3 else 0.0, kind="IFCBEAM")
                                           for i, u in enumerate(POSTS)]
    with contextlib.redirect_stdout(io.StringIO()):
        elev = extract_elevation(pl)
    assert elev["height"] == pytest.approx(_elevation(posts=[], distractors=False)["height"])
