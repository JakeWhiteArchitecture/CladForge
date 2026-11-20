"""Build tests/eaves_oversail.ifc: an eaves modelled in 3D. A wall 6 m long and 2.7 m high to
its plate, rafters on it pitched 30 degrees, and 18 mm plywood sheathing on the rafters that
oversails the wall's outer face by 400 mm on plan. The wall's face stops 200 mm below where
the top of the plywood crosses its plane: the two nearly meet, but the plywood runs on over
the wall, so only the wall can be healed up to it (through the plywood). Used to test
healing at an eaves.
Run: python tests/make_eaves.py"""

import math
import os

import ifcopenshell
import ifcopenshell.api

from make_sample import element, box, sloped

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eaves_oversail.ifc")
T = math.tan(math.radians(30.0))
OVER, RUN = 400.0, 3000.0           # the oversail, and how far the roof runs back over the building, on plan
PLY = 18.0 / math.cos(math.radians(30.0))      # the plywood's thickness, plumb
RAFTER = 150.0 / math.cos(math.radians(30.0))
TOP = lambda y: 2900.0 + y * T      # noqa: E731 — the top of the plywood over the plan line y (the wall's face is y = 0)


def main():
    ifc = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcProject", name="Eaves")
    ifcopenshell.api.run("unit.assign_unit", ifc, length={"is_metric": True, "raw": "MILLIMETERS"})
    context = ifcopenshell.api.run("context.add_context", ifc, context_type="Model")
    body = ifcopenshell.api.run("context.add_context", ifc, context_type="Model", context_identifier="Body",
                                target_view="MODEL_VIEW", parent=context)
    site = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcSite", name="Site")
    building = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuilding", name="Eaves")
    storey = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuildingStorey", name="Ground Floor")
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=project, products=[site])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=site, products=[building])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=building, products=[storey])
    element(ifc, body, storey, "IfcWall", "Wall", (0, 0, 0, 6000, 300, 2700))
    element(ifc, body, storey, "IfcRoof", "Plywood sheathing", None,
            shape=sloped(ifc, body, 0, 6000, -OVER, TOP(-OVER), RUN, TOP(RUN), PLY))
    for i, x in enumerate(range(300, 6000, 600)):
        element(ifc, body, storey, "IfcMember", "Rafter %d" % (i + 1), None,
                shape=sloped(ifc, body, x - 25, x + 25, -OVER, TOP(-OVER) - PLY, RUN, TOP(RUN) - PLY, RAFTER))
    ifc.write(OUT)
    print("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
