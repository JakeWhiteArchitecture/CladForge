import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from synthetic import payload, N, U  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_constants import _parse, frame_to_world  # noqa: E402
from cladding_preview import generate_preview, check_rules  # noqa: E402


@pytest.fixture(scope="module")
def elevation():
    return extract_elevation(payload())


def test_extract_frame_and_size(elevation):
    assert elevation["ok"], elevation["warnings"]
    assert abs(elevation["width"] - 8000) < 1
    assert abs(elevation["height"] - 3000) < 1
    f = elevation["frame"]
    assert all(abs(a - b) < 1e-4 for a, b in zip(f["n"], N))
    assert all(abs(a - b) < 1e-4 for a, b in zip(f["u"], U))
    # origin maps local (0,0) back onto the wall's bottom-left corner
    assert all(abs(a - b) < 1 for a, b in zip(frame_to_world(f, 0, 0), (10000.0, 5000.0, 100.0)))


def test_openings_and_penetrations(elevation):
    holes = [h for pg in elevation["polygons"] for h in pg["holes"]]
    # window (1200 x 1200) and pipe (100 x 100) are interior holes
    assert len(holes) == 2, holes
    areas = sorted(round(abs(_area(h))) for h in holes)
    assert any(abs(a - 1200 * 1200) < 10 for a in areas)
    assert any(abs(a - 100 * 100) < 10 for a in areas)
    # slab and roof bands reach the edges, so they notch the outer boundary instead
    ext = [tuple(pt) for pt in elevation["polygons"][0]["exterior"]]
    assert (4500.0, 2000.0) in ext and (4500.0, 2200.0) in ext
    assert (1500.0, 1400.0) in ext and (1500.0, 1600.0) in ext
    assert abs(_area(elevation["polygons"][0]["exterior"])) - 1200 * 1200 - 100 * 100 < 8000 * 3000


def test_abutments(elevation):
    ab = {a["source"]: a for a in elevation["abutments"]}
    assert "base" in ab and ab["base"]["v"] == 0
    assert "IfcSlab" in ab and abs(ab["IfcSlab"]["v"] - 2200) < 1
    assert abs(ab["IfcSlab"]["u0"] - 4500) < 1 and abs(ab["IfcSlab"]["u1"] - 8000) < 1
    assert "IfcRoof" in ab and abs(ab["IfcRoof"]["v"] - 1600) < 1
    assert ab["IfcRoof"]["u0"] == 0 and abs(ab["IfcRoof"]["u1"] - 1500) < 1
    # the internal floor never reaches the face, so it is not an abutment
    assert len(elevation["abutments"]) == 3


def _area(ring):
    s = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        s += x0 * y1 - x1 * y0
    return s / 2


def _params(elevation, **kw):
    p = {"elevations": [dict(elevation, offset=kw.pop("offset", 0))]}
    p.update(kw)
    return p


def test_horizontal_planks_trimmed(elevation):
    out = generate_preview(_params(elevation, sheathing=True, insulation=True, trim=True))
    types = {}
    for m in out["geometry"]:
        types[m["ifc_type"]] = types.get(m["ifc_type"], 0) + 1
    assert types["sheathing"] == 1 and types["insulation"] == 1
    assert types["batten"] > 10 and types["plank"] > 20
    assert "counter_batten" not in types
    info = out["info"][0]
    assert info["base_level"] == 150 and info["battens"] == "vertical"
    # nothing sits inside the window opening once trimmed
    for m in out["geometry"]:
        if m["ifc_type"] in ("plank", "batten"):
            for u, v in m["profile"]:
                assert not (2000 < u < 3200 and 900 < v < 2100), (m["name"], u, v)
    assert out["dimensions"]


def test_vertical_planks_have_counter_battens(elevation):
    out = generate_preview(_params(elevation, plank_orient="vertical", trim=False))
    types = {m["ifc_type"] for m in out["geometry"]}
    assert "counter_batten" in types and "batten" in types and "plank" in types
    assert out["info"][0]["battens"] == "horizontal"
    # Counter-batten centres are a parameter, not a constant: they set the spacing.
    for centres in (400.0, 800.0):
        cbs = sorted(min(q[0] for q in m["profile"])
                     for m in generate_preview(_params(elevation, plank_orient="vertical",
                                                       cb_centres=centres, trim=False))["geometry"]
                     if m["ifc_type"] == "counter_batten")
        gaps = [round(b - a, 1) for a, b in zip(cbs, cbs[1:])]
        assert gaps and max(gaps) <= centres + 1, (centres, gaps)


