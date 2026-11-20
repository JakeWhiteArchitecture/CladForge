"""Build tests/valley_roof.ifc: two roof slabs at right angles on plan, both pitched 30
degrees, falling towards each other and meeting on a sloping valley. Each is modelled up
to the valley line, as a roof is drawn. Used by the browser smoke test.
Run: python tests/make_valley.py"""

import math
import os

import ifcopenshell
import ifcopenshell.api

from make_sample import element

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "valley_roof.ifc")
T = math.tan(math.radians(30.0))
THICK = 200.0


def slab(ifc, body, plan, height):
    """A roof slab as a faceted solid: its top over the plan outline [(x, y)] at
    z = height(x, y), THICK deep measured plumb."""
    top = [(float(x), float(y), float(height(x, y))) for x, y in plan]
    bottom = [(x, y, z - THICK) for x, y, z in top]
    point = lambda p: ifc.createIfcCartesianPoint(p)       # noqa: E731
    face = lambda pts: ifc.createIfcFace([ifc.createIfcFaceOuterBound(ifc.createIfcPolyLoop([point(p) for p in pts]), True)])  # noqa: E731
    faces = [face(top), face(bottom[::-1])]
    n = len(top)
    for i in range(n):
        j = (i + 1) % n
        faces.append(face([top[j], top[i], bottom[i], bottom[j]]))
    brep = ifc.createIfcFacetedBrep(ifc.createIfcClosedShell(faces))
    return ifc.createIfcProductDefinitionShape(None, None, [ifc.createIfcShapeRepresentation(body, "Body", "Brep", [brep])])


def main():
    ifc = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcProject", name="Valley Roof")
    ifcopenshell.api.run("unit.assign_unit", ifc, length={"is_metric": True, "raw": "MILLIMETERS"})
    context = ifcopenshell.api.run("context.add_context", ifc, context_type="Model")
    body = ifcopenshell.api.run("context.add_context", ifc, context_type="Model", context_identifier="Body",
                                target_view="MODEL_VIEW", parent=context)
    site = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcSite", name="Site")
    building = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuilding", name="Valley Roof")
    storey = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuildingStorey", name="Roof")
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=project, products=[site])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=site, products=[building])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=building, products=[storey])
    # Slope A rises with y, slope C with x: they meet over the plan line x = y.
    element(ifc, body, storey, "IfcRoof", "Roof A", None, shape=slab(
        ifc, body, [(0, 0), (3000, 3000), (3000, 6000), (0, 6000)], lambda x, y: 3000.0 + y * T))
    element(ifc, body, storey, "IfcRoof", "Roof C", None, shape=slab(
        ifc, body, [(0, 0), (6000, 0), (6000, 3000), (3000, 3000)], lambda x, y: 3000.0 + x * T))
    ifc.write(OUT)
    print("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
