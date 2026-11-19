"""Custom plank profiles: a fixed plank with its real section (cladding_profiles)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402

from synthetic import payload  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from cladding_constants import _parse  # noqa: E402
from cladding_primitives import ring_at  # noqa: E402
import cladding_profiles as cp  # noqa: E402

CLICK = cp.LIBRARY["cedral_click"]


@pytest.fixture(scope="module")
def face():
    return extract_elevation(payload())


def _build(face, **kw):
    params = dict({"elevations": [dict(face, offset=0)], "cladding_type": "plank", "plank_profile": "custom",
                   "trim": True, "reveals": False}, **kw)
    return params, generate_preview(params)


def _volume(part):
    """A part's volume: its outline's area at mid-depth (exact, the shifts being straight
    in depth) times its thickness."""
    mid = part["depth"] + part["thickness"] / 2
    rings = [ring_at(part, k, mid) for k in range(1 + len(part.get("holes") or []))]
    return Polygon(rings[0], rings[1:]).area * part["thickness"]


def test_cedral_click_from_the_drawing():
    sec = cp.section("cedral_click")
    assert (sec["width"], sec["thickness"], sec["cover"]) == (188.136, 12.034, 174.034)
    assert sec["name"] == "Cedral Click"
    sl = cp.slabs(sec)
    assert [(round(a, 3), round(b, 3)) for a, b, _s in sl] == [(0.0, 5.085), (5.085, 7.034), (7.034, 12.034)]
    # the back's 45-ish chamfer: the first slab's leading side slopes from 24.068 to 21.127
    (a0, b0, a1, b1), = sl[0][2]
    assert round(a0, 3) == 24.068 and round(a1, 3) == round(24.068 - 4.068 * 5.085 / 7.034, 3) and b0 == b1 == 188.136


def test_a_custom_profile_sets_the_plank_and_its_lap():
    p = _parse({"cladding_type": "plank", "plank_profile": "custom", "plank_w": 120, "plank_t": 25, "plank_gap": 8})
    assert (p["plank_w"], p["plank_t"], p["plank_lap"], p["plank_gap"]) == (188.136, 12.034, 14.102, 0.0)
    assert round(p["cover"], 3) == 174.034
    assert _parse({"cladding_type": "plank", "plank_profile": "board"})["section"] is None
    assert _parse({"cladding_type": "panel", "plank_profile": "custom"})["section"] is None


@pytest.mark.parametrize("orient", ["horizontal", "vertical"])
def test_every_plank_carries_its_section(face, orient):
    params, out = _build(face, plank_orient=orient)
    planks = [m for m in out["geometry"] if m["ifc_type"] == "plank"]
    assert planks and all(m.get("parts") for m in planks)
    assert out["info"][0]["cover"] == pytest.approx(174.034)
    area = Polygon(CLICK["ring"]).area
    full = [m for m in planks if len(m["parts"]) == 3 and not m.get("corner") and not m.get("holes")
            and len(m["profile"]) == 4 and not m.get("vshift")]
    assert full
    for m in full:
        us, vs = [q[0] for q in m["profile"]], [q[1] for q in m["profile"]]
        length, across = (max(us) - min(us), max(vs) - min(vs)) if orient == "horizontal" else \
            (max(vs) - min(vs), max(us) - min(us))
        if abs(across - 188.136) > 0.01:
            continue                                   # a course cut by the base or top
        assert sum(_volume(q) for q in m["parts"]) == pytest.approx(area * length, rel=1e-6)
        # the parts stack through the plank's thickness, face outermost
        assert min(q["depth"] for q in m["parts"]) == pytest.approx(m["depth"])
        assert max(q["depth"] + q["thickness"] for q in m["parts"]) == pytest.approx(m["depth"] + 12.034)


def test_courses_lap_by_the_profile(face):
    _params, out = _build(face)
    planks = sorted((m for m in out["geometry"] if m["ifc_type"] == "plank" and m.get("section_at") is not None),
                    key=lambda m: m["section_at"])
    starts = sorted({round(m["section_at"], 3) for m in planks})
    assert all(b - a == pytest.approx(174.034) for a, b in zip(starts, starts[1:]))
    # the next course's face covers this one's lip: faces meet with the drawing's 0.9 joint
    lo = [m for m in planks if m["section_at"] == starts[1]][0]
    face_top = max(q[1] for p in lo["parts"] if p["depth"] + p["thickness"] == pytest.approx(lo["depth"] + 12.034)
                   for q in p["profile"])
    assert starts[2] - face_top == pytest.approx(174.034 - 173.136)


def test_dragging_shows_plain_boards(face):
    _params, out = _build(face, trim=False)
    planks = [m for m in out["geometry"] if m["ifc_type"] == "plank"]
    assert planks and not any(m.get("parts") for m in planks)
    assert all(m["thickness"] == pytest.approx(12.034) for m in planks)


def test_parts_keep_a_mitre_to_the_linings(face):
    """Boards mitred to a window's linings keep the mitre in every slab of the section."""
    _params, out = _build(face, reveals=True)
    mitred = [m for m in out["geometry"] if m["ifc_type"] == "plank" and m.get("vshift") and m.get("parts")]
    assert mitred
    assert any(q.get("vshift") and any(sh[1] for ring in q["vshift"] for sh in ring) for m in mitred for q in m["parts"])