def test_panels_joints_on_battens(elevation):
    p = _parse(_params(elevation, cladding_type="panel", panel_w=1200, panel_gap=10))
    assert abs(p["batten_centres"] - 1210 / 3) < 1e-6  # bay 1210 needs 3 spans under 600, locked
    out = generate_preview(_params(elevation, cladding_type="panel", trim=False, offset=300))
    battens = sorted(m["profile"][0][0] + 25 for m in out["geometry"] if m["ifc_type"] == "batten")
    panels = [m for m in out["geometry"] if m["ifc_type"] == "panel"]
    assert panels
    # Every panel edge is backed: a batten centred on the joint gap, or, at a window
    # jamb, the solid timber cavity closer that the setting-out started from.
    jambs = {2000.0, 3200.0}
    for m in panels:
        u0, u1 = m["profile"][0][0], m["profile"][1][0]
        for edge in (u0, u1):
            if 1 < edge < 7999 and min(abs(edge - j) for j in jambs) > 10.5:      # on a jamb line, or the gap off it
                assert min(abs(b - (edge - 5)) for b in battens) < 1e-6 or min(abs(b - (edge + 5)) for b in battens) < 1e-6
    # Supports are the battens plus the closers at the jambs; only a window is unsupported.
    supports = sorted(set(battens) | jambs)
    for a, b in zip(supports, supports[1:]):
        if (a, b) == (2000.0, 3200.0):
            continue
        assert b - a <= 600 + 1e-6, (a, b)
    # Driven by the openings, every bay is sized to fit, so there is no closing cut:
    # the panels vary in width instead, and none exceeds the maximum.
    info = out["info"][0]
    assert info["set_out_from_openings"] and info["openings"] == 1
    assert info["closing_cut_left"] == 0 and info["closing_cut_right"] == 0
    assert len(info["panel_widths"]) > 1 and max(info["panel_widths"]) <= 1200 + 1e-6
    checks = {c["name"]: c["status"] for c in check_rules(_params(elevation, cladding_type="panel", offset=300))}
    assert checks.get("Setting-out") == "pass"
    # Centred instead, the last panel on each side is a narrow closing cut.
    centred = generate_preview(_params(elevation, cladding_type="panel", trim=False, offset=300,
                                       set_out_from_openings=False))["info"][0]
    assert centred["closing_cut_left"] > 0 and centred["closing_cut_right"] > 0


def test_check_rules(elevation):
    checks = {c["name"]: c["status"] for c in check_rules(_params(elevation, splash=120, insulation=True))}
    assert checks["Splash zone"] == "warn"
    assert checks["Fixing through insulation"] == "fail"
    assert checks["Cavity depth"] == "pass"
    checks = {c["name"]: c["status"] for c in check_rules(
        _params(elevation, plank_orient="vertical", counter_batten="no"))}
    assert checks["Buildup"] == "fail"


def test_panel_seam_noggins_wait_for_counter_battens(elevation):
    """A noggin between vertical battens sits on the drainage plane and dams it. The
    horizontal seams stay unsupported until a counter-batten layer holds the battens
    off the wall."""
    def build(cb):
        out = generate_preview(_params(elevation, cladding_type="panel", counter_batten=cb,
                                       panel_h=1200, trim=False))
        types = [m["ifc_type"] for m in out["geometry"]]
        check = next(c for c in check_rules(_params(elevation, cladding_type="panel",
                                                    counter_batten=cb, panel_h=1200), out["info"])
                     if c["name"] == "Panel seams")
        return types.count("cross_batten"), types.count("counter_batten"), check["status"]

    noggins, cbs, status = build("no")
    assert (noggins, cbs, status) == (0, 0, "warn")
    noggins, cbs, status = build("yes")
    assert noggins > 0 and cbs > 0 and status == "pass"


def test_sliding_the_offset_keeps_the_batten_centres(elevation):
    """Sliding the set-out moves the grid of battens as a whole. Only the distance to
    the edge battens changes; every gap between regular battens stays at the pitch."""
    def interior_gaps(kind, offset, **kw):
        out = generate_preview(_params(elevation, cladding_type=kind, offset=offset, trim=False,
                                       set_out_from_openings=False, **kw))
        us = sorted({round(min(q[0] for q in m["profile"]) + 25, 1)
                     for m in out["geometry"] if m["ifc_type"] == "batten"})
        gaps = [round(b - a, 1) for a, b in zip(us, us[1:])]
        return gaps[1:-1], out     # drop the two end gaps, which are meant to vary

    for kind in ("plank", "panel"):
        base, _ = interior_gaps(kind, 0)
        pitch = max(set(base), key=base.count)
        for off in (-150, 40, 170):
            gaps, out = interior_gaps(kind, off)
            assert gaps and all(abs(g - pitch) < 1.0 for g in gaps), (kind, off, gaps)
            labels = [d["label"] for d in out["dimensions"] if "c/c" in d["label"]]
            assert labels == ["%.0f c/c" % pitch], (kind, off, labels)


