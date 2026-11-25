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
from cladding_primitives import buildup_depth, ring_at  # noqa: E402
import cladding_corners as profiles  # noqa: E402
from cladding_edges import settings as edge_settings  # noqa: E402

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


def test_profile_not_offered_at_a_reentrant_corner_or_master_at_plank_reveals(faces):
    _params, out = _pair(faces, k=-1.0, detail="profile")
    assert not [m for m in out["geometry"] if m["ifc_type"] == "corner_profile"]
    assert out["info"][0]["corner"]["details"][1] == "mitre"
    _params, out = _pair(faces, k=-1.0, detail="profile", kind="plank")
    assert not [m for m in out["geometry"] if m["ifc_type"] in ("corner_profile", "corner_trim")]
    assert out["info"][0]["corner"]["details"][1] == "mitre"
    _params, out = _opening(faces, {"jamb": "lap", "head": "lap"}, kind="plank")
    assert not [m for m in out["geometry"] if m["ifc_type"] in ("corner_profile", "corner_trim")]
    assert {(d["jamb"], d["head"]) for d in out["info"][0]["opening_details"]} == {("mitre", "mitre")}
    _params, out = _opening(faces, {"jamb": "profile", "head": "profile"}, kind="plank", plank_profile="seam")
    assert not [m for m in out["geometry"] if m["ifc_type"] in ("corner_profile", "corner_trim")]
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


def test_plank_profile_at_jambs_and_head(faces):
    """With plank boards the Marley-type profile goes round windows too: its nose's
    outer corner 26 out from the face plank's back and from the lining's back, the face
    planks stopping 5 + 1 short of the opening line, the linings D + 5 + 1 short of the
    cladding face, and the head's nose running on over the jambs'."""
    params, out = _opening(faces, {"jamb": "profile", "head": "profile"}, kind="plank")
    p = _parse(params)
    D = p["plank_t"]
    trims = {m["name"]: m for m in out["geometry"] if m["ifc_type"] == "corner_trim"}
    assert sorted(trims) == ["Elevation A Head Profile 1", "Elevation A Jamb Profile 1L", "Elevation A Jamb Profile 1R"]
    assert {(d["jamb"], d["head"]) for d in out["info"][0]["opening_details"]} == {("profile", "profile")}
    linings = {m["name"][-2:]: m["corner"] for m in out["geometry"] if m["ifc_type"] == "reveal"}
    assert all(c == {"k_l": 0.0, "ext_l": -(D + 6.0), "u_l": 0.0} for c in linings.values())
    assert (-6.0, 0.0, 0.0, 0.0) in _shifts(out) and (6.0, 0.0, 0.0, 0.0) in _shifts(out)   # both jambs, mirrored
    u0, u1, v0, v1 = out["info"][0]["opening_details"][0]["rect"]
    s = edge_settings(params["elevations"][0])
    air = s["air"] if s["vent"] == "back" else 0.0
    zc = v1 - air - 26                                       # the head nose's outer face
    strips = sorted(tuple(sorted({(q[0], q[1]) for q in r})) for r in out["info"][0]["profile_strips"])
    box_ = lambda ua, ub, va, vb: tuple(sorted({(ua, va), (ub, va), (ub, vb), (ua, vb)}))   # noqa: E731
    assert strips == sorted([box_(u0 - 5, u0 + 26, v0, zc), box_(u1 - 26, u1 + 5, v0, zc),
                             box_(u0 - 5, u1 + 5, zc, zc + 31)])
    assert trims["Elevation A Head Profile 1"]["trim_info"]["length"] == u1 - u0 + 10
    # the cavity closers run right up to the opening line: the nose's back is its flanges' line
    closers = {m["name"][-2:]: [q[0] for q in m["profile"]] for m in out["geometry"] if m["ifc_type"] == "closer"}
    assert max(closers["1L"]) == u0 and min(closers["1R"]) == u1
    assert not out["info"][0]["profile_warnings"]


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


