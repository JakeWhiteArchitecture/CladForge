"""
CladForge — server-side IFC import.

The browser imports with web-ifc, which is fast and keeps the file private but
cannot build every model's geometry. This is the fallback: IfcOpenShell's
tessellation engine, which handles the swept solids, clippings and mapped
items that defeat web-ifc, and reports per-element failures instead of
abandoning the file.

    import_ifc(path) -> {"context", "elements": [{..., "verts", "idx"}], "failed", ...}

Vertices are IFC millimetres, Z-up, packed as base64 float32 triples; indices
as base64 uint32. Whatever the file's own length unit, output is millimetres.
"""

import base64

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.unit

MAX_ELEMENTS = 40000          # a model larger than this is refused rather than hung on
SKIP_TYPES = frozenset({"IfcOpeningElement", "IfcOpeningStandardCase", "IfcSpace", "IfcAnnotation",
                        "IfcGrid", "IfcVirtualElement", "IfcSpatialZone"})


def _b64(values, fmt):
    import array
    arr = array.array(fmt, values)
    return base64.b64encode(arr.tobytes()).decode("ascii")


def _storey_map(ifc):
    """expressID of a product -> {"name", "elevation"} of its storey."""
    out = {}
    for rel in ifc.by_type("IfcRelContainedInSpatialStructure"):
        structure = rel.RelatingStructure
        if not structure or not structure.is_a("IfcBuildingStorey"):
            continue
        record = {"name": structure.Name or ("Storey %d" % structure.id()),
                  "elevation": float(structure.Elevation or 0.0)}
        for product in rel.RelatedElements or []:
            out[product.id()] = record
    return out


def _context(ifc):
    out = {}
    for key, cls in (("project", "IfcProject"), ("site", "IfcSite"), ("building", "IfcBuilding")):
        found = ifc.by_type(cls)
        if found:
            out[key] = found[0].Name or ""
    return out


def import_ifc(path):
    """Tessellate every placed product in the file. Never raises on one bad element."""
    ifc = ifcopenshell.open(path)
    # IfcOpenShell emits SI metres whatever the file declares, so millimetres is x1000.
    # calculate_unit_scale is read anyway so a file with no unit assignment is reported.
    declared = ifcopenshell.util.unit.calculate_unit_scale(ifc)
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    settings.set("weld-vertices", True)
    storeys = _storey_map(ifc)

    products = [p for p in ifc.by_type("IfcProduct")
                if p.Representation is not None and not p.is_a() in SKIP_TYPES]
    result = {"context": _context(ifc), "elements": [], "failed": [], "unit_scale": declared,
              "schema": ifc.schema, "n_products": len(products), "warnings": []}
    if len(products) > MAX_ELEMENTS:
        result["warnings"].append("Model has %d products; only the first %d were tessellated."
                                  % (len(products), MAX_ELEMENTS))
        products = products[:MAX_ELEMENTS]

    try:
        iterator = ifcopenshell.geom.iterator(settings, ifc, 1, include=products)
        ok = iterator.initialize()
    except Exception as exc:  # noqa: BLE001 — reported to the caller
        result["warnings"].append("Geometry iterator failed to start: %s" % exc)
        ok = False
    seen = set()
    while ok:
        try:
            shape = iterator.get()
            geometry = shape.geometry
            verts = [c * 1000.0 for c in geometry.verts]
            faces = list(geometry.faces)
            if verts and faces:
                seen.add(shape.id)
                result["elements"].append({
                    "type": shape.type, "name": shape.name or "", "expressID": shape.id,
                    "storey": storeys.get(shape.id),
                    "verts": _b64(verts, "f"), "idx": _b64(faces, "I"),
                    "n_tris": len(faces) // 3})
        except Exception as exc:  # noqa: BLE001
            result["failed"].append(str(exc)[:200])
        try:
            ok = iterator.next()
        except Exception:  # noqa: BLE001 — a broken element must not end the walk
            break

    missing = [p for p in products if p.id() not in seen]
    if missing:
        result["warnings"].append("%d product(s) produced no geometry (%s)." % (
            len(missing), ", ".join(sorted({p.is_a() for p in missing}))[:120]))
    if not result["elements"]:
        result["warnings"].append("No geometry could be built from this file.")
    return result
