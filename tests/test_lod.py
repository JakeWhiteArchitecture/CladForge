"""Level of development: the same build at LOD 100 (a flat sheet on each picked face), LOD 200
(boards, battens and layers, plain) and LOD 300 (everything), in the app and in the IFC."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import ifcopenshell  # noqa: E402
import ifcopenshell.validate  # noqa: E402

from synthetic import payload  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

from cladding_lod import LODS, MASS_COLORS, MASS_T, PLAIN, at_lod, lod_of, masses  # noqa: E402
from cladding_primitives import ring_at  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from ifc_generator import meshes_to_ifc, own_elements  # noqa: E402
from test_ridge import _roof, SEAM  # noqa: E402

KINDS = {"panel": dict(cladding_type="panel"), "plank": dict(cladding_type="plank", plank_orient="horizontal"),
         "seam": dict(cladding_type="plank", plank_profile="seam", plank_orient="vertical")}


def _wall(**kw):
    a = extract_elevation(dict(payload("Elevation A"), options={"debug": False, "clad_depth": 150.0}))
    params = dict({"elevations": [dict(a, offset=0)], "trim": True}, **kw)
    return params, generate_preview(params)


def _far(m, k=0):
    return ring_at(m, k, m["depth"] + m["thickness"])


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_lod_100_is_one_plain_mass_per_face_exact_at_the_claddings_extremities(kind):
    """No battens, layers, closers, corner profiles or seams: the cladding alone, as one mass
    with no joints, its outer face, its edges and the edges of its openings where the real
    cladding's are."""
    params, out = _wall(**KINDS[kind])
    (mass,) = at_lod(out["geometry"], out["masses"], 100)
    assert mass["ifc_type"] == "mass" and mass["name"] == "Elevation A Cladding zone" and mass["lod"] == 100
    assert mass["color"] == MASS_COLORS[kind] and mass["mass_kind"] == kind and mass["frame"] == params["elevations"][0]["frame"]
    boards = [m for m in out["geometry"] if m["ifc_type"] in ("panel", "plank")]
    assert len(boards) > 5
    # the outer face, and the back of the cladding
    assert mass["depth"] + mass["thickness"] == pytest.approx(max(m["depth"] + m["thickness"] for m in boards))
    assert mass["depth"] == pytest.approx(min(m["depth"] for m in boards))
    # the same ground covered, to the same edges: the boards' outlines together, less only their joints
    whole = Polygon(_far(mass), [_far(mass, k + 1) for k in range(len(mass["holes"]))])
    union = unary_union([Polygon(_far(m), [_far(m, k + 1) for k in range(len(m["holes"]))]).buffer(0) for m in boards])
    assert whole.bounds == pytest.approx(union.bounds, abs=0.01)
    assert whole.buffer(0.05).contains(union) and whole.difference(union.buffer(0.05)).area < 0.06 * whole.area   # only the joints are filled
    assert union.buffer(8.0).buffer(-8.0).symmetric_difference(whole).area < 1e-4 * whole.area
    assert len(mass["holes"]) == 2                                            # the window and the opening beside it: cut to the cladding's edge
    assert len({MASS_COLORS[k] for k in KINDS}) == 3                          # told apart at a glance


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_at_a_corner_the_masses_keep_the_boards_own_mitres(kind):
    from test_chains import _three_faces
    out = generate_preview(dict(_three_faces(), trim=True, **KINDS[kind]))
    assert [m["name"] for m in out["masses"]] == ["Elevation %s Cladding zone" % c for c in "ABC"]
    for mass in out["masses"]:
        boards = [m for m in out["geometry"] if m["elevation"] == mass["elevation"] and m["ifc_type"] in ("panel", "plank")]
        for s in (mass["depth"], mass["depth"] + mass["thickness"]):          # at its back and at its outer face
            us = [u for u, _v in ring_at(mass, 0, s)]
            want = [u for m in boards if m["depth"] <= s <= m["depth"] + m["thickness"] + 1e-9 for u, _v in ring_at(m, 0, s)]
            assert (min(us), max(us)) == pytest.approx((min(want), max(want)), abs=0.01)
    b = next(m for m in out["masses"] if m["elevation"] == "Elevation B")
    assert {"u_l", "u_r"} <= set(b["corner"])                                # a mitre at each end of the middle face