def test_a_batten_across_the_corner_line_stays_on_its_joint(faces):
    """The last joint lands 16 mm short of the wall's corner line, so its batten straddles
    it. Past the line the cavity carries on in front of the other face's insulation: the
    batten is trimmed in height only and stays centred on the joint, its gasket on it."""
    params, out = _pair(faces, detail="profile", panel_t=10, trim=True, insulation=True, insulation_t=100,
                        sheathing=True, sheathing_t=9, batten_d=50)
    geo = [m for m in out["geometry"] if m["elevation"] == "Elevation A"]
    span = lambda m: (min(q[0] for q in m["profile"]), max(q[0] for q in m["profile"]))  # noqa: E731
    panels = sorted(span(m) for m in geo if m["ifc_type"] == "panel" and min(q[1] for q in m["profile"]) < 1500 < max(q[1] for q in m["profile"]))
    joint = (panels[-2][1] + panels[-1][0]) / 2                  # 7984
    (batten,) = [m for m in geo if m["ifc_type"] == "batten" and span(m)[0] < joint < span(m)[1]
                 and min(q[1] for q in m["profile"]) < 1500 < max(q[1] for q in m["profile"])]
    lo, hi = span(batten)
    assert hi > 8000 and abs((lo + hi) / 2 - joint) < 1e-6          # across the line, on the joint
    (gasket,) = [m for m in geo if m["ifc_type"] == "gasket" and m["on"] == batten["name"].rstrip("abcdefgh")
                 and min(q[1] for q in m["profile"]) < 1500 < max(q[1] for q in m["profile"])]
    glo, ghi = span(gasket)
    assert abs((glo + ghi) / 2 - joint) < 1e-6 and round(ghi - glo, 6) == round(hi - lo + 30, 6)


# ── the Marley-type corner profile: Profile with planks ────────────────────

def _trim_plan(m):
    """The trim's section in plan (world x, y)."""
    from shapely.geometry import Polygon
    return Polygon([frame_to_world(m["frame"], a, b, 0.0)[:2] for a, b in m["profile"]])


