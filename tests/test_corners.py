"""Corner details everywhere: Master at internal corners and reveals, matching jambs, and
the outer corner profile."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from synthetic import payload, corner_payload, U, N, ORIGIN  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_preview import generate_preview, check_rules  # noqa: E402
from cladding_constants import _parse, frame_to_world  # noqa: E402
from cladding_primitives import buildup_depth  # noqa: E402
import cladding_corners as profiles  # noqa: E402

WINDOW = "2000,900"          # opening_key of the synthetic window (u0, v0)


@pytest.fixture(scope="module")
def faces():
    return extract_elevation(payload()), extract_elevation(corner_payload())


def _pair(faces, k=1.0, detail="mitre", master_first=True, kind="panel", **kw):
    a, b = faces
    common = dict(chain="Chain 1", chain_reversed=False, offset=0)
    ra = dict(a, chain_start=0, corner_hi=k, master_hi=master_first, detail_hi=detail, **common)
    rb = dict(b, chain_start=a["width"], corner_lo=k, master_lo=not master_first, detail_lo=detail, **common)
    params = dict({"elevations": [ra, rb], "cladding_type": kind, "trim": False, "reveals": False,
                   "set_out_from_openings": False}, **kw)
    return params, generate_preview(params)


def _ends(out, elevation, side):
    return {round(m["corner"]["ext_" + side], 6) for m in out["geometry"]
            if m["ifc_type"] == "panel" and m["elevation"] == elevation and "ext_" + side in (m.get("corner") or {})}


def test_master_at_an_internal_corner_leaves_a_panel_gap(faces):
    """At a re-entrant corner the master runs into the corner and the other board stops
    clear of the master's whole buildup by the panel joint gap: its end is the buildup
    depth plus that gap from the wall corner, and the master board's face is the buildup
    depth from it, so the gap between them is exactly the joint gap."""
    params, out = _pair(faces, k=-1.0, detail="lap", panel_gap=12)
    depth = buildup_depth(_parse(params))
    assert _ends(out, "Elevation A", "r") <= {0.0}                       # the master, into the corner
    (other,) = _ends(out, "Elevation B", "l")
    assert abs(-other - depth - 12) < 1e-6, other                        # stops depth + gap short
    assert abs((-other) - depth - params["panel_gap"]) < 1e-6            # the gap to the master's side


def test_master_at_an_external_corner_is_flush_and_clear(faces):
    params, out = _pair(faces, detail="lap", panel_gap=10)
    p = _parse(params)
    depth = buildup_depth(p)
    assert _ends(out, "Elevation A", "r") == {round(depth, 6)}           # out to the other face
    assert _ends(out, "Elevation B", "l") == {round(depth - p["panel_t"] - 10, 6)}


def _opening(faces, detail, kind="panel", elev=None, **kw):
    a, _b = faces
    params = dict({"elevations": [dict(a, offset=0, opening_details={WINDOW: detail}, **(elev or {}))],
                   "cladding_type": kind, "trim": True, "reveals": True}, **kw)
    return params, generate_preview(params)


def _shifts(out):
    return {tuple(round(x, 6) for x in sh) for m in out["geometry"] if m.get("vshift")
            for ring in m["vshift"] for sh in ring if any(sh)}


def test_both_jambs_of_an_opening_always_match(faces):
    """One detail and one arrangement for both jambs, mirrored: the left jamb's board
    moves into the opening exactly as the right one's does, the other way."""
    params, out = _opening(faces, {"jamb": "lap", "jamb_master": "reveal"})
    gap = _parse(params)["panel_gap"]
    shifts = _shifts(out)
    assert (-gap, 0.0, 0.0, 0.0) in shifts and (gap, 0.0, 0.0, 0.0) in shifts   # both stop a gap short
    linings = {m["name"][-2:]: m.get("corner") for m in out["geometry"] if m["ifc_type"] == "reveal"}
    assert linings["1L"] is None and linings["1R"] is None               # both run out flush
    # the face board as master instead: both jambs covered the same way, mirrored
    params, out = _opening(faces, {"jamb": "lap", "jamb_master": "face"})
    t = _parse(params)["panel_t"]
    shifts = _shifts(out)
    assert (t, 0.0, 0.0, 0.0) in shifts and (-t, 0.0, 0.0, 0.0) in shifts
    corners = {m["name"][-2:]: m["corner"] for m in out["geometry"] if m["ifc_type"] == "reveal"}
    assert corners["1L"] == corners["1R"] == {"k_l": 0.0, "ext_l": -(t + gap), "u_l": 0.0}
    # the head keeps its own choice (mitred here)
    assert corners["1H"]["k_l"] == -1.0


