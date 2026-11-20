"""The raking corner: a leaning wall turning a corner with another wall. The planes meet
on a line that is not plumb, so the corner is the end of the leaning wall's face and a
raking edge of the upright one's. It is mitred on the plane that bisects it, or, where the
job's corner is Profile, the boards stop against a corner profile run up the raking line."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import ifcopenshell  # noqa: E402

from cladding_checks import check_rules  # noqa: E402
from cladding_constants import frame_to_world  # noqa: E402
from cladding_corners import TRIM_BACK  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from cladding_primitives import ring_at  # noqa: E402
from fabric_extract import extract_elevation, rake_link, chain_link, hip_link, valley_link, valley_links  # noqa: E402
from ifc_generator import meshes_to_ifc  # noqa: E402

H = 3000.0
PANEL, PLANK_H, PLANK_V = dict(cladding_type="panel"), dict(cladding_type="plank", plank_orient="horizontal"), dict(cladding_type="plank", plank_orient="vertical")


def _quad(name, pts, outward):
    return extract_elevation({"name": name, "faces": [[pts[0], pts[1], pts[2]], [pts[0], pts[2], pts[3]]], "outward": list(outward),
                              "context": [], "options": {"debug": False, "clad_depth": 150.0}})


def _pair(lean=15.0):
    """A wall along x leaning *lean* degrees back (out, if negative) and an upright wall
    round the corner from it at x = 6000: an external right-angled corner on a raking line."""
    a = math.radians(lean)
    dy = H * math.tan(a)
    leaning = _quad("Leaning", [[0, 0, 0], [6000, 0, 0], [6000, dy, H], [0, dy, H]], (0, -math.cos(a), math.sin(a)))
    upright = _quad("Upright", [[6000, 0, 0], [6000, 4000, 0], [6000, 4000, H], [6000, dy, H]], (1, 0, 0))
    return leaning, upright


def _build(lean=15.0, detail=None, match=True, **kw):
    a, b = _pair(lean)
    link = rake_link(a, b)
    said = {} if detail is None else {"detail": detail, "match": match}
    ra = dict(a, offset=0, chain="A", valleys=[dict(link["a"], **dict({"with": "Upright", "k": link["k"], "master": True}, **said))])
    rb = dict(b, offset=0, chain="B", valleys=[dict(link["b"], **dict({"with": "Leaning", "k": link["k"], "master": False}, **said))])
    params = dict({"elevations": [ra, rb], "trim": True, "splash": 0}, **kw)
    return (a, b), params, generate_preview(params)


def _off_plane(out, a, b):
    """{(face, kind): the least distance of its parts from the corner's bisecting plane, on
    its own side}. The plane passes through the foot of the corner, (6000, 0, 0)."""
    na, nb = a["frame"]["n"], b["frame"]["n"]
    d = [na[i] - nb[i] for i in range(3)]
    size = math.sqrt(sum(x * x for x in d))
    d = [x / size for x in d]
    foot = [6000.0, 0.0, 0.0]
    centre = frame_to_world(a["frame"], 3000.0, 1500.0, 0.0)
    side = 1.0 if sum((centre[i] - foot[i]) * d[i] for i in range(3)) > 0 else -1.0
    least = {}
    for m in out["geometry"]:
        if m["ifc_type"] in ("corner_profile", "corner_trim"):
            continue
        pts = [frame_to_world(m["frame"], u, v, s) for s in (m["depth"], m["depth"] + m["thickness"]) for u, v in ring_at(m, 0, s)]
        sign = side if m["elevation"] == "Leaning" else -side
        key = (m["elevation"], m["ifc_type"])
        least[key] = min(least.get(key, 1e9), min(sign * sum((q[i] - foot[i]) * d[i] for i in range(3)) for q in pts))
    return least


# ── finding it ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("lean", [15.0, -15.0])
def test_a_leaning_wall_and_an_upright_one_turn_a_raking_corner(lean):
    a, b = _pair(lean)
    link = rake_link(a, b)
    assert link["kind"] == "rake" and link["external"] and link["k"] == 1.0 and link["angle"] == 90.0
    assert link["length"] == pytest.approx(H / math.cos(math.radians(lean)), abs=0.1)
    # on the leaning wall the corner is the end of its face; on the upright one it rakes
    assert link["a"]["p1"][0] == link["a"]["p2"][0] == 6000.0
    assert abs(link["b"]["p1"][0] - link["b"]["p2"][0]) == pytest.approx(H * math.tan(math.radians(abs(lean))), abs=0.1)
    assert chain_link(a, b) is None and hip_link(a, b) is None and valley_link(a, b) is None
    assert [x and x["kind"] for x in valley_links(a, [b])] == ["rake"]


def test_an_internal_raking_corner_and_what_is_not_one():
    a, _b = _pair(15.0)
    dy = H * math.tan(math.radians(15.0))
    inner = _quad("Inner", [[0, -3000, 0], [0, 0, 0], [0, dy, H], [0, -3000, H]], (1, 0, 0))       # running out from its other end
    link = rake_link(a, inner)
    assert link["external"] is False and link["k"] == -1.0
    wall = _quad("W", [[0, 0, 0], [4000, 0, 0], [4000, 0, H], [0, 0, H]], (0, -1, 0))
    side = _quad("S", [[4000, 0, 0], [4000, 3000, 0], [4000, 3000, H], [4000, 0, H]], (1, 0, 0))
    assert rake_link(wall, side) is None and chain_link(wall, side)                                 # two upright walls: a chain corner
    t = math.tan(math.radians(30.0))
    roof = _quad("Roof", [[0, 0, H], [4000, 0, H], [4000, 3000, H + 3000 * t], [0, 3000, H + 3000 * t]], (0, -0.5, math.cos(math.radians(30.0))))
    assert rake_link(wall, roof) is None                                                            # a roof on a wall: an eaves, not a corner


# ── mitred ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("lean", [15.0, -15.0, 25.0])
@pytest.mark.parametrize("kw, kind", [(PANEL, "panel"), (PLANK_H, "plank"), (PLANK_V, "plank")])
def test_every_layer_is_mitred_on_the_plane_that_bisects_the_corner(kw, kind, lean):
    (a, b), params, out = _build(lean, **kw)
    least = _off_plane(out, a, b)
    for face in ("Leaning", "Upright"):
        assert least[(face, kind)] == pytest.approx(5.0, abs=0.3), face          # half the 10 mm mitre gap, each side
        assert least[(face, "batten")] > 0.0                                     # behind the boards, never through the plane
    (row,) = [r for r in check_rules(params, out["info"]) if r["name"].endswith("Raking corner")]
    assert row["status"] == "pass" and "10 mm apart across the mitre" in row["message"]
    assert not [m for m in out["geometry"] if m["ifc_type"] in ("corner_profile", "corner_trim")]


def test_the_mitre_gap_is_the_chains():
    a, b = _pair(15.0)
    link = rake_link(a, b)
    ra = dict(a, offset=0, mitre_gap=20, valleys=[dict(link["a"], **{"with": "Upright", "k": 1.0, "master": True})])
    rb = dict(b, offset=0, mitre_gap=20, valleys=[dict(link["b"], **{"with": "Leaning", "k": 1.0, "master": False})])
    out = generate_preview(dict({"elevations": [ra, rb], "trim": True, "splash": 0}, **PANEL))
    least = _off_plane(out, a, b)
    assert least[("Leaning", "panel")] == pytest.approx(10.0, abs=0.3) and least[("Upright", "panel")] == pytest.approx(10.0, abs=0.3)


# ── under a corner profile ────────────────────────────────────────────────

@pytest.mark.parametrize("lean", [15.0, -15.0])
def test_panels_stop_against_a_corner_profile_run_up_the_rake(lean):
    (a, b), params, out = _build(lean, corner="profile", **PANEL)
    (profile,) = [m for m in out["geometry"] if m["ifc_type"] == "corner_profile"]
    assert profile["name"] == "Leaning Corner Profile Upright" and profile["thickness"] == pytest.approx(H / math.cos(math.radians(lean)), abs=0.1)
    # its nose's outer corner is where the two panel faces meet, and it runs up the corner line
    face = out["info"][0]["total_depth"]
    foot = frame_to_world(profile["frame"], 0.0, 0.0, 0.0)
    s, c = math.sin(math.radians(lean)), math.cos(math.radians(lean))
    on_line = [6000.0 + face, -face / c + foot[2] * s / c, foot[2]]            # the outer corner line, at the foot's height
    assert foot == pytest.approx(on_line, abs=0.5) and abs(foot[2]) < 30.0
    n = profile["frame"]["n"]
    assert [round(c, 4) for c in n] == [0.0, round(math.sin(math.radians(lean)), 4), round(math.cos(math.radians(lean)), 4)]
    # the panels stop square, the nose (their own thickness) and the 1 mm profile gap short of the outer corner
    least = _off_plane(out, a, b)
    for face_name in ("Leaning", "Upright"):
        assert least[(face_name, "panel")] == pytest.approx(1.0 / math.sqrt(2.0), abs=0.2)
    assert not any(m.get("vshift") for m in out["geometry"] if m["ifc_type"] == "panel")          # square cuts
    (row,) = [r for r in check_rules(params, out["info"]) if r["name"].endswith("Raking corner")]
    assert row["status"] == "pass" and "corner profile" in row["message"]


def test_planks_take_the_plank_corner_profile():
    (a, b), _params, out = _build(15.0, corner="profile", **PLANK_H)
    (trim,) = [m for m in out["geometry"] if m["ifc_type"] == "corner_trim"]
    assert trim["trim_info"]["length"] == pytest.approx(H / math.cos(math.radians(15.0)), abs=0.1)
    least = _off_plane(out, a, b)
    assert least[("Leaning", "plank")] == pytest.approx((TRIM_BACK + 1.0) / math.sqrt(2.0), abs=0.3)


def test_the_corner_is_one_detail_for_both_faces_whatever_their_chains_say():
    """The record carries the detail (the app sends both faces the same one), and a profile
    needs both faces clad alike."""
    _faces, _p, out = _build(15.0, detail="profile", **PANEL)                # the job itself says mitre
    assert len([m for m in out["geometry"] if m["ifc_type"] == "corner_profile"]) == 1
    _faces, params, unlike = _build(15.0, detail="profile", match=False, **PANEL)
    assert not [m for m in unlike["geometry"] if m["ifc_type"] == "corner_profile"]
    (row,) = [r for r in check_rules(params, unlike["info"]) if r["name"].endswith("Raking corner")]
    assert row["status"] == "warn" and "not clad alike" in row["message"]
    _faces, params, lap = _build(15.0, corner="lap", **PANEL)                # Master is not drawn on a rake
    (row,) = [r for r in check_rules(params, lap["info"]) if r["name"].endswith("Raking corner")]
    assert row["status"] == "warn" and "Master is not drawn" in row["message"]


def test_a_raking_corner_exports():
    _faces, params, out = _build(15.0, corner="profile", **PANEL)
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    assert len([e for e in ifc.by_type("IfcElement") if not e.is_a("IfcElementAssembly")]) == len(out["geometry"])
    assert [e.Name for e in ifc.by_type("IfcMember") if "Corner Profile" in e.Name] == ["Leaning Corner Profile Upright"]