def test_the_engine_leaves_where_it_is_on_the_page(elevation, monkeypatch):
    """An abort inside WASM kills Python outright, so as it builds the preview leaves the step
    and the faces it is on where the app can read them after a crash; cleared once done."""
    import cladding_constants

    class Page:
        def __init__(self):
            self.seen = []

        def __setattr__(self, name, value):
            if name == "_engineStep":
                self.seen.append(value)
            object.__setattr__(self, name, value)

    page = Page()
    monkeypatch.setattr(cladding_constants, "_js", type("JS", (), {"globalThis": page}))
    generate_preview({"elevations": [dict(elevation, offset=0)], "trim": True})
    assert "apply_boolean_ops (%s)" % elevation["name"] in page.seen
    assert any(s.startswith("hip_ends (") for s in page.seen)
    assert page.seen[-1] == ""


def test_extraction_leaves_where_it_is_on_the_page_too(monkeypatch):
    """Picking a face can kill the engine as well: extraction leaves its stage, down to the
    nearby element it is reading, and clears it once done."""
    import cladding_constants

    seen = []
    page = type("Page", (), {"__setattr__": lambda self, k, v: seen.append(v) if k == "_engineStep" else None})()
    monkeypatch.setattr(cladding_constants, "_js", type("JS", (), {"globalThis": page}))
    out = extract_elevation(dict(payload(), name="Elevation A"))
    assert out["ok"]
    assert any(s.startswith("extracting Elevation A: fitting the plane") for s in seen)
    assert any(s.startswith("extracting Elevation A: reading ") for s in seen)
    assert seen[-1] == ""


def test_model_text_is_not_read(monkeypatch):
    """Revit's 3D Model Text comes out as a proxy, thousands of sliver triangles that GEOS
    fails on (an abort in the browser). It is not part of the building: never read."""
    import cladding_constants
    seen = []
    page = type("Page", (), {"__setattr__": lambda self, k, v: seen.append(v) if k == "_engineStep" else None})()
    monkeypatch.setattr(cladding_constants, "_js", type("JS", (), {"globalThis": page}))
    pl = dict(payload(), name="Elevation A")
    text = {"type": "IfcBuildingElementProxy", "name": "Model Text:150mm Model Text:4499637",
            "tris": [[[x, -1.0, 1000.0], [x + 0.001, 1.0, 1000.0], [x, 1.0, 1150.0]] for x in range(0, 900, 3)]}
    pl["context"] = list(pl.get("context") or []) + [text]
    out = extract_elevation(pl)
    assert out["ok"]
    assert not any("Model Text" in s for s in seen)


def test_an_intricate_element_is_sectioned_on_the_grid():
    """Past INTRICATE_TRIS an element's section is joined on the 0.01 mm grid, where GEOS
    cannot fail: a slab cut into slivers still sections as the slab."""
    from fabric_extract import INTRICATE_TRIS, _section
    tris = []
    k = INTRICATE_TRIS                      # thin strips across a 1000 x 200 slab, through the face
    for i in range(k):
        x0, x1 = 1000.0 * i / k, 1000.0 * (i + 1) / k
        for y0, y1 in ((-100.0, 100.0),):
            tris.append([(x0, y0, 0.0), (x1, y0, 0.0), (x1, y1, 0.0)])
            tris.append([(x0, y0, 0.0), (x1, y1, 0.0), (x0, y1, 0.0)])
            tris.append([(x0, y0, 200.0), (x1, y1, 200.0), (x1, y0, 200.0)])
            tris.append([(x0, y0, 200.0), (x0, y1, 200.0), (x1, y1, 200.0)])
    for x in (0.0, 1000.0):
        tris.append([(x, -100.0, 0.0), (x, 100.0, 0.0), (x, 100.0, 200.0)])
        tris.append([(x, -100.0, 0.0), (x, 100.0, 200.0), (x, -100.0, 200.0)])
    for y in (-100.0, 100.0):
        tris.append([(0.0, y, 0.0), (1000.0, y, 0.0), (1000.0, y, 200.0)])
        tris.append([(0.0, y, 0.0), (1000.0, y, 200.0), (0.0, y, 200.0)])
    straddles, touches, poly, _pts = _section(tris, (0.0, -1.0, 0.0), 0.0, (1.0, 0.0, 0.0))
    assert straddles and touches and poly is not None and poly.is_valid
    assert poly.area == pytest.approx(1000.0 * 200.0, rel=0.01)