def test_master_at_a_head_both_ways(faces):
    params, out = _opening(faces, {"head": "lap", "head_master": "face"})
    t = _parse(params)["panel_t"]
    assert (0.0, 0.0, -t, 0.0) in _shifts(out)                          # the board covers the lining
    params, out = _opening(faces, {"head": "lap", "head_master": "reveal"}, elev={"head_vent": "back", "head_air": 25})
    gap = _parse(params)["panel_gap"]
    assert (0.0, 0.0, -(25.0 - gap), 0.0) in _shifts(out)                # a gap above the lowered lining


def test_profile_at_a_chain_corner(faces):
    """The nose's outside faces are flush with both panel faces, and each panel stops the
    nose (D, the panel thickness) and the 1 mm profile gap short of the outer corner line."""
    params, out = _pair(faces, detail="profile", panel_t=10)
    depth = buildup_depth(_parse(params))
    assert _ends(out, "Elevation A", "r") == {round(depth - 10 - 1, 6)}
    assert _ends(out, "Elevation B", "l") == {round(depth - 10 - 1, 6)}
    (prof,) = [m for m in out["geometry"] if m["ifc_type"] == "corner_profile"]
    assert prof["elevation"] == "Elevation A"                            # the face before the corner
    assert prof["frame"]["n"] == [0.0, 0.0, 1.0] and len(prof["holes"]) == 1   # upright, hollow nose
    pts = [frame_to_world(prof["frame"], u, v, 0) for u, v in prof["profile"]]
    along = [(q[0] - ORIGIN[0]) * U[0] + (q[1] - ORIGIN[1]) * U[1] for q in pts]
    out_of = [(q[0] - ORIGIN[0]) * N[0] + (q[1] - ORIGIN[1]) * N[1] for q in pts]
    assert abs(max(out_of) - depth) < 0.01              # flush with face A's panel face
    assert abs(max(along) - (8000.0 + depth)) < 0.01    # and with face B's, round the corner
    info = prof["profile_info"]
    assert (info["D"], info["flange_a"], info["flange_b"], info["thickness"]) == (10.0, 35.0, 35.0, 1.1)
    # flange A runs 35 behind face A's panel, which ends D short of the corner
    assert abs(min(along) - (8000.0 + depth - 10 - 35)) < 0.01
    assert not [c for c in check_rules(params, out["info"]) if c["name"] == "Corner profile"]


def test_profile_not_offered_at_a_reentrant_corner_or_with_planks(faces):
    _params, out = _pair(faces, k=-1.0, detail="profile")
    assert not [m for m in out["geometry"] if m["ifc_type"] == "corner_profile"]
    assert out["info"][0]["corner"]["details"][1] == "mitre"
    _params, out = _pair(faces, detail="profile", kind="plank")
    assert not [m for m in out["geometry"] if m["ifc_type"] == "corner_profile"]
    assert out["info"][0]["corner"]["details"][1] == "mitre"
    _params, out = _opening(faces, {"jamb": "profile", "head": "lap"}, kind="plank")
    assert not [m for m in out["geometry"] if m["ifc_type"] == "corner_profile"]
    assert {(d["jamb"], d["head"]) for d in out["info"][0]["opening_details"]} == {("mitre", "mitre")}


