"""Upright battens that follow the chain above or below. Two chains on one wall, one over
the other, each set their battens out across their own width; the one built later puts
its uprights on the lines of the one already there, so they run through."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from cladding_constants import frame_to_world  # noqa: E402
from cladding_preview import generate_preview, build_all_groups  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402

PANEL = dict(cladding_type="panel")
PLANK_H = dict(cladding_type="plank", plank_orient="horizontal")
PLANK_V = dict(cladding_type="plank", plank_orient="vertical", counter_batten="yes")
SEAM = dict(cladding_type="plank", plank_profile="seam", plank_orient="vertical")


def _face(name, x0, x1, z0, z1, y=0.0, outward=(0, -1, 0), window=None):
    """A wall face along x between two levels, a window cut out of it if given (x0, x1, z0, z1)."""
    rects = [(x0, x1, z0, z1)]
    if window:
        a0, a1, w0, w1 = window
        rects = [(x0, a0, z0, z1), (a1, x1, z0, z1), (a0, a1, z0, w0), (a0, a1, w1, z1)]
    tris = []
    for a, b, c, d in rects:
        q = [[a, y, c], [b, y, c], [b, y, d], [a, y, d]]
        tris += [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]
    return extract_elevation({"name": name, "faces": tris, "outward": list(outward), "context": [],
                              "options": {"debug": False, "clad_depth": 150.0}})


def _build(first, second, **kw):
    """Two faces, each its own chain with its own settings, built in this order."""
    (fa, sa), (fb, sb) = first, second
    elevs = [dict(fa, offset=0, chain="Chain 1", settings=sa), dict(fb, offset=0, chain="Chain 2", settings=sb)]
    params = dict({"elevations": elevs, "trim": True, "splash": 0}, **kw)
    return params, generate_preview(params)


def _lines(out, name):
    """The world x of every upright batten of the face, whichever layer stands upright."""
    xs = set()
    for m in out["geometry"]:
        if m["elevation"] != name or m["ifc_type"] not in ("batten", "counter_batten") or m.get("clip_v_at"):
            continue
        us, vs = [q[0] for q in m["profile"]], [q[1] for q in m["profile"]]
        if max(vs) - min(vs) > max(us) - min(us):
            xs.add(round(frame_to_world(m["frame"], (min(us) + max(us)) / 2.0, min(vs), 0.0)[0], 1))
    return sorted(xs)


UPPER = dict(x0=0.0, x1=6000.0, z0=1500.0, z1=4000.0)
LOWER = dict(x0=0.0, x1=6000.0, z0=0.0, z1=1500.0)
WINDOW = (2137.0, 3391.0, 2000.0, 3200.0)       # in the upper face: its jambs set the panels out


@pytest.mark.parametrize("above, below", [(PANEL, PLANK_H), (PLANK_H, PANEL), (SEAM, PANEL), (PANEL, PLANK_V), (PANEL, SEAM)])
def test_the_chain_built_second_follows_the_first(above, below):
    up, low = _face("Upper", window=WINDOW, **UPPER), _face("Lower", **LOWER)
    _p, apart = _build((up, dict(above, match_battens=False)), (low, dict(below, match_battens=False)))
    assert _lines(apart, "Lower") != _lines(apart, "Upper"), "left alone the two do not line up"
    _p, out = _build((up, above), (low, below))
    assert _lines(out, "Upper") == _lines(apart, "Upper"), "the one already there does not move"
    assert _lines(out, "Lower") == _lines(out, "Upper")
    info = {i["elevation"]: i for i in out["info"]}
    assert info["Lower"]["matched_to"] == {"name": "Upper", "chain": "Chain 1", "side": "above", "count": len(_lines(out, "Upper"))}
    assert not info["Upper"].get("matched_to")


def test_it_works_the_other_way_up():
    up, low = _face("Upper", **UPPER), _face("Lower", window=(2137.0, 3391.0, 300.0, 1200.0), **LOWER)
    _p, out = _build((low, PANEL), (up, PLANK_H))
    assert _lines(out, "Upper") == _lines(out, "Lower")
    assert {i["elevation"]: i for i in out["info"]}["Upper"]["matched_to"]["side"] == "below"


def test_the_setting_turns_it_off():
    up, low = _face("Upper", window=WINDOW, **UPPER), _face("Lower", **LOWER)
    _p, out = _build((up, PANEL), (low, dict(PLANK_H, match_battens=False)))
    assert _lines(out, "Lower") != _lines(out, "Upper")
    assert not {i["elevation"]: i for i in out["info"]}["Lower"].get("matched_to")


@pytest.mark.parametrize("other", [
    dict(x0=6000.0, x1=9000.0, z0=1500.0, z1=4000.0),                # beside it, on the same wall
    dict(x0=0.0, x1=6000.0, z0=1000.0, z1=3000.0),                   # over the same height (an overlap, not a stack)
    dict(x0=0.0, x1=6000.0, z0=1500.0, z1=4000.0, y=-900.0),         # another wall, parallel and well in front
    dict(x0=5800.0, x1=9000.0, z0=1500.0, z1=4000.0),                # barely over it: 200 mm shared
])
def test_only_a_face_directly_above_or_below_is_followed(other):
    first, low = _face("Other", **other), _face("Lower", **LOWER)
    _p, out = _build((first, PANEL), (low, PLANK_H))
    assert not {i["elevation"]: i for i in out["info"]}["Lower"].get("matched_to")
    _p, alone = _build((first, PANEL), (low, dict(PLANK_H, match_battens=False)))
    assert _lines(out, "Lower") == _lines(alone, "Lower")


def test_a_wall_round_the_corner_is_not_followed():
    side = extract_elevation({"name": "Side", "faces": [[[6000, 0, 1500], [6000, 4000, 1500], [6000, 4000, 4000]],
                                                        [[6000, 0, 1500], [6000, 4000, 4000], [6000, 0, 4000]]],
                              "outward": [1, 0, 0], "context": [], "options": {"debug": False, "clad_depth": 150.0}})
    _p, out = _build((side, PANEL), (_face("Lower", **LOWER), PLANK_H))
    assert not {i["elevation"]: i for i in out["info"]}["Lower"].get("matched_to")


def test_faces_of_one_chain_do_not_follow_each_other():
    up, low = _face("Upper", window=WINDOW, **UPPER), _face("Lower", **LOWER)
    elevs = [dict(up, offset=0, chain="Chain 1"), dict(low, offset=0, chain="Chain 1")]
    out = generate_preview(dict({"elevations": elevs, "trim": True, "splash": 0}, **PANEL))
    assert not any(i.get("matched_to") for i in out["info"])


def test_a_set_back_storey_still_lines_up():
    up, low = _face("Upper", window=WINDOW, y=300.0, **UPPER), _face("Lower", **LOWER)
    _p, out = _build((up, PANEL), (low, PLANK_H))
    assert _lines(out, "Lower") == _lines(out, "Upper")


def test_past_the_end_of_the_face_it_follows_its_own_set_out_carries_on():
    up = _face("Upper", x0=0.0, x1=3000.0, z0=1500.0, z1=4000.0)
    low = _face("Lower", x0=0.0, x1=6000.0, z0=0.0, z1=1500.0)
    _p, out = _build((up, PLANK_H), (low, PLANK_H))
    upper, lower = _lines(out, "Upper"), _lines(out, "Lower")
    assert set(upper) <= set(lower), "every line of the face above, under it"
    beyond = [x for x in lower if x > upper[-1]]
    assert len(beyond) >= 6 and beyond[-1] == 5975.0                                   # battens to the far end, and its edge batten
    gaps = [b - a for a, b in zip([upper[-1]] + beyond, beyond)]
    assert max(gaps) <= 600.0 and min(gaps) >= 100.0, gaps
    assert {i["elevation"]: i for i in out["info"]}["Lower"]["matched_to"]["count"] == len(upper)


def test_the_nearest_face_is_the_one_followed():
    top = _face("Top", x0=0.0, x1=6000.0, z0=3000.0, z1=4000.0)
    mid = _face("Mid", x0=0.0, x1=6000.0, z0=1500.0, z1=3000.0, window=(2137.0, 3391.0, 1800.0, 2700.0))
    low = _face("Lower", **LOWER)
    elevs = [dict(top, offset=120, chain="Chain 1", settings=PLANK_H), dict(mid, offset=0, chain="Chain 2", settings=dict(PANEL, match_battens=False)),
             dict(low, offset=0, chain="Chain 3", settings=PLANK_H)]
    out = generate_preview({"elevations": elevs, "trim": True, "splash": 0})
    assert _lines(out, "Top") != _lines(out, "Mid")
    assert _lines(out, "Lower") == _lines(out, "Mid")
    assert {i["elevation"]: i for i in out["info"]}["Lower"]["matched_to"]["name"] == "Mid"


def test_a_matched_line_can_be_turned_off_in_the_2d_view():
    up, low = _face("Upper", **UPPER), _face("Lower", **LOWER)
    _p, out = _build((up, PLANK_H), (low, PLANK_H))
    posts = {i["elevation"]: i for i in out["info"]}["Lower"]["posts"]
    assert posts and all(q["state"] == "on" and q["name"] == "Upper batten" for q in posts)
    drop = posts[3]["u"]
    low_off = dict(low, post_states={str(int(drop + 0.5)): "off"})
    _p, out2 = _build((up, PLANK_H), (low_off, PLANK_H))
    assert len(_lines(out2, "Lower")) == len(_lines(out, "Lower")) - 1


def test_the_clients_columns_come_first():
    up, low = _face("Upper", window=WINDOW, **UPPER), _face("Lower", **LOWER)
    low = dict(low, posts=[{"u": 1000.0, "default": "on", "name": "Column"}, {"u": 3000.0, "default": "on", "name": "Column"}])
    _p, out = _build((up, PANEL), (low, PLANK_H))
    info = {i["elevation"]: i for i in out["info"]}["Lower"]
    assert not info.get("matched_to") and info["model_posts"] == 2
    assert 1000.0 in _lines(out, "Lower") and 3000.0 in _lines(out, "Lower")


def test_the_exporters_quick_build_lays_the_battens_the_same():
    up, low = _face("Upper", window=WINDOW, **UPPER), _face("Lower", **LOWER)
    params, out = _build((up, PANEL), (low, PLANK_H))
    meshes, _dims, infos = build_all_groups(params)
    assert {i["elevation"]: i for i in infos}["Lower"]["matched_to"]["name"] == "Upper"
    assert _lines({"geometry": meshes}, "Lower") == _lines(out, "Lower")


def test_a_face_set_out_on_the_client_s_battens_leads_whatever_was_picked_first():
    """The job: the standing seam skirts were picked before the Rockpanel under them, so each
    skirt led with its own set-out, and the Rockpanel, on the client's battens, followed
    nothing (the client's own come first). The skirt's uprights should be on the battens of
    the chain below: a face set out on the client's columns leads, whatever the order."""
    up, low = _face("Upper", **UPPER), _face("Lower", **LOWER)
    cols = [600.0, 1200.0, 1800.0, 2400.0, 2500.0, 3100.0, 3700.0, 4300.0, 4900.0, 5500.0]
    low = dict(low, posts=[{"u": u, "w": 45.0, "v0": 0.0, "v1": 1500.0, "name": "Batten %d" % i} for i, u in enumerate(cols)])
    _p, out = _build((up, SEAM), (low, PANEL))
    info = {i["elevation"]: i for i in out["info"]}
    assert (info["Upper"].get("matched_to") or {}).get("name") == "Lower"
    assert not info["Lower"].get("matched_to")
    lower = set(_lines(out, "Lower"))
    assert {round(x, 1) for x in cols} <= lower and set(_lines(out, "Upper")) >= {round(x, 1) for x in cols}


def test_a_skirt_over_two_wings_follows_both():
    """The job's skirt Q runs over two Rockpanel wings of one wall: it followed one of them
    and set itself out over the other. It follows every face stacked under it at that height."""
    from cladding_preview import generate_preview
    up = _face("Upper", **UPPER)
    a, b = _face("Lower A", x0=0.0, x1=2500.0, z0=0.0, z1=1500.0), _face("Lower B", x0=3500.0, x1=6000.0, z0=0.0, z1=1500.0)
    ca, cb = [400.0, 1000.0, 1600.0, 2200.0], [3800.0, 4400.0, 5000.0, 5600.0]
    cols = lambda us, x0: [{"u": u - x0, "w": 45.0, "v0": 0.0, "v1": 1500.0, "name": "B%d" % i} for i, u in enumerate(us)]   # noqa: E731
    elevs = [dict(up, offset=0, chain="Skirt", settings=SEAM),
             dict(a, offset=0, chain="Wing A", settings=PANEL, posts=cols(ca, 0.0)),
             dict(b, offset=0, chain="Wing B", settings=PANEL, posts=cols(cb, 3500.0))]
    out = generate_preview({"elevations": elevs, "trim": True, "splash": 0})
    lines = set(_lines(out, "Upper"))
    assert set(ca) <= lines and set(cb) <= lines
    info = {i["elevation"]: i for i in out["info"]}
    assert {info["Upper"]["matched_to"]["name"]} | set(info["Upper"]["matched_to"].get("also", [])) == {"Lower A", "Lower B"}
