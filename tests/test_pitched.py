"""Pitched faces: a leaning wall, a roof slope, a flat roof, a soffit. Each keeps its own
plane: u stays level, v runs up the slope, and everything set out "up the wall" is set out
up the slope. The proof used throughout is the synthetic wall tipped back about its own
foot: the face, its window, its abutments and every part built on it must be the upright
wall's, turned through the same angle."""

import copy
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import ifcopenshell  # noqa: E402

from synthetic import payload, corner_payload, box_tris, world, U, N, ORIGIN  # noqa: E402
from cladding_constants import frame_to_world, v_to_z, z_to_v  # noqa: E402
from cladding_checks import check_rules  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from fabric_extract import extract_elevation, chain_link, face_axis, fit_plane  # noqa: E402
from ifc_generator import meshes_to_ifc  # noqa: E402

WINDOW = "2000,900"          # opening_key of the synthetic window (u0, v0)


def _turn(p, deg):
    """A world point turned about the wall's foot (the line along U through ORIGIN): the
    wall's top goes back into the building by *deg*, so 90 lays the face flat, facing up."""
    phi = -math.radians(deg)
    q = [p[i] - ORIGIN[i] for i in range(3)]
    kxq = (U[1] * q[2] - U[2] * q[1], U[2] * q[0] - U[0] * q[2], U[0] * q[1] - U[1] * q[0])
    kd = sum(U[i] * q[i] for i in range(3))
    c, s = math.cos(phi), math.sin(phi)
    return [ORIGIN[i] + q[i] * c + kxq[i] * s + U[i] * kd * (1 - c) for i in range(3)]


def _tipped(pl, deg):
    pl = copy.deepcopy(pl)
    pl["faces"] = [[_turn(p, deg) for p in t] for t in pl["faces"]]
    tip = _turn([ORIGIN[i] + pl["outward"][i] for i in range(3)], deg)
    pl["outward"] = [tip[i] - ORIGIN[i] for i in range(3)]
    for c in pl["context"]:
        c["tris"] = [[_turn(p, deg) for p in t] for t in c["tris"]]
    if pl.get("seeds"):
        pl["seeds"] = [_turn(s, deg) for s in pl["seeds"]]
    return pl


def _extract(deg=0.0, pl=None):
    pl = pl or payload()
    pl.setdefault("options", {}).update(debug=False, clad_depth=150.0)
    return extract_elevation(_tipped(pl, deg) if deg else pl)


def _box(mesh, back=0.0):
    """World bounding box of a prism's corners, turned back upright by *back* degrees."""
    pts = [_turn(frame_to_world(mesh["frame"], u, v, d), -back) if back else frame_to_world(mesh["frame"], u, v, d)
           for u, v in mesh["profile"] for d in (mesh["depth"], mesh["depth"] + mesh["thickness"])]
    return [min(q[i] for q in pts) for i in range(3)] + [max(q[i] for q in pts) for i in range(3)]


@pytest.mark.parametrize("deg", [12.0, 58.6, 90.0, 104.0])
def test_a_wall_tipped_back_is_the_same_face_on_its_own_plane(deg):
    up, tipped = _extract(), _extract(deg)
    assert tipped["ok"] and not tipped["warnings"]
    for key in ("width", "height", "n_holes"):
        assert abs(tipped[key] - up[key]) < 0.5, key
    assert abs(tipped["area"] - up["area"]) < 5.0                       # mm², of 21.5 m²
    assert abs(tipped["pitch"] - abs(90.0 - deg)) < 0.1 and tipped["faces_down"] is False
    f = tipped["frame"]
    cross = lambda a, b: (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])  # noqa: E731
    assert abs(f["u"][2]) < 1e-9                                        # u stays level
    assert all(abs(a - b) < 1e-6 for a, b in zip(cross(f["n"], f["u"]), f["v"]))   # v = n × u, up the slope
    assert f["v"][2] >= -1e-9
    assert [(a["source"], round(a["v"]), round(a["u0"]), round(a["u1"])) for a in tipped["abutments"] if a["source"] == "base"] == \
        [("base", 0, 0, 8000)]
    if deg < 90:    # past level the face is seen from its other side, so u runs the other way
        assert [(a["source"], round(a["v"]), round(a["u0"]), round(a["u1"]), a["pitched"]) for a in tipped["abutments"]] == \
            [(a["source"], round(a["v"]), round(a["u0"]), round(a["u1"]), a["pitched"]) for a in up["abutments"]]
        # the face's own corners land where the upright wall's do, turned
        for u, v in ((0, 0), (8000, 3000), (2000, 900)):
            want = _turn(frame_to_world(up["frame"], u, v), deg)
            assert all(abs(a - b) < 0.05 for a, b in zip(frame_to_world(f, u, v), want)), (u, v)