def test_a_penetration_is_cut_on_the_grid_where_anything_nearly_meets():
    """A face beside a neighbouring slope modelled a hair off the hip: its edge and the
    slope's cut nearly coincide, which GEOS can fail to node (an abort in the browser). The
    cut is taken on the 0.01 mm grid there; a clean face keeps its exact coordinates."""
    from shapely.geometry import Polygon
    from fabric_extract import _cut_out
    face = Polygon([(0, 0), (3000, 0), (3000, 3000), (0, 3000)])
    off = Polygon([(-500, 2999.996), (2000.003, 2999.996), (2000.003, 4000), (-500, 4000)])   # 0.004 mm into the face
    cut = _cut_out(face, off)
    assert cut.is_valid and abs(cut.area - 3000 * 3000) < 30.0
    assert all(abs(c * 100 - round(c * 100)) < 1e-6 for x, y in cut.exterior.coords for c in (x, y))   # on the grid
    clean = Polygon([(1000.123456, 1000), (1500, 1000), (1500, 1500), (1000.123456, 1500)])
    exact = _cut_out(face, clean)
    assert 1000.123456 in [round(x, 6) for x, _y in exact.interiors[0].coords]


def test_faces_picked_on_parallel_surfaces_are_clad_on_the_outermost():
    """A wall face picked on a 9 mm board and on the wall's face beside it, 9 mm behind
    (the job's skirts): the face is the board's, not a plane 4.5 mm into it, and it says
    so. A sliver on a third plane further out does not count."""
    from fabric_extract import fit_plane
    quad = lambda y, x0, x1, z0, z1: [[(x0, y, z0), (x1, y, z0), (x1, y, z1)], [(x0, y, z0), (x1, y, z1), (x0, y, z1)]]   # noqa: E731
    # outward is -y: the wall's face at y = 0, the board on it 9 mm out at y = -9 (n.p = 9 there)
    faces = quad(-9.0, 0, 4000, 0, 2400) + quad(0.0, 4000, 6000, 0, 2400)
    n, d, warnings = fit_plane(faces, (0.0, -1.0, 0.0))
    assert n == pytest.approx((0.0, -1.0, 0.0)) and d == pytest.approx(9.0, abs=1e-6)
    assert any("parallel planes" in w for w in warnings)
    n, d, _w = fit_plane(faces + quad(-39.0, 0, 20, 0, 20), (0.0, -1.0, 0.0))  # a 20 mm square 30 mm further out: a sliver
    assert d == pytest.approx(9.0, abs=1e-6)
    n, d, warnings = fit_plane(quad(0.0, 0, 4000, 0, 2400), (0.0, -1.0, 0.0))  # one plane: as before, no warning
    assert d == pytest.approx(0.0, abs=1e-6) and not warnings


def _box(x0, y0, z0, x1, y1, z1, kind="IfcBeam", name="BOX"):
    c = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    q = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return {"type": kind, "name": name, "tris": [[list(c[a]), list(c[b]), list(c[e])] for f in q for a, b, e in ((f[0], f[1], f[2]), (f[0], f[2], f[3]))]}


@pytest.mark.parametrize("what, cut", [("eaves box", True), ("batten", False), ("box mid-wall", False)])
def test_an_eaves_box_in_front_of_a_wall_s_head_stops_the_cladding_under_it(what, cut):
    """The job's eaves boxes stand in front of the skirt walls' heads, in the cladding zone,
    without passing through the wall: the wall's cladding ran on up behind them. Now the face
    is cut from the box's underside up. A batten modelled in front, or a box part way down the
    wall, leaves the face as it is."""
    from fabric_extract import extract_elevation as ex
    wall = [[(0, 0, 0), (6000, 0, 0), (6000, 0, 3000)], [(0, 0, 0), (6000, 0, 3000), (0, 0, 3000)]]     # facing -y
    box = {"eaves box": _box(1000, -120, 2750, 5000, 0, 3000),
           "batten": _box(1000, -45, 2925, 5000, 0, 3000, name="Beam Dimension Lumber 75x45"),
           "box mid-wall": _box(1000, -120, 1200, 5000, 0, 1450)}[what]
    face = ex({"name": "Wall", "faces": wall, "outward": [0, -1, 0], "context": [box],
               "options": {"debug": False, "clad_depth": 150.0}})
    assert face["ok"]
    if cut:
        assert face["area"] == pytest.approx(6000 * 3000 - 4000 * 250, rel=1e-3)
        assert any("stops under it" in w for w in face["warnings"])
    else:
        assert face["area"] == pytest.approx(6000 * 3000, rel=1e-3)
