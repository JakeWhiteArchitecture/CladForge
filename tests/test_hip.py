"""The hip: two roof slopes falling away from each other and meeting on a sloping line. It
is finished as a ridge is (VMZINC's ventilated ridge/hip detail): every layer cut to the
plane that bisects it, 30 mm clear between the two slopes' trays, under a hip cap."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import ifcopenshell  # noqa: E402

from cladding_checks import check_rules  # noqa: E402
from cladding_constants import frame_to_world  # noqa: E402
from cladding_hip import HIP_SEAMS  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from cladding_primitives import ring_at  # noqa: E402
from cladding_ridge import RIDGE_GAP, RIDGE_LEG, RIDGE_UPSTAND  # noqa: E402
from dxf_generator import meshes_to_dxf_string  # noqa: E402
from fabric_extract import hip_link, valley_link, valley_links, chain_link  # noqa: E402
from ifc_generator import meshes_to_ifc  # noqa: E402
from test_valley import _face, _pair as _valley_pair  # noqa: E402

SEAM = dict(cladding_type="plank", plank_profile="seam", plank_orient="vertical")


def _pair(deg=30.0, deg_c=None):
    """A hipped corner: A's eaves along y = 0, rising with y; C's along x = 0, rising with
    x. They meet over the plan line x·tan(c) = y·tan(a), from the eaves corner up."""
    ta, tc = math.tan(math.radians(deg)), math.tan(math.radians(deg if deg_c is None else deg_c))
    r = ta / tc
    a = _face("Roof A", [(0, 0), (6000, 0), (6000, 3000), (3000 * r, 3000)], lambda x, y: y * ta,
              (0.0, -math.sin(math.atan(ta)), math.cos(math.atan(ta))))
    c = _face("Roof C", [(0, 0), (3000 * r, 3000), (3000 * r, 6000), (0, 6000)], lambda x, y: x * tc,
              (-math.sin(math.atan(tc)), 0.0, math.cos(math.atan(tc))))
    return a, c


def _build(deg=30.0, deg_c=None, elev=None, **kw):
    a, c = _pair(deg, deg_c)
    link = hip_link(a, c)
    extra = dict({"offset": 0, "verge": "none", "eaves": "none"}, **(elev or {}))
    ra = dict(dict(a, valleys=[dict(link["a"], **{"with": c["name"], "k": link["k"], "master": True})]), **extra)
    rc = dict(dict(c, valleys=[dict(link["b"], **{"with": a["name"], "k": link["k"], "master": False})]), **extra)
    params = dict({"elevations": [ra, rc], "trim": True, "splash": 0}, **kw)
    return link, params, generate_preview(params)


def _off_plane(out, a, c):
    """{(face, kind): the least distance of any of its parts from the hip's bisecting plane,
    on its own side}. The plane passes through the eaves corner, the origin."""
    na, nc = a["frame"]["n"], c["frame"]["n"]
    d = [na[i] - nc[i] for i in range(3)]
    size = math.sqrt(sum(x * x for x in d))
    d = [x / size for x in d]
    centre = frame_to_world(a["frame"], 4500.0, 1500.0, 0.0)
    side = 1.0 if sum(x * y for x, y in zip(d, centre)) > 0 else -1.0
    least = {}
    for m in out["geometry"]:
        if m["ifc_type"] == "ridge_cap":
            continue
        pts = [frame_to_world(m["frame"], u, v, s) for s in (m["depth"], m["depth"] + m["thickness"]) for u, v in ring_at(m, 0, s)]
        sign = side if m["elevation"] == "Roof A" else -side
        key = (m["elevation"], "upstand" if m.get("hip_part") else m["ifc_type"])
        least[key] = min(least.get(key, 1e9), min(sign * sum(q[i] * d[i] for i in range(3)) for q in pts))
    return least


# ── finding the hip ───────────────────────────────────────────────────────

def test_two_slopes_falling_apart_on_a_sloping_line_meet_at_a_hip():
    a, c = _pair(30.0)
    link = hip_link(a, c)
    t = math.tan(math.radians(30.0))
    assert link["kind"] == "hip" and link["a"]["kind"] == "hip" and link["b"]["kind"] == "hip"
    assert abs(link["length"] - 3000.0 * math.sqrt(2.0 + t * t)) < 1.0            # (0,0,0) to (3000, 3000, 3000·tan)
    for side, e in (("a", a), ("b", c)):
        ends = sorted(tuple(round(x) for x in frame_to_world(e["frame"], *link[side][k])) for k in ("p1", "p2"))
        assert ends == [(0, 0, 0), (3000, 3000, 1732)]
    assert valley_link(a, c) is None and chain_link(a, c) is None                 # not a valley, not a ridge
    assert [x and x["kind"] for x in valley_links(a, [c, a])] == ["hip", None]


def test_a_valley_is_not_a_hip_and_a_ridge_is_not_one_either():
    va, vc = _valley_pair(30.0)
    assert hip_link(va, vc) is None and valley_link(va, vc)["kind"] == "valley"
    t = math.tan(math.radians(30.0))
    left = _face("L", [(0, 0), (6000, 0), (6000, 3000), (0, 3000)], lambda x, y: y * t, (0.0, -0.5, math.cos(math.radians(30.0))))
    right = _face("R", [(0, 3000), (6000, 3000), (6000, 6000), (0, 6000)], lambda x, y: (6000 - y) * t, (0.0, 0.5, math.cos(math.radians(30.0))))
    assert hip_link(left, right) is None and chain_link(left, right)["kind"] == "ridge"


# ── what is built ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("deg, deg_c", [(30.0, None), (20.0, 40.0), (45.0, None)])
def test_every_layer_is_cut_to_the_hip(deg, deg_c):
    a, c = _pair(deg, deg_c)
    _link, _params, out = _build(deg, deg_c, **SEAM)
    least = _off_plane(out, a, c)
    for face in ("Roof A", "Roof C"):
        # 30 mm clear between the two slopes' trays, their seams and their turned-up ends
        for kind in ("plank", "seam", "upstand"):
            assert least[(face, kind)] == pytest.approx(RIDGE_GAP / 2.0, abs=0.6), (face, kind)
        # the deck and battens stop behind the trays' ends, never nearer the plane
        assert least[(face, "deck")] >= RIDGE_GAP / 2.0 - 0.5
        assert least[(face, "batten")] >= RIDGE_GAP / 2.0 - 0.5
        assert least[(face, "deck")] < 60.0


def test_the_layers_behind_meet_on_the_hip():
    a, c = _pair(30.0)
    _link, _params, out = _build(sheathing=True, sheathing_t=12, insulation=True, insulation_t=60, **SEAM)
    least = _off_plane(out, a, c)
    for face in ("Roof A", "Roof C"):
        for kind in ("sheathing", "insulation"):
            assert least[(face, kind)] == pytest.approx(0.0, abs=0.6), (face, kind)


def test_a_tray_keeps_its_width_and_its_seams_stay_straight():
    """An end at the hip slides along the tray's own side: no vertex leaves its seam line."""
    _link, _params, out = _build(**SEAM)
    _l2, _p2, plain = _build(elev={"valleys": []}, **SEAM)
    lines = {round(q[0], 1) for m in plain["geometry"] if m["ifc_type"] == "plank" for q in m["profile"]}
    trays = [m for m in out["geometry"] if m["ifc_type"] == "plank"]
    cut = 0
    for m in trays:
        us = sorted({round(q[0], 1) for q in m["profile"]})
        if m.get("vshift"):
            cut += 1
            if len(m["profile"]) == 4:                  # its two sides and two ends: both sides still on their seam lines
                assert all(u in lines for u in us), m["name"]
            assert max(us) - min(us) <= 530.5
    assert cut >= 10