def test_a_face_within_a_degree_of_vertical_is_a_wall_and_is_stood_up():
    r = _extract(0.6)
    assert r["frame"]["v"] == [0.0, 0.0, 1.0] and r["frame"]["n"][2] == 0.0 and r["pitch"] == 90.0
    r = _extract(1.5)
    assert r["frame"]["n"][2] > 0.02 and abs(r["pitch"] - 88.5) < 0.05


def test_a_level_face_runs_u_along_its_long_side_whichever_way_up_it_is():
    flat = _extract(90.0)
    assert abs(flat["pitch"]) < 0.05 and flat["frame"]["n"][2] > 0.999
    assert abs(abs(sum(a * b for a, b in zip(flat["frame"]["u"], U))) - 1.0) < 1e-6        # along the 8000 side
    assert (flat["width"], flat["height"]) == (8000.0, 3000.0)
    soffit = _extract(270.0)
    assert soffit["ok"] and soffit["faces_down"] and soffit["frame"]["n"][2] < -0.999
    assert (soffit["width"], soffit["height"]) == (8000.0, 3000.0)
    # no faces to read an outline from: any level direction, never a failure
    assert face_axis((0.0, 0.0, 1.0)) == (1.0, 0.0, 0.0)


def test_a_face_leaning_out_is_pitched_and_faces_down():
    r = _extract(-20.0)
    assert r["ok"] and r["faces_down"] and abs(r["pitch"] - 70.0) < 0.1
    assert r["frame"]["v"][2] > 0.9 and (r["width"], r["height"]) == (8000.0, 3000.0)
    assert r["n_holes"] == _extract()["n_holes"]


def test_the_click_keeps_its_own_patch_on_a_slope():
    """A slab through the face leaves it in pieces; the click picks its piece, on a slope
    as on a wall (the seed is read in the face's own v, not world height)."""
    def cut(deg):
        pl = payload()
        slab = box_tris(-100.0, 8100.0, 1400.0, 1600.0, -300.0, 600.0)
        pl["context"] = [{"type": "IfcSlab", "name": "Floor through", "tris": [[list(p) for p in t] for t in slab]}]
        pl["seeds"] = [list(world(1000.0, 2500.0))]
        return _extract(deg, pl)
    up, tipped = cut(0.0), cut(50.0)
    assert up["height"] == 1400.0 and tipped["height"] == 1400.0
    assert abs(tipped["area"] - up["area"]) < 1.0


def test_battens_standing_on_a_slope_are_found_as_on_a_wall():
    def with_battens(deg):
        pl = payload()
        pl["context"] = list(pl["context"]) + [
            {"type": "IFCMEMBER", "name": "Batten %d" % i, "tris": [[list(p) for p in t] for t in box_tris(u - 22.5, u + 22.5, 0.0, 2950.0, 0.0, 38.0)]}
            for i, u in enumerate((300.0, 900.0, 1500.0))]
        return _extract(deg, pl)
    up, tipped = with_battens(0.0), with_battens(40.0)
    assert [(q["u"], q["w"], q["v0"], q["v1"]) for q in tipped["posts"]] == [(q["u"], q["w"], q["v0"], q["v1"]) for q in up["posts"]]
    assert len(tipped["posts"]) == 3 and not tipped["posts_passed"]


def test_a_pitched_face_stands_on_its_own_and_never_chains_at_a_corner():
    a, b = _extract(), extract_elevation(corner_payload())
    assert chain_link(a, b)
    assert chain_link(_extract(30.0), b) is None and chain_link(b, _extract(30.0)) is None


BUILDS = [dict(cladding_type="panel"), dict(cladding_type="plank", plank_orient="horizontal"),
          dict(cladding_type="plank", plank_orient="vertical"),
          dict(cladding_type="plank", plank_profile="seam", plank_orient="vertical"),
          dict(cladding_type="panel", _detail={"jamb": "profile", "head": "profile"}),
          dict(cladding_type="plank", plank_orient="horizontal", _detail={"jamb": "profile", "head": "profile"})]


