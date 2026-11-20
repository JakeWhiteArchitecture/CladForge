"""Build tests/hip_short_roof.ifc: the hipped corner of make_hip.py with each roof slab
modelled 150 mm short of the hip (on plan), as roofs often are: the two slopes nearly meet
but neither has an edge on the hip line, so they are not joined until they are healed. Used
by the browser smoke test.
Run: python tests/make_hip_short.py"""

import math
import os

import ifcopenshell
import ifcopenshell.api

from make_sample import element
from make_valley import slab

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hip_short_roof.ifc")
T = math.tan(math.radians(30.0))
SHORT = 150.0


def main():
    ifc = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcProject", name="Hip Roof, short")
    ifcopenshell.api.run("unit.assign_unit", ifc, length={"is_metric": True, "raw": "MILLIMETERS"})
    context = ifcopenshell.api.run("context.add_context", ifc, context_type="Model")
    body = ifcopenshell.api.run("context.add_context", ifc, context_type="Model", context_identifier="Body",
                                target_view="MODEL_VIEW", parent=context)
    site = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcSite", name="Site")
    building = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuilding", name="Hip Roof")
    storey = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuildingStorey", name="Roof")
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=project, products=[site])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=site, products=[building])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=building, products=[storey])
    g = SHORT
    element(ifc, body, storey, "IfcRoof", "Roof A", None, shape=slab(
        ifc, body, [(g, 0), (6000, 0), (6000, 3000), (3000 + g, 3000)], lambda x, y: 3000.0 + y * T))
    element(ifc, body, storey, "IfcRoof", "Roof C", None, shape=slab(
        ifc, body, [(0, g), (3000, 3000 + g), (3000, 6000), (0, 6000)], lambda x, y: 3000.0 + x * T))
    ifc.write(OUT)
    print("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
