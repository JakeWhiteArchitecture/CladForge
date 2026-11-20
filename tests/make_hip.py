"""Build tests/hip_roof.ifc: a hipped roof corner. Two roof slabs at right angles on plan,
both pitched 30 degrees, each rising from its own eaves, falling away from each other and
meeting on a sloping hip. Each is modelled up to the hip line, as a roof is drawn. Used by
the browser smoke test.
Run: python tests/make_hip.py"""

import math
import os

import ifcopenshell
import ifcopenshell.api

from make_sample import element
from make_valley import slab

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hip_roof.ifc")
T = math.tan(math.radians(30.0))


def main():
    ifc = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcProject", name="Hip Roof")
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
    # Slope A's eaves run along y = 0 and it rises with y; slope C's run along x = 0 and it
    # rises with x: they meet over the plan line x = y, the hip, from the eaves corner up.
    element(ifc, body, storey, "IfcRoof", "Roof A", None, shape=slab(
        ifc, body, [(0, 0), (6000, 0), (6000, 3000), (3000, 3000)], lambda x, y: 3000.0 + y * T))
    element(ifc, body, storey, "IfcRoof", "Roof C", None, shape=slab(
        ifc, body, [(0, 0), (3000, 3000), (3000, 6000), (0, 6000)], lambda x, y: 3000.0 + x * T))
    ifc.write(OUT)
    print("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
