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
from shapely.geometry import Polygon  # noqa: E402

from cladding_eaves import EAVES_TRAY  # noqa: E402
from cladding_hip import HIP_SEAMS, HIP_PROJECT, hips_of  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from cladding_primitives import ring_at  # noqa: E402
from cladding_ridge import RIDGE_GAP, RIDGE_LEG, RIDGE_UPSTAND  # noqa: E402
from dxf_generator import meshes_to_dxf_string  # noqa: E402
from fabric_extract import hip_link, valley_link, valley_links, chain_link  # noqa: E402
from ifc_generator import meshes_to_ifc, own_elements  # noqa: E402
from test_ridge import _paired  # noqa: E402
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


def _legs(out, face=None):
    return [m for m in out["geometry"] if m["ifc_type"] == "ridge_cap" and m.get("cap_leg") and face in (None, m["elevation"])]


def _apex(leg, h, rest):
    """The leg's two apex corners at the depth *rest*, as their distance in from the hip
    line: on the plane that bisects the hip they stand k·depth outside it."""
    ring = ring_at(leg, 0, rest)
    dist = sorted((q[0] - h["p1"][0]) * h["m"][0] + (q[1] - h["p1"][1]) * h["m"][1] for q in ring)
    return dist[:2], dist[2:]


def test_each_slope_lays_its_own_leg_of_the_cap():
    link, params, out = _build(**SEAM)
    legs = _legs(out)
    assert sorted(m["name"] for m in legs) == ["Roof A Hip Cap Roof C", "Roof C Hip Cap Roof A"]
    seam_top = max(m["depth"] + m["thickness"] for m in out["geometry"] if m["ifc_type"] == "seam" and not m.get("hip_part"))
    for leg, elev in zip(sorted(legs, key=lambda m: m["name"]), params["elevations"]):
        (h,) = hips_of(elev)
        assert leg["frame"] is elev["frame"] or leg["frame"] == elev["frame"]        # a sheet lying on its slope
        assert leg["depth"] == pytest.approx(seam_top + 2.0) and leg["hip_info"]["leg"] == RIDGE_LEG
        outer, inner = _apex(leg, h, leg["depth"])
        assert outer == pytest.approx([-h["k"] * leg["depth"]] * 2, abs=0.01)      # the apex, on the bisecting plane
        assert inner == pytest.approx([RIDGE_LEG - h["k"] * (leg["depth"] + leg["thickness"] / 2.0)] * 2, abs=0.01)
    # the two legs meet edge to edge along the apex: the same line in the world
    tops = []
    for leg in legs:
        ring = ring_at(leg, 0, leg["depth"])
        elev = next(e for e in params["elevations"] if e["name"] == leg["elevation"])
        (h,) = hips_of(elev)
        on = sorted(ring, key=lambda q: (q[0] - h["p1"][0]) * h["m"][0] + (q[1] - h["p1"][1]) * h["m"][1])[:2]
        tops.append([frame_to_world(leg["frame"], q[0], q[1], leg["depth"]) for q in on])
    (a1, a2), (c1, c2) = tops
    line = [a2[i] - a1[i] for i in range(3)]
    size = math.sqrt(sum(x * x for x in line))
    for q in (c1, c2):                          # C's apex corners lie on the line through A's
        rel = [q[i] - a1[i] for i in range(3)]
        along = sum(rel[i] * line[i] for i in range(3)) / size
        assert math.sqrt(max(0.0, sum(x * x for x in rel) - along * along)) < 0.05


def test_a_free_end_is_cut_back_to_the_roofs_own_edge_and_projects_at_the_eaves():
    """The cap runs on past the end of the hip and is chopped to the line of each slope's
    own edge: level along the top of the slope, and at the eaves 30 mm beyond the trays."""
    _link, params, out = _build(elev={"eaves": "on"}, **SEAM)
    top = params["elevations"][0]["height"]
    tray_foot = min(q[1] for m in out["geometry"] if m["ifc_type"] == "plank" for q in m["profile"])
    assert tray_foot == -EAVES_TRAY
    for leg in _legs(out):
        vs = sorted(q[1] for q in leg["profile"])
        assert vs[:2] == pytest.approx([tray_foot - HIP_PROJECT] * 2) and vs[2:] == pytest.approx([top] * 2)
    _l, _p, bare = _build(**SEAM)               # no eaves: the trays stop at the bottom edge offset
    for leg in _legs(bare):
        assert sorted(q[1] for q in leg["profile"])[:2] == pytest.approx([-HIP_PROJECT] * 2)


def test_the_chain_can_cut_a_free_end_square_instead():
    link, _params, out = _build(elev={"cap_ends": "square"}, **SEAM)
    for leg in _legs(out):
        elev = next(e for e in _params["elevations"] if e["name"] == leg["elevation"])
        (h,) = hips_of(elev)
        dx, dy = (h["p2"][0] - h["p1"][0]) / h["length"], (h["p2"][1] - h["p1"][1]) / h["length"]
        along = sorted((q[0] - h["p1"][0]) * dx + (q[1] - h["p1"][1]) * dy for q in leg["profile"])
        assert along == pytest.approx([0.0, 0.0, link["length"], link["length"]], abs=0.05)


# ── a whole hipped roof: hips married to a ridge ──────────────────────────

