"""Build tests/leaning_corner.ifc: a wall leaning back 15 degrees and an upright wall round
the corner from it, 3 m high. Their faces meet on a raking line: plumb in neither, the end of
the leaning wall's face and a raking edge of the upright one's. Used to look at, and test in
the browser, the raking corner (cladding_rake).
Run: python tests/make_leaning.py"""

import math
import os

import ifcopenshell
import ifcopenshell.api

from make_sample import element

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leaning_corner.ifc")
LEAN = math.radians(15.0)
H = 3000.0
DY = H * math.tan(LEAN)          # how far the leaning wall's head stands back from its foot
THICK = 300.0


def prism(ifc, body, front, normal):
    """A wall as a faceted solid: its outer face the quad *front* (anticlockwise seen from
    outside), THICK deep behind it along -normal."""
    back = [tuple(p[i] - THICK * normal[i] for i in range(3)) for p in front]
    front = [tuple(float(c) for c in p) for p in front]
    point = lambda p: ifc.createIfcCartesianPoint(p)       # noqa: E731
    face = lambda pts: ifc.createIfcFace([ifc.createIfcFaceOuterBound(ifc.createIfcPolyLoop([point(p) for p in pts]), True)])  # noqa: E731
    faces = [face(front), face(back[::-1])]
    for i in range(4):
        j = (i + 1) % 4
        faces.append(face([front[j], front[i], back[i], back[j]]))
    brep = ifc.createIfcFacetedBrep(ifc.createIfcClosedShell(faces))
    return ifc.createIfcProductDefinitionShape(None, None, [ifc.createIfcShapeRepresentation(body, "Body", "Brep", [brep])])


def main():
    ifc = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcProject", name="Leaning Corner")
    ifcopenshell.api.run("unit.assign_unit", ifc, length={"is_metric": True, "raw": "MILLIMETERS"})
    context = ifcopenshell.api.run("context.add_context", ifc, context_type="Model")
    body = ifcopenshell.api.run("context.add_context", ifc, context_type="Model", context_identifier="Body",
                                target_view="MODEL_VIEW", parent=context)
    site = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcSite", name="Site")
    building = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuilding", name="Leaning Corner")
    storey = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuildingStorey", name="Ground Floor")
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=project, products=[site])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=site, products=[building])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=building, products=[storey])
    element(ifc, body, storey, "IfcWall", "Leaning wall", None, shape=prism(
        ifc, body, [(0, 0, 0), (6000, 0, 0), (6000, DY, H), (0, DY, H)], (0.0, -math.cos(LEAN), math.sin(LEAN))))
    element(ifc, body, storey, "IfcWall", "Upright wall", None, shape=prism(
        ifc, body, [(6000, 0, 0), (6000, 4000, 0), (6000, 4000, H), (6000, DY, H)], (1.0, 0.0, 0.0)))
    ifc.write(OUT)
    print("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
