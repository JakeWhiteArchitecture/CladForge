"""Build tests/sample_house.ifc: a small two-storey block with a voided window and
door in the south wall, a balcony slab crossing that wall, and a single-storey
wing whose flat roof abuts the east wall. Used by the browser smoke test and
as a demo model. Run: python tests/make_sample.py"""

import os

import ifcopenshell
import ifcopenshell.api

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_house.ifc")


def box(ifc, body, x0, y0, z0, x1, y1, z1):
    pts = [ifc.createIfcCartesianPoint((float(a), float(b))) for a, b in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    pts.append(pts[0])
    profile = ifc.createIfcArbitraryClosedProfileDef("AREA", None, ifc.createIfcPolyline(pts))
    place = ifc.createIfcAxis2Placement3D(ifc.createIfcCartesianPoint((0.0, 0.0, float(z0))), None, None)
    solid = ifc.createIfcExtrudedAreaSolid(profile, place, ifc.createIfcDirection((0.0, 0.0, 1.0)), float(z1 - z0))
    return ifc.createIfcProductDefinitionShape(None, None, [ifc.createIfcShapeRepresentation(body, "Body", "SweptSolid", [solid])])


def sloped(ifc, body, x0, x1, y0, z0, y1, z1, t):
    """Roof slab of vertical thickness t whose top runs from (y0, z0) to (y1, z1), spanning x0..x1."""
    prof = [(y0, z0 - t), (y1, z1 - t), (y1, z1), (y0, z0)]
    pts = [ifc.createIfcCartesianPoint((float(a), float(b))) for a, b in prof]
    pts.append(pts[0])
    profile = ifc.createIfcArbitraryClosedProfileDef("AREA", None, ifc.createIfcPolyline(pts))
    place = ifc.createIfcAxis2Placement3D(ifc.createIfcCartesianPoint((float(x0), 0.0, 0.0)),
                                          ifc.createIfcDirection((1.0, 0.0, 0.0)), ifc.createIfcDirection((0.0, 1.0, 0.0)))
    solid = ifc.createIfcExtrudedAreaSolid(profile, place, ifc.createIfcDirection((0.0, 0.0, 1.0)), float(x1 - x0))
    return ifc.createIfcProductDefinitionShape(None, None, [ifc.createIfcShapeRepresentation(body, "Body", "SweptSolid", [solid])])


def element(ifc, body, storey, cls, name, bounds, predefined=None, shape=None):
    e = ifcopenshell.api.run("root.create_entity", ifc, ifc_class=cls, name=name)
    e.Representation = shape or box(ifc, body, *bounds)
    e.ObjectPlacement = ifc.createIfcLocalPlacement(None, ifc.createIfcAxis2Placement3D(
        ifc.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None))
    if predefined:
        e.PredefinedType = predefined
    if storey is not None:
        ifcopenshell.api.run("spatial.assign_container", ifc, relating_structure=storey, products=[e])
    return e


def main():
    ifc = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcProject", name="Sample House")
    ifcopenshell.api.run("unit.assign_unit", ifc, length={"is_metric": True, "raw": "MILLIMETERS"})
    ctx = ifcopenshell.api.run("context.add_context", ifc, context_type="Model")
    body = ifcopenshell.api.run("context.add_context", ifc, context_type="Model", context_identifier="Body",
                                target_view="MODEL_VIEW", parent=ctx)
    site = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcSite", name="Plot 12")
    building = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuilding", name="House")
    ground = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuildingStorey", name="Ground Floor")
    first = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuildingStorey", name="First Floor")
    ground.Elevation, first.Elevation = 0.0, 3000.0
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=project, products=[site])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=site, products=[building])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=building, products=[ground, first])

    # Main block 8000 x 6000 on plan, walls 300 thick, 6000 high. Outer faces at y=0 (south) and x=8000 (east).
    south = element(ifc, body, ground, "IfcWall", "South wall", (0, 0, 0, 8000, 300, 6000))
    element(ifc, body, ground, "IfcWall", "North wall", (0, 5700, 0, 8000, 6000, 6000))
    element(ifc, body, ground, "IfcWall", "West wall", (0, 300, 0, 300, 5700, 6000))
    element(ifc, body, ground, "IfcWall", "East wall", (7700, 300, 0, 8000, 5700, 6000))
    # Openings voided from the south wall: a window and a door.
    for name, bounds in (("Window", (2000, -50, 900, 3200, 350, 2100)), ("Door", (5000, -50, -10, 5900, 350, 2100))):
        opening = element(ifc, body, None, "IfcOpeningElement", name + " opening", bounds)
        ifc.createIfcRelVoidsElement(ifcopenshell.guid.new(), None, None, None, south, opening)
    element(ifc, body, ground, "IfcSlab", "Ground slab", (0, 0, -150, 8000, 6000, 0), "FLOOR")
    element(ifc, body, first, "IfcSlab", "First floor slab", (300, 300, 2800, 7700, 5700, 3000), "FLOOR")
    element(ifc, body, first, "IfcSlab", "Balcony", (4500, -1500, 3000, 8000, 300, 3200), "FLOOR")
    element(ifc, body, first, "IfcSlab", "Main roof", (0, 0, 6000, 8000, 6000, 6200), "ROOF")
    # Single-storey wing to the east: three walls turning two external corners, and a
    # pitched roof (ridge along X) that meets the main east wall as a gable abutment.
    element(ifc, body, ground, "IfcWall", "Wing south wall", (8000, 1000, 0, 11300, 1300, 3500))  # runs 300 past the corner
    element(ifc, body, ground, "IfcWall", "Wing east wall", (10700, 1000, 0, 11000, 4000, 3500))
    element(ifc, body, ground, "IfcWall", "Wing north wall", (8000, 3700, 0, 11000, 4000, 3500))
    element(ifc, body, ground, "IfcRoof", "Wing roof south", None, "GABLE_ROOF",
            shape=sloped(ifc, body, 7900, 11300, 700, 3400, 2500, 4500, 200))
    element(ifc, body, ground, "IfcRoof", "Wing roof north", None, "GABLE_ROOF",
            shape=sloped(ifc, body, 7900, 11300, 2500, 4500, 4300, 3400, 200))
    ifc.write(OUT)
    print("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