def test_the_trays_are_turned_up_along_their_raking_ends():
    _link, _params, out = _build(**SEAM)
    ups = [m for m in out["geometry"] if m.get("hip_part")]
    cut = [m for m in out["geometry"] if m["ifc_type"] == "plank" and m.get("vshift")]
    assert ups and len(ups) == len(cut)
    assert {m["thickness"] for m in ups} == {RIDGE_UPSTAND}
    by = {m["name"]: m for m in cut}
    for up in ups:
        tray = by[up["name"].replace(" Hip Upstand", "")]
        assert up["depth"] == pytest.approx(tray["depth"] + tray["thickness"])


def test_one_cap_runs_the_length_of_the_hip():
    link, _params, out = _build(**SEAM)
    (cap,) = [m for m in out["geometry"] if m["ifc_type"] == "ridge_cap"]
    assert cap["name"] == "Roof A Hip Cap Roof C" and cap["thickness"] == pytest.approx(link["length"], abs=0.1)
    assert cap["hip_info"]["leg"] == RIDGE_LEG
    # it runs up the hip line from one end to the other
    ends = [frame_to_world(cap["frame"], 0.0, 0.0, s) for s in (0.0, cap["thickness"])]
    assert sorted(tuple(round(x) for x in q) for q in ends) == [(0, 0, 0), (3000, 3000, 1732)]
    # and sits over both slopes: its section is as wide as two legs, less the fold of the hip
    xs = [q[0] for q in cap["profile"]]
    assert 300.0 < max(xs) - min(xs) < 2.0 * RIDGE_LEG and abs(max(xs) + min(xs)) < 1.0
    # clear of everything under it
    seam_top = max(m["depth"] + m["thickness"] for m in out["geometry"] if m["ifc_type"] == "seam" and not m.get("hip_part"))
    assert min(q[1] for q in cap["profile"]) > 0 and seam_top < 100.0