def _build(deg, kw):
    kw = dict(kw)
    detail = kw.pop("_detail", None)
    # no verges or eaves: this is the wall's buildup turned onto the slope, nothing a roof adds
    elev = dict(_extract(deg), offset=0, verge="none", eaves="none", **({"opening_details": {WINDOW: detail}} if detail else {}))
    params = dict({"elevations": [elev], "trim": True}, **kw)
    return params, generate_preview(params)


LEANS = [35.0, 15.0, -15.0, -30.0]       # leaning back (sloping away, facing the sky) and leaning out (overhanging)


@pytest.mark.parametrize("deg", LEANS)
@pytest.mark.parametrize("kw", BUILDS + [dict(cladding_type="plank", plank_orient="vertical"),
                                         dict(cladding_type="panel", _detail={"jamb": "lap", "head": "lap"})])
def test_the_cladding_on_a_slope_is_the_walls_turned_onto_it(kw, deg):
    """Every part (battens, boards, trays and seams, closers, jamb and head linings, corner
    profiles) sits where the upright wall's does, turned through the pitch: so linings are
    square to the slope and profiles run along it. The same whether the wall leans back or
    leans out over the ground."""
    _p0, up = _build(0.0, kw)
    _p1, tipped = _build(deg, kw)
    assert [m["name"] for m in tipped["geometry"]] == [m["name"] for m in up["geometry"]]
    kinds = {m["ifc_type"] for m in up["geometry"]}
    assert {"reveal", "closer", "batten"} <= kinds
    if (kw.get("_detail") or {}).get("jamb") == "profile":
        assert {"corner_profile", "corner_trim"} & kinds
    for a, b in zip(up["geometry"], tipped["geometry"]):
        assert all(abs(x - y) < 0.2 for x, y in zip(_box(a), _box(b, back=deg))), a["name"]
    assert [d["label"] for d in tipped["dimensions"]] == [d["label"] for d in up["dimensions"]]


@pytest.mark.parametrize("deg", [15.0, -15.0, -30.0])
@pytest.mark.parametrize("kw", [dict(cladding_type="panel"), dict(cladding_type="plank", plank_orient="horizontal"),
                                dict(cladding_type="plank", plank_orient="vertical"),
                                dict(cladding_type="plank", plank_orient="horizontal", plank_profile="custom")])
def test_a_leaning_wall_exports_every_part_where_it_was_built(kw, deg):
    """Planks and panels on a wall leaning back or leaning out: in the IFC, as a reader
    builds it, every part has a body and stands where the build put it. A profiled plank
    cut to a mitre is a sweep cut with a brep: its corners used to be rounded to a tenth of
    a micron, which on a leaning face left the brep's faces not quite flat, and the cut
    came back empty: the plank was missing from the model."""
    import ifcopenshell.geom
    from test_ifc_placement import _built_box
    params, out = _build(deg, kw)
    assert out["info"][0]["elevation"] and params["elevations"][0]["faces_down"] is (deg < 0)
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    meshes = {m["name"]: m for m in out["geometry"]}
    checked = 0
    for product in ifc.by_type("IfcElement"):
        if product.is_a("IfcElementAssembly"):
            continue
        verts = ifcopenshell.geom.create_shape(settings, product).geometry.verts
        assert len(verts) >= 12, product.Name                                # it has a body at all
        mesh = meshes[product.Name]
        if mesh.get("section_ring"):
            continue                                                         # a profiled plank is not its board
        scale = 1000.0 if max(abs(c) for c in verts) < 100.0 else 1.0
        xyz = [[verts[i + k] * scale for i in range(0, len(verts), 3)] for k in range(3)]
        assert [min(a) for a in xyz] + [max(a) for a in xyz] == pytest.approx(_built_box(mesh), abs=0.6), product.Name
        checked += 1
    assert checked > 30


def test_levels_are_heights_read_up_the_slope():
    wall, slope, flat = _extract()["frame"], _extract(60.0)["frame"], _extract(90.0)["frame"]
    assert v_to_z(wall, 1200.0) == ORIGIN[2] + 1200.0 and z_to_v(wall, ORIGIN[2] + 1200.0) == 1200.0
    assert abs(v_to_z(slope, 1200.0) - (ORIGIN[2] + 600.0)) < 0.01        # 1200 up a 30° slope rises 600
    assert abs(z_to_v(slope, ORIGIN[2] + 600.0) - 1200.0) < 0.01
    assert abs(z_to_v(flat, v_to_z(flat, 750.0)) - 750.0) < 1e-6          # no height to read: v stands in for it
    # cut between two levels, the cladding stops at them up the slope
    elev = dict(_extract(60.0), offset=0, clip_v_lo=z_to_v(slope, ORIGIN[2] + 300.0), clip_v_hi=z_to_v(slope, ORIGIN[2] + 1200.0))
    out = generate_preview({"elevations": [elev], "trim": True, "cladding_type": "panel", "splash": 0})
    vs = [v for m in out["geometry"] if m["ifc_type"] == "panel" for _u, v in m["profile"]]
    assert 600.0 <= min(vs) <= 615.0 and 2385.0 <= max(vs) <= 2400.0     # the levels, less the edge gap


