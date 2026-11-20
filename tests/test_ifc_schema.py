"""The IFC export in each schema it offers: valid against that schema, the same elements,
every product placed, and a header an importer can read."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import ifcopenshell  # noqa: E402
import ifcopenshell.validate  # noqa: E402

from synthetic import payload  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from ifc_generator import meshes_to_ifc, own_elements  # noqa: E402

BUILDS = [dict(cladding_type="panel"), dict(cladding_type="plank", plank_profile="seam", plank_orient="vertical"),
          dict(cladding_type="plank", plank_profile="custom", plank_orient="horizontal")]


def _export(schema, kw, **context):
    a = extract_elevation(dict(payload("Elevation A"), options={"debug": False, "clad_depth": 150.0}))
    a["storey"] = {"name": "First Floor", "elevation": 3000.0}
    params = dict({"elevations": [dict(a, offset=0)], "trim": True,
                   "context": dict({"project": "Test House"}, **context)}, **kw)
    if schema:
        params["ifc_schema"] = schema
    out = generate_preview(params)
    path = meshes_to_ifc(out["geometry"], params, out["info"])
    return path, ifcopenshell.open(path), out


@pytest.mark.parametrize("schema, written", [(None, "IFC4X3"), ("IFC4X3", "IFC4X3"), ("IFC4", "IFC4"), ("IFC2X3", "IFC2X3"),
                                             ("ifc4", "IFC4"), ("nonsense", "IFC4X3")])
def test_the_export_is_written_in_the_schema_asked_for(schema, written):
    _path, ifc, _out = _export(schema, BUILDS[0])
    assert ifc.schema.startswith(written) and (written != "IFC4" or ifc.schema == "IFC4")


@pytest.mark.parametrize("schema", ["IFC4X3", "IFC4", "IFC2X3"])
@pytest.mark.parametrize("kw", BUILDS)
def test_every_schema_is_valid_and_carries_every_part(schema, kw):
    path, ifc, out = _export(schema, kw, offset=[250000.0, 120000.0, 0.0])
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(ifc, logger)
    assert not logger.statements, [s["message"][:200] for s in logger.statements[:3]]
    parts = [e for e in ifc.by_type("IfcElement") if not e.is_a("IfcElementAssembly")]
    assert len(parts) == len(own_elements(out["geometry"]))
    assert all(e.Representation for e in parts)
    guids = [e.GlobalId for e in ifc.by_type("IfcRoot")]
    assert len(guids) == len(set(guids))
    assert not [b for b in open(path, "rb").read() if b > 127]                    # plain ASCII, as STEP wants


@pytest.mark.parametrize("schema", ["IFC4X3", "IFC4", "IFC2X3"])
def test_every_product_has_a_placement_and_the_storey_stands_at_its_level(schema):
    _path, ifc, _out = _export(schema, BUILDS[0])
    assert not [e.is_a() for e in ifc.by_type("IfcProduct") if e.ObjectPlacement is None]
    (storey,) = ifc.by_type("IfcBuildingStorey")
    assert storey.Elevation == 3000.0
    assert storey.ObjectPlacement.RelativePlacement.Location.Coordinates[2] == 3000.0
    assert storey.ObjectPlacement.PlacementRelTo == ifc.by_type("IfcBuilding")[0].ObjectPlacement
    (assembly,) = ifc.by_type("IfcElementAssembly")
    assert assembly.ObjectPlacement.PlacementRelTo is None                       # the world, like its parts


@pytest.mark.parametrize("schema, view", [("IFC4X3", "ReferenceView"), ("IFC4", "ReferenceView_V1.2"), ("IFC2X3", "CoordinationView_V2.0")])
def test_the_header_names_the_file_the_schema_and_its_model_view(schema, view):
    path, ifc, _out = _export(schema, BUILDS[0])
    head = open(path).read().split("DATA;")[0]
    assert "ViewDefinition [%s]" % view in head and "FILE_NAME('Test House cladding.ifc'" in head
    assert "/dev/null" not in head
    assert ("FILE_SCHEMA(('%s" % schema) in head