def test_the_hip_needs_no_shared_chain_and_takes_other_claddings():
    for kw in (dict(cladding_type="panel"), dict(cladding_type="plank", plank_orient="horizontal")):
        a, c = _pair(30.0)
        _link, params, out = _build(**kw)
        least = _off_plane(out, a, c)
        kind = "panel" if kw["cladding_type"] == "panel" else "plank"
        assert least[("Roof A", kind)] == pytest.approx(RIDGE_GAP / 2.0, abs=0.6)
        assert least[("Roof C", kind)] == pytest.approx(RIDGE_GAP / 2.0, abs=0.6)
        rows = [r for r in check_rules(params, out["info"]) if r["name"].endswith("Hip")]
        assert len(rows) == 1 and rows[0]["status"] == "warn" and "agree the hip" in rows[0]["message"]


# ── reported, checked, exported ───────────────────────────────────────────

def test_the_seams_along_a_hip_are_checked_against_940():
    _link, params, out = _build(**SEAM)                                # 530 trays at this rake: 810 along the hip
    info = out["info"][0]["hips"][0]
    assert info["seam_spacing"] == pytest.approx(530.0 / math.sin(math.atan2(3000.0, 3464.1)), abs=1.0) and info["seam_limit"] == HIP_SEAMS
    (row,) = [r for r in check_rules(params, out["info"]) if r["name"].endswith("Hip")]
    assert row["status"] == "pass" and "810 mm apart" in row["message"]
    # a flatter rake across the trays: one slope steep, the other shallow
    _link, params, out = _build(20.0, 50.0, **SEAM)
    rows = [r for r in check_rules(params, out["info"]) if r["name"].endswith("Hip")]
    assert any(r["status"] == "warn" and "at most 940" in r["message"] for r in rows)


def test_a_hip_exports(tmp_path):
    _link, params, out = _build(**SEAM)
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    caps = [e for e in ifc.by_type("IfcCovering") if "Hip Cap" in e.Name]
    assert len(caps) == 1 and caps[0].PredefinedType == "ROOFING"
    assert len([e for e in ifc.by_type("IfcElement") if not e.is_a("IfcElementAssembly")]) == len(out["geometry"])
    dxf = meshes_to_dxf_string(out["geometry"], params, out["info"])
    assert "CORNER_PROFILE" in dxf
    assert out["info"][0]["hip_strips"] and out["info"][1]["hip_strips"]
