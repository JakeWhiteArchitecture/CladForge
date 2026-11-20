"""Build tests/head_mono.ifc: the head of a monopitch roof over a wall, as VMZINC's "Head
verge G3" draws it. A wall 6 m long and 2.7 m high, and a roof slab pitched 15 degrees whose
top surface starts over the wall's outer face and falls away behind it. The wall stands
under the slab, so its face stops a slab's thickness short of the roof's top edge: the two
nearly meet and are healed, and then the roof's top edge is the head of the wall. The roof
runs on over the wall's cladding under a cap that turns down over it.
Run: python tests/make_head.py"""

import math
import os

import ifcopenshell
import ifcopenshell.api

from make_sample import element, sloped

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "head_mono.ifc")
T = math.tan(math.radians(15.0))
RUN = 3000.0                        # how far the roof runs back over the building, on plan
SLAB = 200.0 / math.cos(math.radians(15.0))      # the roof slab's thickness, plumb
TOP = lambda y: 2700.0 - y * T      # noqa: E731 — the roof's top over the plan line y (the wall's face is y = 0): it falls away behind


def main():
    ifc = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcProject", name="Monopitch head")
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
    # the wall stands under the roof slab: its top follows the slab's underside, so its outer face stops
    # a slab's thickness short of the roof's top edge (the slab's own end carries on up, in the same plane)
    under = lambda y: TOP(y) - SLAB      # noqa: E731
    element(ifc, body, storey, "IfcWall", "Wall", None, shape=sloped(ifc, body, 0, 6000, 0, under(0), 300, under(300), under(0)))
    element(ifc, body, storey, "IfcRoof", "Roof", None, shape=sloped(ifc, body, 0, 6000, 0, TOP(0), RUN, TOP(RUN), SLAB))
    ifc.write(OUT)
    print("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
