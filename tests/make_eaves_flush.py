"""Build tests/eaves_flush.ifc: an eaves with no overhang, as detail DL-41 draws it. A wall
6 m long and 2.7 m high, and a roof slab pitched 30 degrees whose top surface comes down to
the wall's outer face at the wall's head: the two picked faces (the wall's outer face, the
roof's top) meet on one level line, with nothing sailing over. The roof's plywood and trays
run on past the wall's cladding and drip over it; the wall's cladding stops under them.
Run: python tests/make_eaves_flush.py"""

import math
import os

import ifcopenshell
import ifcopenshell.api

from make_sample import element, sloped

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eaves_flush.ifc")
T = math.tan(math.radians(30.0))
RUN = 3000.0                        # how far the roof runs back over the building, on plan
SLAB = 200.0 / math.cos(math.radians(30.0))      # the roof slab's thickness, plumb
TOP = lambda y: 2700.0 + y * T      # noqa: E731 — the roof's top over the plan line y (the wall's face is y = 0)


def main():
    ifc = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcProject", name="Eaves, no overhang")
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
    element(ifc, body, storey, "IfcWall", "Wall", (0, 0, 0, 6000, 300, 2700))
    element(ifc, body, storey, "IfcRoof", "Roof", None, shape=sloped(ifc, body, 0, 6000, 0, TOP(0), RUN, TOP(RUN), SLAB))
    ifc.write(OUT)
    print("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
