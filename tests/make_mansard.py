"""Build tests/mansard.ifc: a mansard, as VMZINC's "Mansard" (VMZ-SN-MA-KN-b1) draws it. A
lower roof slab 6 m long pitched 70 degrees, rising 3 m, and an upper slab pitched 15 degrees
whose top surface starts on the lower's top edge: the flatter slope runs on over the steeper
one at the knuckle. Run: python tests/make_mansard.py"""

import math
import os

import ifcopenshell
import ifcopenshell.api

from make_sample import element, sloped

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mansard.ifc")
LOWER, UPPER = 70.0, 15.0           # the two pitches
H = 3000.0                          # the knuckle, up from the lower slope's foot
RUN = H / math.tan(math.radians(LOWER))       # how far the lower slope leans in, on plan
BACK = 3000.0                       # how far the upper slope runs back from the knuckle, on plan


def main():
    ifc = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcProject", name="Mansard")
    ifcopenshell.api.run("unit.assign_unit", ifc, length={"is_metric": True, "raw": "MILLIMETERS"})
    ctx = ifcopenshell.api.run("context.add_context", ifc, context_type="Model")
    body = ifcopenshell.api.run("context.add_context", ifc, context_type="Model", context_identifier="Body",
                                target_view="MODEL_VIEW", parent=ctx)
    site = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcSite", name="Site")
    building = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuilding", name="Building")
    storey = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuildingStorey", name="Ground Floor")
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=project, products=[site])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=site, products=[building])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=building, products=[storey])
    # the lower slope: its top surface from the foot (y = -RUN, z = 0) up to the knuckle (y = 0, z = H)
    element(ifc, body, storey, "IfcRoof", "Lower", None,
            shape=sloped(ifc, body, 0, 6000, -RUN, 0.0, 0.0, H, 200.0 / math.cos(math.radians(LOWER))))
    # the upper slope: from the knuckle back and up
    element(ifc, body, storey, "IfcRoof", "Upper", None,
            shape=sloped(ifc, body, 0, 6000, 0.0, H, BACK, H + BACK * math.tan(math.radians(UPPER)), 200.0 / math.cos(math.radians(UPPER))))
    ifc.write(OUT)
    print("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
