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
    assert corners["1H"]["k_l"] == 1.0


def test_master_at_a_head_both_ways(faces):
    params, out = _opening(faces, {"head": "lap", "head_master": "face"})
    t = _parse(params)["panel_t"]
    assert (0.0, 0.0, -t, 0.0) in _shifts(out)                          # the board covers the lining
    params, out = _opening(faces, {"head": "lap", "head_master": "reveal"}, elev={"head_vent": "back", "head_air": 25})
    gap = _parse(params)["panel_gap"]
    assert (0.0, 0.0, -(25.0 - gap), 0.0) in _shifts(out)                # a gap above the lowered lining


def test_profile_at_a_chain_corner(faces):
    """The nose's outside faces are flush with both panel faces, and each panel stops the
    nose (D, the panel thickness) short of the outer corner line."""
    params, out = _pair(faces, detail="profile", panel_t=10)
    depth = buildup_depth(_parse(params))
    assert _ends(out, "Elevation A", "r") == {round(depth - 10, 6)}
    assert _ends(out, "Elevation B", "l") == {round(depth - 10, 6)}
    (prof,) = [m for m in out["geometry"] if m["ifc_type"] == "corner_profile"]
    assert prof["elevation"] == "Elevation A"                            # the face before the corner
    assert prof["frame"]["n"] == [0.0, 0.0, 1.0] and len(prof["holes"]) == 1   # upright, hollow nose
    pts = [frame_to_world(prof["frame"], u, v, 0) for u, v in prof["profile"]]
    along = [(q[0] - ORIGIN[0]) * U[0] + (q[1] - ORIGIN[1]) * U[1] for q in pts]
    out_of = [(q[0] - ORIGIN[0]) * N[0] + (q[1] - ORIGIN[1]) * N[1] for q in pts]
    assert abs(max(out_of) - depth) < 0.01              # flush with face A's panel face
    assert abs(max(along) - (8000.0 + depth)) < 0.01    # and with face B's, round the corner
    info = prof["profile_info"]
    assert (info["D"], info["flange_a"], info["flange_b"], info["thickness"]) == (10.0, 35.0, 10.0, 1.1)
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
    assert all(c == {"k_l": 0.0, "ext_l": -10.0, "u_l": 0.0} for c in linings.values())   # D short of the arris
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
    assert ps["NoseSize"] == 10.0 and ps["FlangeA"] == 35.0 and ps["FlangeB"] == 10.0 and ps["Thickness"] == 1.1
    assert ps["Length"] > 2000
    dxf = meshes_to_dxf_string(out["geometry"], params, out["info"])
    assert "CORNER_PROFILE" in dxf
