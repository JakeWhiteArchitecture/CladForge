import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import ifcopenshell  # noqa: E402
import ifcopenshell.geom  # noqa: E402

from synthetic import payload  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from ifc_generator import meshes_to_ifc  # noqa: E402
from dxf_generator import meshes_to_dxf_string  # noqa: E402


@pytest.fixture(scope="module")
def design():
    a = extract_elevation(payload("Elevation A"))
    a["storey"] = {"name": "First Floor", "elevation": 3000.0}
    b = extract_elevation(payload("Elevation B"))
    params = {"elevations": [dict(a, offset=200), dict(b, offset=0)], "sheathing": True,
              "insulation": True, "cladding_type": "panel", "trim": True,
              "context": {"project": "Test House", "site": "Plot 1", "building": "House"}}
    out = generate_preview(params)
    return params, out


def test_ifc_export(design):
    params, out = design
    path = meshes_to_ifc(out["geometry"], params, out["info"])
    ifc = ifcopenshell.open(path)
    assert ifc.schema.startswith("IFC4X3")
    assert ifc.by_type("IfcProject")[0].Name == "Test House"
    storeys = {s.Name: s for s in ifc.by_type("IfcBuildingStorey")}
    assert set(storeys) == {"First Floor", "Ground Floor"}
    assert abs(storeys["First Floor"].Elevation - 3000.0) < 1e-6
    assemblies = ifc.by_type("IfcElementAssembly")
    assert len(assemblies) == 2 and all(a.PredefinedType == "USERDEFINED" for a in assemblies)
    members = ifc.by_type("IfcMember")
    coverings = ifc.by_type("IfcCovering")
    plates = ifc.by_type("IfcPlate")
    assert members and coverings and plates
    assert len(members) + len(coverings) + len(plates) == len(out["geometry"])
    # voids survive: the sheathing carries the window hole
    voided = [e for e in ifc.by_type("IfcArbitraryProfileDefWithVoids")]
    assert voided
    psets = {ps.Name for ps in ifc.by_type("IfcPropertySet")}
    assert {"Pset_CoveringCommon", "Pset_MemberCommon", "CladForge_SettingOut", "CladForge_Disclaimer"} <= psets
    guids = [e.GlobalId for e in ifc.by_type("IfcRoot")]
    assert len(guids) == len(set(guids))
    # deterministic GUIDs across re-export
    ifc2 = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    assert sorted(e.GlobalId for e in ifc2.by_type("IfcElement")) == sorted(e.GlobalId for e in ifc.by_type("IfcElement"))
    # geometry is well-formed: IfcOpenShell can build shapes for the solids
    settings = ifcopenshell.geom.settings()
    for elem in (members[0], coverings[0], plates[0]):
        shape = ifcopenshell.geom.create_shape(settings, elem)
        assert len(shape.geometry.verts) >= 24
    os.unlink(path)


def test_ifc_extrusion_lands_on_the_wall(design):
    """A batten's placement lies on the wall plane, offset outward by its depth."""
    params, out = design
    path = meshes_to_ifc(out["geometry"], params, out["info"])
    ifc = ifcopenshell.open(path)
    from synthetic import N, ORIGIN
    batten = [m for m in out["geometry"] if m["ifc_type"] == "batten"][0]
    solid = [s for s in ifc.by_type("IfcExtrudedAreaSolid")][0]
    loc = solid.Position.Location.Coordinates
    n = solid.Position.Axis.DirectionRatios
    assert all(abs(a - b) < 1e-5 for a, b in zip(n, N))
    # signed distance of the location from the wall face equals some layer depth
    dist = sum((loc[i] - ORIGIN[i]) * N[i] for i in range(3))
    depths = sorted({m["depth"] for m in out["geometry"]})
    assert any(abs(dist - d) < 1e-3 for d in depths), (dist, depths, batten["depth"])
    os.unlink(path)


def test_dxf_export(design):
    params, out = design
    dxf = meshes_to_dxf_string(out["geometry"], params, out["info"])
    assert dxf.startswith("  0\r\nSECTION")
    assert dxf.rstrip().endswith("EOF")
    for layer in ("WALL", "OPENING", "SPLASH_ZONE", "SHEATHING", "INSULATION", "BATTEN", "CLADDING", "DIMS", "NOTES"):
        assert "\r\n%s\r\n" % layer in dxf, layer
    assert "ELEVATION A" in dxf and "ELEVATION B" in dxf
    assert "not a quantity take-off" in dxf
    assert "Approved Document B" in dxf
    assert dxf.count("\r\nLINE\r\n") > 500


@pytest.mark.parametrize("as_walls", [False, True])
def test_panels_can_be_exported_as_walls(design, as_walls):
    """Archicad and others bring an IfcCovering in as a slab; with panels_as_walls each
    panel is an IfcWall instead, to edit as a wall. Nothing else changes."""
    params, out = design
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], dict(params, panels_as_walls=as_walls), out["info"]))
    n_panels = sum(1 for m in out["geometry"] if m["ifc_type"] == "panel")
    walls = ifc.by_type("IfcWall")
    panel_coverings = [c for c in ifc.by_type("IfcCovering") if c.ObjectType == "Cladding panel"]
    assert n_panels > 0
    if as_walls:
        assert len(walls) == n_panels and not panel_coverings
        assert all(w.ObjectType == "Cladding panel" and w.PredefinedType == "USERDEFINED" for w in walls)
        psets = {r.RelatingPropertyDefinition.Name for w in walls for r in w.IsDefinedBy}
        assert "Pset_WallCommon" in psets
        # still in the elevation's assembly, with their material
        assert all(w.Decomposes and w.Decomposes[0].RelatingObject.is_a("IfcElementAssembly") for w in walls)
        assert all(any(r.is_a("IfcRelAssociatesMaterial") for r in w.HasAssociations) for w in walls)
    else:
        assert not walls and len(panel_coverings) == n_panels
    # the rest of the buildup is the same either way
    assert len(ifc.by_type("IfcMember")) == sum(1 for m in out["geometry"] if m["ifc_type"] in
                                                 ("batten", "counter_batten", "cross_batten", "closer", "corner_profile", "corner_trim"))