def test_a_face_with_nothing_built_on_it_yet_is_a_flat_sheet_on_the_picked_surface():
    a = extract_elevation(dict(payload("Elevation A"), options={"debug": False, "clad_depth": 150.0}))
    groups = [(dict(KINDS["plank"]), [a])]
    (sheet,) = masses([], groups)
    assert sheet["depth"] == 0.0 and sheet["thickness"] == MASS_T and sheet["profile"] == a["polygons"][0]["exterior"]
    assert sheet["holes"] == [] and sheet["mass_kind"] == "plank"


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_lod_200_is_the_boards_battens_and_layers_plain(kind):
    _params, out = _wall(**KINDS[kind])
    full, plain = at_lod(out["geometry"], out["masses"], 300), at_lod(out["geometry"], out["masses"], 200)
    assert full == out["geometry"] and all(m.get("lod") in (200, 300) for m in full)
    count = lambda ms, k: len([m for m in ms if m["ifc_type"] == k])          # noqa: E731
    for k in PLAIN:                                                           # every board, batten and layer: the same count
        assert count(plain, k) == count(full, k), k
    assert count(plain, "panel") + count(plain, "plank") > 0 and count(plain, "batten") > 0
    assert not [m for m in plain if m["ifc_type"] in ("closer", "gasket", "corner_profile", "corner_trim")]
    assert len(plain) < len(full)
    if kind == "seam":
        seams = [m for m in plain if m["ifc_type"] == "seam"]
        lines = {m["seam_line"] for m in full if m["ifc_type"] == "seam"}
        assert seams and all(m["seam_part"] == "over_up" and not m.get("vshift") for m in seams)   # one plain upstand a seam
        assert {m["seam_line"] for m in seams} == lines
        assert any(m.get("vshift") for m in full if m["ifc_type"] == "seam")  # the full build's are folded: untouched
        assert count(full, "seam") > 4 * len(seams)


def test_lod_200_drops_the_junction_metalwork_of_a_roof():
    _link, _params, out = _roof(**SEAM)
    full, plain = out["geometry"], at_lod(out["geometry"], out["masses"], 200)
    gone = {"ridge_cap", "ridge_filler", "verge_strip", "valley_gutter", "eaves_strip"}
    assert gone & {m["ifc_type"] for m in full} and not gone & {m["ifc_type"] for m in plain}
    assert not [m for m in plain if m.get("ridge_part")]                      # nor the trays' turned-up ends
    assert {m["name"] for m in plain if m["ifc_type"] == "plank"} == {m["name"] for m in full if m["ifc_type"] == "plank"}
    assert all(lod_of(m) == m["lod"] for m in full)


@pytest.mark.parametrize("lod", LODS)
def test_the_ifc_is_written_at_the_level_asked_for_and_says_which(lod):
    params, out = _wall(**KINDS["seam"])
    meshes = at_lod(out["geometry"], out["masses"], lod)
    path = meshes_to_ifc(meshes, dict(params, lod=lod), out["info"])
    ifc = ifcopenshell.open(path)
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(ifc, logger)
    assert not logger.statements, [s["message"][:200] for s in logger.statements[:3]]
    parts = [e for e in ifc.by_type("IfcElement") if not e.is_a("IfcElementAssembly")]
    assert len(parts) == len(own_elements(meshes)) and all(e.Representation for e in parts)   # a tray's seams are in its element
    said = [p.NominalValue.wrappedValue for p in ifc.by_type("IfcPropertySingleValue") if p.Name == "LevelOfDevelopment"]
    assert said == ["LOD %d" % lod]
    if lod == 100:
        (sheet,) = parts
        assert sheet.is_a("IfcCovering") and sheet.ObjectType == "Cladding zone (LOD 100)" and sheet.Name == "Elevation A Cladding zone"
    else:
        assert not [e for e in parts if e.ObjectType == "Cladding zone (LOD 100)"]


def test_a_file_written_without_a_level_is_the_full_build_and_says_nothing():
    params, out = _wall(**KINDS["panel"])
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    assert not [p for p in ifc.by_type("IfcPropertySingleValue") if p.Name == "LevelOfDevelopment"]
    assert at_lod(out["geometry"], out["masses"], None) == out["geometry"] == at_lod(out["geometry"], out["masses"], 350)


def test_the_masses_of_a_hipped_roof_are_built_on_a_grid_and_follow_the_hip():
    """Trays cut to a hip nearly meet along it. Their union is taken on a 0.01 mm grid, which
    always nodes: a floating-point union of them can fail in GEOS, and in the browser that
    is the end of the engine, not an error."""
    from test_hip import _build as _hipped
    _link, _params, out = _hipped(elev={"eaves": "on"}, **SEAM)
    assert sorted(m["elevation"] for m in out["masses"]) == ["Roof A", "Roof C"]
    for mass in out["masses"]:
        trays = [m for m in out["geometry"] if m["elevation"] == mass["elevation"] and m["ifc_type"] == "plank"
                 and m["frame"]["n"] == mass["frame"]["n"]]
        union = unary_union([Polygon(_far(m)).buffer(0) for m in trays])
        whole = Polygon(_far(mass), [_far(mass, k + 1) for k in range(len(mass["holes"]))])
        assert whole.bounds == pytest.approx(union.bounds, abs=0.02)
        assert whole.symmetric_difference(union.buffer(2.0).buffer(-2.0)).area < 1e-3 * whole.area
        assert all(abs(c * 100.0 - round(c * 100.0)) < 1e-6 for q in mass["profile"] for c in q)   # on the grid
