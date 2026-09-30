"""Dissolving horizontal panel joints, bay by bay: merged panels, noggins, names, the board
limit, and dropping joints the grid no longer has."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from synthetic import payload  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_preview import generate_preview, check_rules  # noqa: E402
from cladding_nesting import chain_pieces  # noqa: E402

GAP = 10.0
# 8000 x 3000 with a window over bay 3; rows from 150: 1200, 1200, then 430 at the top.
R1, R2 = (150.0, 1200.0), (1360.0, 1200.0)


@pytest.fixture(scope="module")
def elevation():
    return extract_elevation(payload())


def _build(elevation, joints=None, **kw):
    params = dict({"elevations": [dict(elevation, offset=0, panel_joints=joints)], "cladding_type": "panel",
                   "panel_h": 1200, "panel_gap": GAP, "trim": False}, **kw)
    return generate_preview(params), params


def _stored(out, row, bay):
    """What the 2D view stores when a joint is clicked: its pair, the bay and the level."""
    q = next(q for q in out["info"][0]["hjoints"] if (q["row"], q["bay"]) == (row, bay))
    return [q["row"], q["bay"], q["u0"], q["u1"], q["v"]]


def _panels(out):
    return {m["name"].replace("Elevation A Panel ", ""): m for m in out["geometry"] if m["ifc_type"] == "panel"}


def _span_v(m):
    vs = [q[1] for q in m["profile"]]
    return round(min(vs), 1), round(max(vs), 1)


def test_dissolving_r1_r2_on_three_bays(elevation):
    plain, _ = _build(elevation)
    before = _panels(plain)
    joints = [_stored(plain, 0, k) for k in (4, 5, 6)]
    out, _ = _build(elevation, joints)
    after = _panels(out)
    assert len(after) == len(before) - 3
    for k in (5, 6, 7):                                   # bays 4-6, named from 1
        m = after["R1-%d..R2-%d" % (k, k)]
        assert _span_v(m) == (R1[0], R2[0] + R2[1])       # lower + gap + upper, one panel
        assert "R1-%d" % k not in after and "R2-%d" % k not in after
    for k in (1, 2, 4):                                   # the other bays are left alone
        assert _span_v(after["R1-%d" % k]) == _span_v(before["R1-%d" % k]) == (R1[0], R1[0] + R1[1])
    info = out["info"][0]
    assert info["joints_dropped"] == 0
    assert sorted((q["row"], q["bay"]) for q in info["panel_joints"]) == [(0, 4), (0, 5), (0, 6)]
    assert all(q["size"] == [1192.5, 2410.0] for q in info["panel_joints"])
    # clicking again takes the pair out of the list: the joints come back as they were
    again, _ = _build(elevation, [])
    assert sorted(_panels(again)) == sorted(before)


def test_a_column_of_dissolved_joints_is_one_tall_panel(elevation):
    plain, _ = _build(elevation)
    out, _ = _build(elevation, [_stored(plain, 0, 0), _stored(plain, 1, 0)])
    assert _span_v(_panels(out)["R1-1..R3-1"]) == (150.0, 3000.0)


def test_a_merge_larger_than_the_board_is_refused(elevation):
    """The 2D view refuses a click whose panel would not fit the board either way round,
    from the size the engine reports for that joint (dims2d.toggleJoint). Were one stored
    anyway (the board made smaller later), the Board size check fails on it."""
    plain, _ = _build(elevation)
    one, _ = _build(elevation, [_stored(plain, 0, 0)])
    above = next(q for q in one["info"][0]["hjoints"] if (q["row"], q["bay"]) == (1, 0))
    w, h = above["size"]
    assert (w, h) == (995.0, 2850.0)                      # 1200 + 10 + 1200 + 10 + 430
    fits = lambda bw, bh: (w <= bw and h <= bh) or (w <= bh and h <= bw)  # noqa: E731
    assert not fits(1250, 2500) and fits(1250, 3050)
    out, params = _build(elevation, [_stored(plain, 0, 0), _stored(one, 1, 0)])
    msgs = [c for c in check_rules(params, out["info"]) if c["name"] == "Board size"]
    assert msgs and msgs[0]["status"] == "fail" and "995 x 2850" in msgs[0]["message"]


def test_changing_the_panel_width_drops_joints_that_no_longer_map(elevation):
    plain, _ = _build(elevation)
    joints = [_stored(plain, 0, 0), _stored(plain, 0, 4)]
    out, _ = _build(elevation, joints, panel_w=1000)      # bays 0-1 keep their span, 3-6 move
    info = out["info"][0]
    assert info["joints_dropped"] == 1
    assert [(q["row"], q["bay"]) for q in info["panel_joints"]] == [(0, 0)]
    assert "R1-1..R2-1" in _panels(out)
    # a row height changed under a joint moves its level: dropped, never moved
    out, _ = _build(elevation, joints, panel_h=1300)
    assert out["info"][0]["joints_dropped"] == 2 and not out["info"][0]["panel_joints"]
    # a pair that no longer exists at all, and a joint inside the window, are dropped too
    out, _ = _build(elevation, [[5, 0, 0, 995, 1355], [0, 2, 2000, 3200, 1355]])
    assert out["info"][0]["joints_dropped"] == 2


def test_noggins_only_behind_joints_that_remain(elevation):
    def noggins(joints):
        out, _ = _build(elevation, joints, counter_batten="yes")
        return {(round(sum(q[1] for q in m["profile"]) / 4), round(sum(q[0] for q in m["profile"]) / 4))
                for m in out["geometry"] if m["ifc_type"] == "cross_batten"}
    plain, _ = _build(elevation)
    every = noggins(None)
    fewer = noggins([_stored(plain, 0, 5)])
    gone = every - fewer
    assert gone and all(v == 1355 and 5605 < u < 6797.5 for v, u in gone)


def test_exports_nesting_and_names_use_the_actual_panels(elevation):
    import ifcopenshell
    from ifc_generator import meshes_to_ifc
    from dxf_generator import meshes_to_dxf_string
    plain, _ = _build(elevation)
    trimmed, _ = _build(elevation, trim=True)
    out, params = _build(elevation, [_stored(plain, 0, 4)], trim=True)
    names = [m["name"] for m in out["geometry"] if m["ifc_type"] == "panel"]
    assert "Elevation A Panel R1-5..R2-5" in names
    pieces = chain_pieces(out["geometry"], {"Elevation A"})
    assert len(pieces) == len(chain_pieces(trimmed["geometry"], {"Elevation A"})) - 1
    (merged,) = [q for q in pieces if q["name"] == "Elevation A Panel R1-5..R2-5"]
    assert merged["w"] == 1192.5 and merged["h"] == 2400.0        # 2410, less the 10 mm bottom edge offset
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    assert any(c.Name == "Elevation A Panel R1-5..R2-5" for c in ifc.by_type("IfcCovering"))
    # the DXF elevation draws one panel outline fewer
    count = lambda o: meshes_to_dxf_string(o["geometry"], params, o["info"]).count("CLADDING")  # noqa: E731
    assert count(out) < count(trimmed)
    assert "1 horizontal joint(s) dissolved" in meshes_to_dxf_string(out["geometry"], params, out["info"])