def _pitch_checks(deg, **kw):
    params = dict({"elevations": [dict(_extract(deg), offset=0)], "trim": True}, **kw)
    checks = check_rules(params)
    return [(c["status"], c["message"]) for c in checks if c["name"] == "Pitch"], \
        next(c["message"] for c in checks if c["name"] == "Splash zone")


SEAM = dict(cladding_type="plank", plank_profile="seam", plank_orient="vertical")


def test_a_wall_gets_no_pitch_check():
    pitch, splash = _pitch_checks(0.0, **SEAM)
    assert pitch == [] and "slope" not in splash


@pytest.mark.parametrize("pitch_deg, status, says", [(2.0, "fail", "below the 3°"), (5.0, "warn", "generally sealed"),
                                                     (31.4, "pass", "run up the slope")])
def test_standing_seam_is_checked_against_the_pitch(pitch_deg, status, says):
    (got,), splash = _pitch_checks(90.0 - pitch_deg, **SEAM)
    assert got[0] == status and says in got[1] and ("pitched %.1f°" % pitch_deg) in got[1]
    assert "measured up the slope" in splash


def test_trays_across_a_slope_fail_and_open_joints_on_a_roof_are_warned():
    (across,), _ = _pitch_checks(60.0, **dict(SEAM, plank_orient="horizontal"))
    assert across[0] == "fail" and "across the slope" in across[1]
    (panels,), _ = _pitch_checks(60.0, cladding_type="panel")
    assert panels[0] == "warn" and "waterproofing" in panels[1] and "panels" in panels[1]
    (leaning,), _ = _pitch_checks(8.0, cladding_type="panel")              # a wall leaning back 8°: still a wall
    assert leaning[0] == "pass" and "leans back" in leaning[1]
    (out,), _ = _pitch_checks(-25.0, **SEAM)                               # leaning out: sheds no water
    assert out[0] == "pass" and "faces down" in out[1]
    level, _ = _pitch_checks(90.0, **SEAM)
    assert level[0][0] == "fail" and "level" in level[0][1]


def test_a_pitched_face_exports_to_ifc_on_its_slope():
    deg = 58.6
    params, out = _build(deg, dict(SEAM))
    _p, up = _build(0.0, dict(SEAM))
    path = meshes_to_ifc(out["geometry"], params, out["info"])
    ifc = ifcopenshell.open(path)
    assert ifc.schema.startswith("IFC4X3")
    products = [e for e in ifc.by_type("IfcElement") if not e.is_a("IfcElementAssembly")]
    up_path = meshes_to_ifc(up["geometry"], _p, up["info"])
    assert len(products) == len([e for e in ifcopenshell.open(up_path).by_type("IfcElement") if not e.is_a("IfcElementAssembly")])
    # every extrusion runs along the slope's own axes: none along world Z alone unless the upright one's does
    n = out["geometry"][0]["frame"]["n"]
    assert abs(n[2] - math.sin(math.radians(deg))) < 1e-6
    axes = {tuple(round(c, 3) for c in p.Axis.DirectionRatios) for p in ifc.by_type("IfcAxis2Placement3D") if p.Axis}
    assert tuple(round(c, 3) for c in n) in axes


def test_the_plane_fit_keeps_a_pitched_normal_and_flattens_a_walls():
    wall = [[[0, 0, 0], [1000, 0, 0], [1000, 0.5, 1000]]]                 # 0.03° off vertical
    n, _d, warnings = fit_plane(wall, [0, -1, 0])
    assert n == (0.0, -1.0, 0.0) and not warnings
    roof = [[[0, 0, 0], [1000, 0, 0], [1000, 1000, 1000]]]                # 45°
    n, d, _w = fit_plane(roof, [0, -1, 1])
    assert abs(n[1] + math.sqrt(0.5)) < 1e-9 and abs(n[2] - math.sqrt(0.5)) < 1e-9 and abs(d) < 1e-9