def test_profile_at_jambs_and_head(faces):
    params, out = _opening(faces, {"jamb": "profile", "head": "profile"}, panel_t=10)
    names = sorted(m["name"] for m in out["geometry"] if m["ifc_type"] == "corner_profile")
    assert names == ["Elevation A Head Profile 1", "Elevation A Jamb Profile 1L", "Elevation A Jamb Profile 1R"]
    head = next(m for m in out["geometry"] if m["name"] == "Elevation A Head Profile 1")
    assert head["frame"]["n"][2] == 0.0                                  # runs along the head
    linings = {m["name"][-2:]: m["corner"] for m in out["geometry"] if m["ifc_type"] == "reveal"}
    assert all(c == {"k_l": 0.0, "ext_l": -11.0, "u_l": 0.0} for c in linings.values())   # D + 1 short of the arris
    assert len(out["info"][0]["profile_strips"]) == 3


def test_profile_checks(faces):
    params, out = _pair(faces, detail="profile", panel_t=9)
    msgs = [c["message"] for c in check_rules(params, out["info"]) if c["name"] == "Corner profile"]
    assert any("confirm the profile size" in m for m in msgs), msgs
    assert not profiles.flange_supported(out["geometry"], "Elevation A", (-500.0, -400.0), (0.0, 3000.0),
                                         buildup_depth(_parse(params)) - 9)


def test_profile_exports(faces):
    import ifcopenshell
    from ifc_generator import meshes_to_ifc
    from dxf_generator import meshes_to_dxf_string
    params, out = _pair(faces, detail="profile", panel_t=10)
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], dict(params, trim=True), out["info"]))
    members = [e for e in ifc.by_type("IfcMember") if e.ObjectType == "Corner profile"]
    assert len(members) == 1
    import ifcopenshell.util.element as eu
    ps = eu.get_psets(members[0])["CladForge_CornerProfile"]
    assert ps["NoseSize"] == 10.0 and ps["FlangeA"] == 35.0 and ps["FlangeB"] == 35.0 and ps["Thickness"] == 1.1
    assert ps["Length"] > 2000
    dxf = meshes_to_dxf_string(out["geometry"], params, out["info"])
    assert "CORNER_PROFILE" in dxf


# ── the timber L at a profiled corner, the profile gap, and EPDM gaskets ──

def _plan(m, v=None):
    """A timber's footprint in plan (world x, y), from its u extent and depth."""
    from shapely.geometry import Polygon
    us = [q[0] for q in m["profile"]]
    v = m["profile"][0][1] if v is None else v
    d0, d1 = m["depth"], m["depth"] + m["thickness"]
    return Polygon([frame_to_world(m["frame"], u, v, d)[:2] for u, d in ((min(us), d0), (max(us), d0), (max(us), d1), (min(us), d1))])


@pytest.mark.parametrize("insulation", [False, True])
def test_a_profiled_corner_has_a_solid_timber_l(faces, insulation):
    """Rockpanel H.03: the face before the corner has a batten twice as wide running on
    past the corner, flush with the other face's batten face; the other face's batten,
    one wide, sits tight behind it. Square, no mitre, and both survive trimming."""
    from shapely.ops import unary_union
    params, out = _pair(faces, detail="profile", panel_t=10, trim=True, insulation=insulation, insulation_t=100)
    p = _parse(params)
    L = {m["corner_timber"]: m for m in out["geometry"] if m.get("corner_timber")}
    wide, narrow = L["wide"], L["narrow"]
    assert (wide["elevation"], narrow["elevation"]) == ("Elevation A", "Elevation B")
    d0 = wide["depth"]
    d1 = d0 + wide["thickness"]
    uw, un = [q[0] for q in wide["profile"]], [q[0] for q in narrow["profile"]]
    assert (round(min(uw), 3), round(max(uw), 3)) == (round(8000 + d1 - 2 * p["batten_w"], 3), round(8000 + d1, 3))
    assert (round(min(un), 3), round(max(un), 3)) == (round(-d0, 3), round(-d0 + p["batten_w"], 3))
    assert "k_r" not in (wide.get("corner") or {}) and "k_l" not in (narrow.get("corner") or {})   # square
    a, b = _plan(wide), _plan(narrow)
    assert a.intersection(b).area < 1.0 and a.distance(b) < 1e-6                # tight, not overlapping
    assert unary_union([a, b]).geom_type == "Polygon"                            # one solid L
    # flange A lies on the wide timber and flange B on its end: nothing to warn about
    assert not [c for c in check_rules(params, out["info"]) if c["name"] == "Corner profile"]