def _dxf(rings):
    out = ["0", "SECTION", "2", "ENTITIES"]
    for r in rings:
        out += ["0", "LWPOLYLINE", "8", "0", "90", str(len(r)), "70", "1"]
        for x, y in r:
            out += ["10", "%.4f" % x, "20", "%.4f" % y]
    out += ["0", "ENDSEC", "0", "EOF"]
    return "\n".join(out)


def test_a_profile_read_from_a_dxf():
    ring = CLICK["ring"]
    copies = [[(x + 500 + k * 174.034, y + 20) for x, y in ring] for k in range(3)]
    sec, msg = cp.profile_from_dxf(_dxf(copies), "Click")
    assert (sec["width"], sec["thickness"], sec["cover"]) == (188.136, 12.034, 174.034)
    assert Polygon(sec["ring"]).symmetric_difference(Polygon(ring)).area < 0.01
    assert "174.0 centres" in msg
    # drawn upside down (face at the bottom), and stacked up the page: read the same way
    flipped = [[(x + k * 174.034, -y) for x, y in ring] for k in range(2)]
    sec2, _ = cp.profile_from_dxf(_dxf(flipped))
    assert Polygon(sec2["ring"]).symmetric_difference(Polygon(ring)).area < 0.01
    stacked = [[(y, x + k * 174.034) for x, y in ring] for k in range(2)]
    sec3, _ = cp.profile_from_dxf(_dxf(stacked))
    assert (sec3["width"], sec3["cover"]) == (188.136, 174.034)
    # one copy: its own width is the cover; nothing closed: a message
    one, msg = cp.profile_from_dxf(_dxf(copies[:1]))
    assert one["cover"] == one["width"] and "one copy" in msg
    assert cp.profile_from_dxf("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF")[0] is None


def test_an_imported_profile_builds(face):
    square = [(0, 0), (140, 0), (140, 18), (0, 18)]
    sec, _ = cp.profile_from_dxf(_dxf([square, [(x + 130, y) for x, y in square]]), "Shiplap")
    _params, out = _build(face, custom_section=sec)
    planks = [m for m in out["geometry"] if m["ifc_type"] == "plank"]
    assert all(len(m["parts"]) == 1 for m in planks) and out["info"][0]["cover"] == pytest.approx(130)


def test_ifc_one_element_per_plank_with_its_section(face):
    import ifcopenshell
    from ifc_generator import meshes_to_ifc
    params, out = _build(face)
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    planks = [e for e in ifc.by_type("IfcCovering") if e.ObjectType == "Cladding plank (Cedral Click)"]
    assert len(planks) == sum(1 for m in out["geometry"] if m["ifc_type"] == "plank")
    items = planks[0].Representation.Representations[0].Items
    assert len(items) == 3 and all(i.is_a("IfcFacetedBrep") for i in items)