def _hipped_roof(**kw):
    """9 x 6 m on plan, 30° all round: two long slopes on a ridge 3 m long, a hip end at
    each end of it. The records as the app sends them: the long slopes one chain at their
    ridge, each hip end its own, every hip kept by both of its faces."""
    t, s, c = math.tan(math.radians(30.0)), math.sin(math.radians(30.0)), math.cos(math.radians(30.0))
    faces = {
        "South": _face("South", [(0, 0), (9000, 0), (6000, 3000), (3000, 3000)], lambda x, y: y * t, (0, -s, c)),
        "North": _face("North", [(9000, 6000), (0, 6000), (3000, 3000), (6000, 3000)], lambda x, y: (6000 - y) * t, (0, s, c)),
        "West": _face("West", [(0, 6000), (0, 0), (3000, 3000)], lambda x, y: x * t, (-s, 0, c)),
        "East": _face("East", [(9000, 0), (9000, 6000), (6000, 3000)], lambda x, y: (9000 - x) * t, (s, 0, c)),
    }
    ridge = chain_link(faces["South"], faces["North"])
    south, north = _paired(faces["South"], faces["North"], ridge)
    recs = {"South": dict(south, valleys=[]), "North": dict(north, valleys=[]),
            "West": dict(faces["West"], offset=0, chain="West", valleys=[]), "East": dict(faces["East"], offset=0, chain="East", valleys=[])}
    names = list(faces)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            link = hip_link(faces[a], faces[b])
            if link:
                recs[a]["valleys"].append(dict(link["a"], **{"with": b, "k": link["k"], "master": True}))
                recs[b]["valleys"].append(dict(link["b"], **{"with": a, "k": link["k"], "master": False}))
    params = dict({"elevations": list(recs.values()), "trim": True, "splash": 0}, **dict(SEAM, **kw))
    return ridge, params, generate_preview(params)


def test_a_hipped_roof_has_a_ridge_as_long_as_its_slopes_share_and_four_hips():
    ridge, params, out = _hipped_roof()
    assert ridge["kind"] == "ridge" and ridge["u_a"] == [3000.0, 6000.0]           # not the 9 m of the eaves
    hips = {(i["elevation"], h["with"]) for i in out["info"] for h in i.get("hips", [])}
    assert len(hips) == 8                                                         # four hips, each kept by both its faces
    info = next(i for i in out["info"] if i["elevation"] == "South")["ridge"]
    assert info["length"] == 3000.0 and info["legs"]
    rows = check_rules(params, out["info"])
    assert len([r for r in rows if r["name"].endswith("Hip")]) == 4 and len([r for r in rows if r["name"].endswith("Ridge")]) == 1
    assert not [r for r in rows if r["name"].endswith("Verge")]                    # no free side on any slope


def test_the_caps_are_mitred_where_hips_run_into_the_ridge():
    """On each slope the legs of the caps that meet there (two hip caps at the head of a hip
    end; a hip cap and the ridge cap at an end of the ridge) are cut on one line: they
    touch along it and nowhere lie over one another. The ridge cap stops at the hips."""
    _ridge, params, out = _hipped_roof()
    assert not [m for m in out["geometry"] if m["ifc_type"] == "ridge_cap" and not m.get("cap_leg") and not m.get("stop_end")]    # no folded cap run through
    by_face = {}
    for leg in _legs(out):
        by_face.setdefault(leg["elevation"], []).append(leg)
    assert {k: len(v) for k, v in by_face.items()} == {"South": 3, "North": 3, "West": 2, "East": 2}
    for face, legs in by_face.items():
        for depth in (legs[0]["depth"], legs[0]["depth"] + legs[0]["thickness"]):
            shapes = [Polygon(ring_at(m, 0, depth)) for m in legs]
            assert all(s.is_valid and s.area > 1e5 for s in shapes)
            for i, a in enumerate(shapes):
                for b in shapes[i + 1:]:
                    assert a.intersection(b).area < 1.0, face                      # never overlapping
            if len(legs) == 3:                                                    # ridge leg between two hip legs
                ridge = next(s for m, s in zip(legs, shapes) if "Ridge Cap" in m["name"])
                assert sum(1 for s in shapes if s is not ridge and s.distance(ridge) < 0.01 and s.intersection(ridge.buffer(0.01)).length > 150.0) == 2
                assert 2990.0 < ridge.bounds[0] < 3200.0 and 5800.0 < ridge.bounds[2] < 6010.0
            else:
                assert shapes[0].distance(shapes[1]) < 0.01 and shapes[0].buffer(0.01).intersection(shapes[1]).length > 150.0
    # every leg's foot projects beyond the trays' ends at the eaves; the trays still stop 30 mm apart
    for face, legs in by_face.items():
        foot = min(q[1] for m in out["geometry"] if m["ifc_type"] == "plank" and m["elevation"] == face for q in m["profile"])
        for m in legs:
            if "Hip Cap" in m["name"]:
                assert min(q[1] for q in m["profile"]) == pytest.approx(foot - HIP_PROJECT)


def test_the_ridge_fillers_and_upstands_stop_at_the_hips_too():
    _ridge, _params, out = _hipped_roof()
    fillers = [m for m in out["geometry"] if m["ifc_type"] == "ridge_filler"]
    assert fillers and all(2990.0 <= min(q[0] for q in m["profile"]) and max(q[0] for q in m["profile"]) <= 6010.0 for m in fillers)


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
    assert len(caps) == 2 and caps[0].PredefinedType == "ROOFING"                # a leg on each slope
    assert len([e for e in ifc.by_type("IfcElement") if not e.is_a("IfcElementAssembly")]) == len(own_elements(out["geometry"]))
    dxf = meshes_to_dxf_string(out["geometry"], params, out["info"])
    assert "CORNER_PROFILE" in dxf
    assert out["info"][0]["hip_strips"] and out["info"][1]["hip_strips"]
