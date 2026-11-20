"""Build tests/hipped_roof.ifc: a whole hipped roof, 9 x 6 m on plan and pitched 30 degrees
all round. Two long slopes meet on a ridge 3 m long; a hip end closes each end of it, so
four hips run from the eaves corners up to the two ends of the ridge. Each slab is modelled
up to the lines it meets the others on. Used to look at how hips marry with a ridge.
Run: python tests/make_hipped.py"""

import math
import os

import ifcopenshell
import ifcopenshell.api

from make_sample import element
from make_valley import slab

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hipped_roof.ifc")
T = math.tan(math.radians(30.0))
L, W = 9000.0, 6000.0
H = W / 2.0             # the hips run in this far on plan
SLOPES = [
    ("Roof South", [(0, 0), (L, 0), (L - H, H), (H, H)], lambda x, y: y * T),
    ("Roof North", [(L, W), (0, W), (H, H), (L - H, H)], lambda x, y: (W - y) * T),
    ("Roof West", [(0, W), (0, 0), (H, H)], lambda x, y: x * T),
    ("Roof East", [(L, 0), (L, W), (L - H, H)], lambda x, y: (L - x) * T),
]


def main():
    ifc = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcProject", name="Hipped Roof")
    ifcopenshell.api.run("unit.assign_unit", ifc, length={"is_metric": True, "raw": "MILLIMETERS"})
    context = ifcopenshell.api.run("context.add_context", ifc, context_type="Model")
    body = ifcopenshell.api.run("context.add_context", ifc, context_type="Model", context_identifier="Body",
                                target_view="MODEL_VIEW", parent=context)
    site = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcSite", name="Site")
    building = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuilding", name="Hipped Roof")
    storey = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuildingStorey", name="Roof")
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=project, products=[site])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=site, products=[building])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=building, products=[storey])
    for name, plan, height in SLOPES:
        element(ifc, body, storey, "IfcRoof", name, None, shape=slab(ifc, body, plan, lambda x, y, h=height: 3000.0 + h(x, y)))
    ifc.write(OUT)
    print("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
