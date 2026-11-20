"""Build tests/overrun_corner.ifc: two walls at an external corner, drawn as walls often are.
Wall A runs 600 mm on **past** the corner (a fin: further than the wall's thickness, which
would be a corner as it stands), and wall B stops against A's back, so its outer face is A's
thickness short of the corner. A's face runs through the line the two meet on and B's stops
short of it. Used by the browser smoke test for trimming an overshoot (fabric_extract.heal_link,
"trim").
Run: python tests/make_overrun.py"""

import os

import ifcopenshell
import ifcopenshell.api

from make_sample import element

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "overrun_corner.ifc")
THICK, H, OVER = 300.0, 3000.0, 600.0


def main():
    ifc = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcProject", name="Overrun Corner")
    ifcopenshell.api.run("unit.assign_unit", ifc, length={"is_metric": True, "raw": "MILLIMETERS"})
    context = ifcopenshell.api.run("context.add_context", ifc, context_type="Model")
    body = ifcopenshell.api.run("context.add_context", ifc, context_type="Model", context_identifier="Body",
                                target_view="MODEL_VIEW", parent=context)
    site = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcSite", name="Site")
    building = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuilding", name="Overrun Corner")
    storey = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuildingStorey", name="Ground Floor")
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=project, products=[site])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=site, products=[building])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=building, products=[storey])
    # A: its outer face on y = 0, looking south, from x = 0 to 600 past the corner at x = 4000
    element(ifc, body, storey, "IfcWall", "Wall A", (0.0, 0.0, 0.0, 4000.0 + OVER, THICK, H))
    # B: its outer face on x = 4000, looking east, from the back of A northwards
    element(ifc, body, storey, "IfcWall", "Wall B", (4000.0 - THICK, THICK, 0.0, 4000.0, THICK + 3000.0, H))
    ifc.write(OUT)
    print("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