def test_other_corners_keep_mitred_battens(faces):
    _params, out = _pair(faces, detail="mitre")
    assert not [m for m in out["geometry"] if m.get("corner_timber")]
    assert any((m.get("corner") or {}).get("k_r") == 1.0 for m in out["geometry"]
               if m["ifc_type"] == "batten" and m["elevation"] == "Elevation A")


def test_the_profile_gap(faces):
    params, out = _pair(faces, detail="profile", panel_t=10)
    depth = buildup_depth(_parse(params))
    for gap in (0, 2.5):
        a, b = faces
        params, out = _pair((dict(a, profile_gap=gap), dict(b, profile_gap=gap)), detail="profile", panel_t=10)
        assert _ends(out, "Elevation A", "r") == {round(depth - 10 - gap, 6)}
    # at a jamb the face board stops the gap back from the opening line, both sides
    params, out = _opening(faces, {"jamb": "profile"}, panel_t=10)
    shifts = _shifts(out)
    assert (-1.0, 0.0, 0.0, 0.0) in shifts and (1.0, 0.0, 0.0, 0.0) in shifts


def test_epdm_gaskets_on_the_vertical_timbers(faces):
    """2 mm, 15 mm past the timber each side, in the back 2 mm of the board so the cavity
    keeps its depth; on battens and jamb closers, panels only."""
    params, out = _opening(faces, {}, panel_t=10, trim=False)
    p = _parse(params)
    geo = out["geometry"]
    by_name = {m["name"]: m for m in geo}
    gaskets = [m for m in geo if m["ifc_type"] == "gasket"]
    battens = [m for m in geo if m["ifc_type"] == "batten"]
    back = buildup_depth(p) - p["panel_t"]
    assert gaskets and {round(g["depth"], 6) for g in gaskets} == {round(back, 6)}
    assert {g["thickness"] for g in gaskets} == {2.0}
    assert {by_name[g["on"]]["ifc_type"] for g in gaskets} == {"batten", "closer"}
    assert len([g for g in gaskets if by_name[g["on"]]["ifc_type"] == "batten"]) == len(battens)
    for g in gaskets:
        if by_name[g["on"]]["ifc_type"] != "batten" or by_name[g["on"]].get("corner"):
            continue
        gu, tu = [q[0] for q in g["profile"]], [q[0] for q in by_name[g["on"]]["profile"]]
        assert round(min(tu) - min(gu), 6) == 15.0 and round(max(gu) - max(tu), 6) == 15.0
    _params, planks = _opening(faces, {}, kind="plank")
    assert not [m for m in planks["geometry"] if m["ifc_type"] == "gasket"]


def test_gaskets_and_corner_timbers_export(faces):
    import ifcopenshell
    from ifc_generator import meshes_to_ifc
    from dxf_generator import meshes_to_dxf_string
    params, out = _pair(faces, detail="profile", panel_t=10, trim=True)
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    gaskets = [e for e in ifc.by_type("IfcCovering") if e.ObjectType == "EPDM gasket"]
    assert gaskets and all(e.PredefinedType == "MEMBRANE" for e in gaskets)
    assert "GASKET" in meshes_to_dxf_string(out["geometry"], params, out["info"])