@pytest.mark.parametrize("orient", ["horizontal", "vertical"])
def test_plank_profile_is_the_marley_type(faces, orient):
    """A 31 mm hollow nose with 34 mm flanges 1 mm thick, set 26 back from the nose's
    faces so they lie flush with the planks' backs, on the batten faces; each face's
    planks stop 5 mm and the profile gap short of where the batten faces meet, square,
    against the side of the nose."""
    params, out = _pair(faces, detail="profile", kind="plank", plank_orient=orient)
    p = _parse(params)
    D = p["plank_t"]
    trims = [m for m in out["geometry"] if m["ifc_type"] == "corner_trim"]
    assert [m["name"] for m in trims] == ["Elevation A Corner Profile"]
    assert not [m for m in out["geometry"] if m["ifc_type"] == "corner_profile"]
    t = trims[0]
    ti = t["trim_info"]
    assert (ti["size"], ti["set_back"], ti["flange"], ti["thickness"]) == (31.0, 26.0, 34.0, 1.0)
    assert len(t["holes"]) == 1                                  # the hollow nose
    face = {i["elevation"]: i["total_depth"] for i in out["info"]}
    back = face["Elevation A"] - D                              # the batten face, as depth
    plan = _trim_plan(t)
    # the nose's outline, nothing of it behind the flanges' line, and the two flanges
    assert abs(plan.area - (31 * 31 - 4 * 4 + 2 * 34)) < 0.5
    fa = out["info"][0]
    frame_a = [e for e in params["elevations"] if e["name"] == "Elevation A"][0]["frame"]
    corner_of = lambda d: frame_to_world(frame_a, 8000 + d, 0.0, d)[:2]     # noqa: E731 (A's right end, k = 1)
    pts = {(round(x, 1), round(y, 1)) for x, y in plan.exterior.coords}
    assert tuple(round(c, 1) for c in corner_of(back + 26)) in pts        # its outer corner
    # flange A's front lies on face A's batten face, 34 past the nose's side
    fa_front = frame_to_world(frame_a, 8000 + back + 26 - 31 - 34, 0.0, back)[:2]
    assert (round(fa_front[0], 1), round(fa_front[1], 1)) in pts
    # the planks stop 5 + 1 short of the batten faces' corner on both faces, square
    a_ends = {m["corner"]["ext_r"] for m in out["geometry"] if m["ifc_type"] == "plank"
              and m["elevation"] == "Elevation A" and "k_r" in (m.get("corner") or {})}
    b_ends = {m["corner"]["ext_l"] for m in out["geometry"] if m["ifc_type"] == "plank"
              and m["elevation"] == "Elevation B" and "k_l" in (m.get("corner") or {})}
    if orient == "horizontal":                                 # courses: their ends marked square there
        assert {round(e, 6) for e in a_ends} == {round(back - 6, 6)}
        assert {round(e, 6) for e in b_ends} == {round(face["Elevation B"] - D - 6, 6)}
        assert all(m["corner"]["k_r"] == 0.0 for m in out["geometry"] if m["ifc_type"] == "plank"
                   and m["elevation"] == "Elevation A" and "k_r" in (m.get("corner") or {}))
    else:                                                      # columns: cut at the trim's end, or gone
        a_max = max(max(q[0] for q in m["profile"]) for m in out["geometry"]
                    if m["ifc_type"] == "plank" and m["elevation"] == "Elevation A")
        b_min = min(min(q[0] for q in m["profile"]) for m in out["geometry"]
                    if m["ifc_type"] == "plank" and m["elevation"] == "Elevation B")
        assert a_max <= 8000 + back - 6 + 1e-6 and b_min >= -(face["Elevation B"] - D - 6) - 1e-6
        # cut back, a column keeps its height and is never wider than a plank
        for m in out["geometry"]:
            if m["ifc_type"] == "plank":
                us, vs = [q[0] for q in m["profile"]], [q[1] for q in m["profile"]]
                assert max(us) - min(us) <= p["plank_w"] + 1e-6 and max(vs) - min(vs) > p["plank_w"]
    # both faces show its 31 mm nose; the solid L goes behind it
    assert [round(max(q[0] for q in s) - min(q[0] for q in s), 3) for s in fa["profile_strips"]] == [31.0]
    timbers = {m["corner_timber"]: m for m in out["geometry"] if m.get("corner_timber")}
    assert set(timbers) == {"wide", "narrow"}               # the solid L behind it, either way
    # its wide leg runs right up to the flanges, flush with face B's batten face, and
    # nothing of the profile lies behind them but the flanges' 1 mm
    from shapely.geometry import Polygon
    reach = max(q[0] for q in timbers["wide"]["profile"])
    assert abs(reach - (8000 + back)) < 1e-6
    for m in out["geometry"]:
        if m.get("corner_timber"):
            us, d0, d1 = [q[0] for q in m["profile"]], float(m["depth"]), float(m["depth"]) + float(m["thickness"])
            plan = Polygon([frame_to_world(m["frame"], u, 0.0, d)[:2]
                            for u, d in ((min(us), d0), (max(us), d0), (max(us), d1), (min(us), d1))])
            assert _trim_plan(t).intersection(plan.buffer(-1.01, join_style=2)).area < 0.01, m["name"]
    if orient == "vertical":                                 # standing up the corner in the batten layer
        for m in timbers.values():
            us, vs = [q[0] for q in m["profile"]], [q[1] for q in m["profile"]]
            assert max(vs) - min(vs) > 1000 > max(us) - min(us)
        wide = timbers["wide"]
        inner = min(q[0] for q in wide["profile"])
        rails = [m for m in out["geometry"] if m["ifc_type"] == "batten" and m["elevation"] == "Elevation A"
                 and not m.get("corner_timber") and float(m["depth"]) == float(wide["depth"])]
        assert rails and all(max(q[0] for q in m["profile"]) <= inner + 1e-6 for m in rails)   # stop against it
    assert not fa["profile_warnings"]
    assert out["info"][0]["corner"]["details"][1] == "profile"


