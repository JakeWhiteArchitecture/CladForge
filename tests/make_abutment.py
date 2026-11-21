"""Build tests/lean_to.ifc: a lean-to roof against a higher wall, as VMZINC's head-wall
abutments draw it. A wall 6 m long and 6 m high, and a roof slab pitched 15 degrees whose
top surface runs up to the wall's outer face 2.7 m up and falls away in front of it. The
roof butts the wall: its top edge lies on the wall's face, and the wall stands on above it.
Run: python tests/make_abutment.py"""

import math
import os

import ifcopenshell
import ifcopenshell.api

from make_sample import element, sloped

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lean_to.ifc")
T = math.tan(math.radians(15.0))
RUN = 3000.0                        # how far the roof runs out from the wall, on plan
SLAB = 200.0 / math.cos(math.radians(15.0))      # the roof slab's thickness, plumb
HEAD = 2700.0                       # the roof's top edge, up the wall
WALL = 6000.0                       # the wall's height


def main():
    ifc = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcProject", name="Lean-to")
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
    # the wall: its outer face is the plan line y = 0, and it is 300 thick behind that
    element(ifc, body, storey, "IfcWall", "Wall", None, shape=sloped(ifc, body, 0, 6000, 0, WALL, 300, WALL, WALL))
    # the roof: in front of the wall, its top surface up to the wall's face at HEAD
    element(ifc, body, storey, "IfcRoof", "Roof", None, shape=sloped(ifc, body, 0, 6000, -RUN, HEAD - RUN * T, 0, HEAD, SLAB))
    ifc.write(OUT)
    print("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
