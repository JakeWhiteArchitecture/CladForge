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
from cladding_lod import LODS, MASS_COLORS, MASS_T, PLAIN, at_lod, lod_of  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from ifc_generator import meshes_to_ifc  # noqa: E402
from test_ridge import _roof, SEAM  # noqa: E402

KINDS = {"panel": dict(cladding_type="panel"), "plank": dict(cladding_type="plank", plank_orient="horizontal"),
         "seam": dict(cladding_type="plank", plank_profile="seam", plank_orient="vertical")}


def _wall(**kw):
    a = extract_elevation(dict(payload("Elevation A"), options={"debug": False, "clad_depth": 150.0}))
    params = dict({"elevations": [dict(a, offset=0)], "trim": True}, **kw)
    return params, generate_preview(params)


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_lod_100_is_one_flat_sheet_on_the_picked_face_tinted_by_its_cladding(kind):
    params, out = _wall(**KINDS[kind])
    (sheet,) = at_lod(out["geometry"], out["masses"], 100)
    a = params["elevations"][0]
    assert sheet["ifc_type"] == "mass" and sheet["name"] == "Elevation A Cladding zone" and sheet["lod"] == 100
    assert sheet["color"] == MASS_COLORS[kind] and sheet["mass_kind"] == kind
    assert sheet["depth"] == 0.0 and sheet["thickness"] == MASS_T and sheet["frame"] == a["frame"]   # on the face itself
    assert sheet["profile"] == a["polygons"][0]["exterior"]
    assert sheet["holes"] == [] and a["polygons"][0]["holes"]                 # the openings are not cut out of it
    assert len({MASS_COLORS[k] for k in KINDS}) == 3                          # told apart at a glance


def test_each_chain_is_tinted_by_its_own_cladding():
    a = extract_elevation(dict(payload("Elevation A"), options={"debug": False, "clad_depth": 150.0}))
    b = dict(a, name="Elevation B", settings=KINDS["seam"])
    out = generate_preview({"elevations": [dict(a, offset=0, settings=KINDS["panel"]), dict(b, offset=0)], "trim": True})
    assert {m["elevation"]: m["mass_kind"] for m in out["masses"]} == {"Elevation A": "panel", "Elevation B": "seam"}


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
    assert len(parts) == len(meshes) and all(e.Representation for e in parts)
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