def test_plank_trim_exports_and_checks(faces):
    import ifcopenshell
    import ifcopenshell.util.element as eu
    from ifc_generator import meshes_to_ifc
    from dxf_generator import meshes_to_dxf_string
    params, out = _pair(faces, detail="profile", kind="plank")
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], dict(params, trim=True), out["info"]))
    members = [e for e in ifc.by_type("IfcMember") if e.ObjectType == "Plank corner profile"]
    assert len(members) == 1
    ps = eu.get_psets(members[0])["CladForge_PlankCornerProfile"]
    assert (ps["NoseSize"], ps["FlangeSetBack"], ps["Flange"], ps["Thickness"]) == (31.0, 26.0, 34.0, 1.0)
    assert ps["Length"] > 2000
    assert "CORNER_PROFILE" in meshes_to_dxf_string(out["geometry"], params, out["info"])
    checks = check_rules(params, out["info"])
    corners = [c["message"] for c in checks if c["name"] == "Corners"]
    assert corners and "Marley-type external corner profile" in corners[0]
    assert not [c for c in checks if c["name"] == "Corner profile"]       # no panel-thickness warning


@pytest.mark.parametrize("orient", ["horizontal", "vertical"])
def test_planks_run_out_to_the_trim_once_trimmed(faces, orient):
    """Exported (trimmed to the face), the planks on both faces still run out to the trim,
    the profile gap short of its legs, not back to the wall's corner line."""
    from cladding_primitives import corner_ring
    params, out = _pair(faces, detail="profile", kind="plank", plank_orient=orient, trim=True)
    D = _parse(params)["plank_t"]
    face = {i["elevation"]: i["total_depth"] for i in out["info"]}
    def reach(m, side):
        pts = corner_ring(m["profile"], m.get("corner"), float(m["depth"]) + float(m["thickness"]))
        return max(q[0] for q in pts) if side == "r" else min(q[0] for q in pts)
    a = max(reach(m, "r") for m in out["geometry"] if m["ifc_type"] == "plank" and m["elevation"] == "Elevation A")
    b = min(reach(m, "l") for m in out["geometry"] if m["ifc_type"] == "plank" and m["elevation"] == "Elevation B")
    # courses run right out to it; a column stops on its own edge if the next would not fit
    slack = _parse(params)["plank_gap"] + 0.5 if orient == "vertical" else 0.5
    assert -slack < a - (8000 + face["Elevation A"] - D - 6) < 0.5
    assert -0.5 < b + (face["Elevation B"] - D - 6) < slack
    assert [m for m in out["geometry"] if m.get("corner_timber")]           # and the L survived the trim


@pytest.mark.parametrize("detail", ["profile", "butt"])
def test_the_job_wide_corner_setting_is_kept(faces, detail):
    """The toggle offers Profile and Square for the whole job. Both used to be read back
    as a mitre, so only a corner set on its own ever got them."""
    assert _parse({"corner": detail})["corner"] == detail
    params, out = _pair(faces, detail=None, kind="plank", corner=detail)
    trims = [m for m in out["geometry"] if m["ifc_type"] == "corner_trim"]
    assert bool(trims) == (detail == "profile")
    assert out["info"][0]["corner"]["details"][1] == detail


@pytest.mark.parametrize("kind", ["panel", "plank"])
def test_a_chain_corner_never_shears_a_window_s_profiles(faces, kind):
    """The job's doorway: a face turning a corner at its start (a 1:1 mitre there) and an
    opening with profiled jambs and head. A profile is drawn in its own frame, its section
    across the jamb; one edge of that section sits at 0, which is where the face's corner
    is, so the corner's shear was put on it and each jamb's profile leaned 1 mm sideways for
    every mm up: the two crossed the opening. Only what is laid out on the face is sheared."""
    corner = dict(chain="Chain 1", chain_reversed=False, chain_start=0, corner_lo=1.0, master_lo=False, detail_lo="mitre")
    _params, out = _opening(faces, {"jamb": "profile", "head": "profile"}, kind=kind, elev=corner)
    trims = [m for m in out["geometry"] if m["ifc_type"] in ("corner_profile", "corner_trim")]
    assert len(trims) == 3
    for m in trims:
        assert ring_at(m, 0, 0.0) == ring_at(m, 0, float(m["thickness"])), m["name"]
    assert any((m.get("corner") or {}).get("k_l") for m in out["geometry"] if m["ifc_type"] in ("panel", "plank")), \
        "the face's own boards still take the corner"
