"""
CladForge — IFC4X3 export via IfcOpenShell.

    meshes_to_ifc(meshes, params, infos=None) -> path to .ifc

One IfcElementAssembly per elevation, placed in the host wall's storey and
aggregating IfcMember battens, IfcPlate sheathing and IfcCovering insulation
and boards (panels as IfcWall instead with *panels_as_walls*: Archicad and others
take an IfcCovering in as a slab, while a wall is what a panel is edited as). Every prism becomes an IfcExtrudedAreaSolid whose placement axis
is the elevation normal, so the profile drawn in (u, v) extrudes outward.
A mitred end slopes with depth, so those elements are written as an explicit
brep instead. Raw entity creation is used for property sets: the WASM wheel ships
without pset template files.
"""

import math
import tempfile
import uuid

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.owner.settings
import ifcopenshell.guid

from cladding_constants import (_parse, chain_params, TOOL_NAME, TOOL_URL, IFC_SCHEMA_LABEL, SCOPE_NOTE,
                                QUANTITY_NOTE, DISCLAIMER, frame_to_world)
from cladding_primitives import corner_ring, ring_at

IFC_SCHEMA_VERSIONS = ("IFC4X3", "IFC4X3_ADD2", "IFC4")
# What params["ifc_schema"] can ask for, and what is tried for each, in order. IFC4X3 is the
# newest and the default; IFC4 is what most programs import (Archicad before 29 takes
# IFC2x3 and IFC4 only); IFC2X3 is for anything older still.
IFC_SCHEMA_CHOICES = {"IFC4X3": IFC_SCHEMA_VERSIONS, "IFC4": ("IFC4",), "IFC2X3": ("IFC2X3", "IFC4")}
# The model view each schema's header names: what an importer expects to be told.
_VIEW_DEFINITION = {"IFC2X3": "CoordinationView_V2.0", "IFC4": "ReferenceView_V1.2"}
_GUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, TOOL_URL)

# ifc_type: (IFC class, PredefinedType, ObjectType / material label)
_IFC_TYPE_MAP = {
    "batten":         ("IfcMember",   "USERDEFINED", "Batten"),
    "counter_batten": ("IfcMember",   "USERDEFINED", "Counter-batten"),
    "cross_batten":   ("IfcMember",   "USERDEFINED", "Cross batten"),
    "sheathing":      ("IfcPlate",    "SHEET",       "Sheathing board"),
    "insulation":     ("IfcCovering", "INSULATION",  "External insulation"),
    "panel":          ("IfcCovering", "CLADDING",    "Cladding panel"),
    "plank":          ("IfcCovering", "CLADDING",    "Cladding plank"),
    "closer":         ("IfcMember",   "USERDEFINED", "Cavity closer"),
    "reveal":         ("IfcCovering", "CLADDING",    "Reveal lining"),
    "corner_profile": ("IfcMember",   "USERDEFINED", "Corner profile"),
    "corner_trim":    ("IfcMember",   "USERDEFINED", "Plank corner profile"),
    "gasket":         ("IfcCovering", "MEMBRANE",    "EPDM gasket"),
    "deck":           ("IfcPlate",    "SHEET",       "Deck boarding"),
    "seam":           ("IfcCovering", "CLADDING",    "Standing seam"),
    "ridge_cap":      ("IfcCovering", "ROOFING",     "Ridge cap"),
    "ridge_filler":   ("IfcMember",   "USERDEFINED", "Vented ridge filler"),
    "verge_strip":    ("IfcCovering", "ROOFING",     "Verge strip"),
    "valley_gutter":  ("IfcCovering", "ROOFING",     "Valley gutter"),
}
_MATERIALS = {
    "batten": ("Timber (softwood) batten", "wood"), "counter_batten": ("Timber (softwood) batten", "wood"),
    "cross_batten": ("Timber (softwood) batten", "wood"), "sheathing": ("Sheathing board", "board"),
    "insulation": ("External insulation", "insulation"), "panel": ("Cladding panel", "cladding"),
    "plank": ("Cladding plank", "cladding"), "closer": ("Timber (solid) cavity closer", "wood"),
    "reveal": ("Reveal lining", "cladding"), "corner_profile": ("Aluminium corner profile", "aluminium"),
    "corner_trim": ("Aluminium plank corner profile", "aluminium"),
    "gasket": ("EPDM foam gasket", "rubber"),
    "deck": ("Timber deck (softwood boarding or WBP plywood)", "wood"),
    "seam": ("Standing seam sheet", "metal"),
    "ridge_cap": ("Ridge cap sheet", "metal"), "ridge_filler": ("Vented foam filler", "rubber"),
    "verge_strip": ("Verge strip sheet", "metal"), "valley_gutter": ("Valley gutter sheet", "metal"),
}


# ── GUIDs and header ─────────────────────────────────────────────────────

def _stable_guid(key):
    """Deterministic GlobalId: re-exporting the same design yields the same GUIDs."""
    return ifcopenshell.guid.compress(uuid.uuid5(_GUID_NAMESPACE, key).hex)


def _mesh_fingerprint(mesh):
    flat = ["%s|%s|%.1f|%.1f" % (mesh.get("ifc_type", ""), mesh.get("elevation", ""),
                                 mesh.get("depth", 0), mesh.get("thickness", 0))]
    for ring in [mesh.get("profile", [])] + list(mesh.get("holes") or []):
        flat.append(",".join("%.1f,%.1f" % (a, b) for a, b in ring))
    f = mesh.get("frame") or {}
    flat.append(",".join("%.2f" % c for c in list(f.get("origin", [])) + list(f.get("u", [])) + list(f.get("n", []))))
    return "|".join(flat)


def _header(ifc):
    """FILE_NAME / FILE_DESCRIPTION across ifcopenshell versions (WASM 0.8.2 vs 0.8.5+)."""
    hdr = getattr(ifc, "header", None)
    if hdr is None or not hasattr(hdr, "file_name"):
        hdr = ifc.wrapped_data.header
        if callable(hdr):
            hdr = hdr()
    return hdr


def _create_file(want=None):
    """A file in the schema asked for (IFC_SCHEMA_CHOICES; the newest by default), or the
    nearest this build carries. Ask before trying: a WASM wheel may not ship every schema,
    and requesting one it does not have raises a C++ exception that kills the Pyodide
    runtime outright rather than something Python can catch — so a try/except ladder
    cannot be the fallback."""
    versions = IFC_SCHEMA_CHOICES.get(str(want or "").upper().replace(".", "X"), IFC_SCHEMA_VERSIONS)
    try:
        import ifcopenshell.ifcopenshell_wrapper as _wrapper
        available = set(_wrapper.schema_names())
    except Exception:   # noqa: BLE001 — an old build without the introspection
        available = set()
    for version in tuple(versions) + ("IFC4",):
        if not available or version in available:
            return ifcopenshell.api.run("project.create_file", version=version)
    return ifcopenshell.api.run("project.create_file", version="IFC4")


def _schema_label(ifc):
    """What was actually written, which is not always the headline schema."""
    return getattr(ifc, "schema", None) or getattr(ifc, "schema_identifier", None) or IFC_SCHEMA_LABEL


# ── geometry ─────────────────────────────────────────────────────────────

def _polyline(ifc, ring):
    pts = [ifc.createIfcCartesianPoint((float(a), float(b))) for a, b in ring]
    pts.append(pts[0])
    return ifc.createIfcPolyline(pts)


def _prism_solid(ifc, mesh):
    """Solid for a prism. A square-ended one is an IfcExtrudedAreaSolid: the profile in
    the elevation frame, extruded along the wall normal. A mitred one is an explicit
    brep, because its end faces slope with depth; booleans against an infinite half
    space are not dependable enough to cut a joint people will build from."""
    corner = mesh.get("corner")
    if (corner and (corner.get("k_l") or corner.get("k_r"))) or mesh.get("vshift"):
        return _corner_brep(ifc, mesh), "Brep"      # end faces slope with depth
    outer = _polyline(ifc, corner_ring(mesh["profile"], corner, 0.0))
    holes = [_polyline(ifc, h) for h in (mesh.get("holes") or []) if len(h) >= 3]
    if holes:
        profile = ifc.createIfcArbitraryProfileDefWithVoids("AREA", None, outer, holes)
    else:
        profile = ifc.createIfcArbitraryClosedProfileDef("AREA", None, outer)
    frame = mesh["frame"]
    loc = frame_to_world(frame, 0.0, 0.0, float(mesh["depth"]))
    placement = ifc.createIfcAxis2Placement3D(
        ifc.createIfcCartesianPoint(tuple(float(c) for c in loc)),
        ifc.createIfcDirection(tuple(float(c) for c in frame["n"])),
        ifc.createIfcDirection(tuple(float(c) for c in frame["u"])))
    return ifc.createIfcExtrudedAreaSolid(profile, placement, ifc.createIfcDirection((0.0, 0.0, 1.0)),
                                          float(mesh["thickness"])), "SweptSolid"


def _body(ifc, mesh):
    """A mesh's solid: ([solids], representation type). A plank of a custom profile
    (cladding_profiles) is one solid too: _section_solid."""
    if mesh.get("section_ring") and mesh.get("parts"):
        try:
            return _section_solid(ifc, mesh)
        except Exception:   # noqa: BLE001 — a section that will not sweep keeps its slabs
            return [_corner_brep(ifc, dict(mesh, **part)) for part in mesh["parts"]], "Brep"
    solid, kind = _prism_solid(ifc, mesh)
    return [solid], kind


def _section_solid(ifc, mesh):
    """One solid for a plank of a custom profile: its section swept along the plank. A
    whole plank (its full section, square ends, no hole) is that sweep alone; one cut
    by an opening, a gable, the base or a mitre is the sweep intersected with the
    plank's own board (_prism_solid), a single IfcBooleanResult."""
    frame = mesh["frame"]
    horizontal = mesh.get("section_axis", "v") == "v"
    at = float(mesh["section_at"])
    ring = [(float(w), float(y)) for w, y in mesh["section_ring"]]
    width = max(w for w, _y in ring)
    corner = mesh.get("corner") or {}
    mitred = bool(corner.get("k_l") or corner.get("k_r") or mesh.get("vshift"))
    pts = corner_ring(mesh["profile"], corner, float(mesh["depth"]))
    run = [q[0] if horizontal else q[1] for q in pts]
    across = [q[1] if horizontal else q[0] for q in pts]
    r0, r1 = min(run), max(run)
    whole = (len(pts) == 4 and not mesh.get("holes") and not mitred
             and abs(min(across) - at) < 0.01 and abs(max(across) - (at + width)) < 0.01)
    if not whole:
        r0, r1 = r0 - 1.0, r1 + 1.0             # run past the board's ends, which then cut it
    U, N = frame["u"], frame["n"]
    V = frame.get("v") or [0.0, 0.0, 1.0]
    if horizontal:      # sweep along u from r0; the section's w up v, y out along n (u x v = n)
        loc, axis, xdir = frame_to_world(frame, r0, at, float(mesh["depth"])), U, V
    else:               # sweep down v from r1, so w along u and y still out along n
        loc, axis, xdir = frame_to_world(frame, at, r1, float(mesh["depth"])), [-c for c in V], U
    profile = ifc.createIfcArbitraryClosedProfileDef("AREA", None, _polyline(ifc, ring))
    placement = ifc.createIfcAxis2Placement3D(ifc.createIfcCartesianPoint(tuple(float(c) for c in loc)),
                                              ifc.createIfcDirection(tuple(float(c) for c in axis)),
                                              ifc.createIfcDirection(tuple(float(c) for c in xdir)))
    sweep = ifc.createIfcExtrudedAreaSolid(profile, placement, ifc.createIfcDirection((0.0, 0.0, 1.0)), r1 - r0)
    if whole:
        return [sweep], "SweptSolid"
    board, _kind = _prism_solid(ifc, mesh)
    return [ifc.createIfcBooleanResult("INTERSECTION", sweep, board)], "CSG"


def _point(ifc, cache, xyz):
    key = tuple(round(float(c), 4) for c in xyz)
    if key not in cache:
        cache[key] = ifc.createIfcCartesianPoint(key)
    return cache[key]


def _loop(points):
    """A ring without repeated neighbours: a mitre that steps at the end of an opening
    doubles a vertex, and at a depth where the step closes the two copies coincide."""
    out = [p for i, p in enumerate(points) if p is not points[i - 1]] if len(points) > 1 else list(points)
    return out


def _face(ifc, outer, inner=()):
    outer = _loop(outer)
    if len(outer) < 3:
        return None
    bounds = [ifc.createIfcFaceOuterBound(ifc.createIfcPolyLoop(outer), True)]
    bounds += [ifc.createIfcFaceBound(ifc.createIfcPolyLoop(r), True) for r in map(_loop, inner) if len(r) >= 3]
    return ifc.createIfcFace(bounds)


def _corner_brep(ifc, mesh):
    """Closed shell of a prism whose ends slope with depth. Profile rings run
    anticlockwise in (u, v) and u x v = the outward normal, so the outer ring as given
    faces outwards on the far cap and is reversed on the near one."""
    frame = mesh["frame"]
    near = float(mesh["depth"])
    far = near + float(mesh["thickness"])
    cache, faces, layers = {}, [], []
    rings = [list(mesh["profile"])] + [list(h) for h in (mesh.get("holes") or [])]
    for k, ring in enumerate(rings):
        if len(ring) < 3:
            continue
        lo = [_point(ifc, cache, frame_to_world(frame, u, v, near)) for u, v in ring_at(mesh, k, near)]
        hi = [_point(ifc, cache, frame_to_world(frame, u, v, far)) for u, v in ring_at(mesh, k, far)]
        layers.append((lo, hi))
        for i in range(len(lo)):                       # side faces, one quad per edge
            j = (i + 1) % len(lo)
            if lo[i] is not lo[j] or hi[i] is not hi[j]:
                faces.append(_face(ifc, [lo[i], lo[j], hi[j], hi[i]]))
    outer_lo, outer_hi = layers[0]
    faces.append(_face(ifc, list(reversed(outer_lo)), [list(reversed(r)) for r, _h in layers[1:]]))
    faces.append(_face(ifc, outer_hi, [h for _l, h in layers[1:]]))
    return ifc.createIfcFacetedBrep(ifc.createIfcClosedShell([f for f in faces if f is not None]))


def _make_element(ifc, body, ifc_class, name, solid, predefined=None, object_type=None):
    element = ifcopenshell.api.run("root.create_entity", ifc, ifc_class=ifc_class, name=name)
    solid, kind = solid
    rep = ifc.createIfcShapeRepresentation(body, "Body", kind, solid if isinstance(solid, list) else [solid])
    element.Representation = ifc.createIfcProductDefinitionShape(None, None, [rep])
    origin = ifc.createIfcCartesianPoint((0.0, 0.0, 0.0))
    element.ObjectPlacement = ifc.createIfcLocalPlacement(None, ifc.createIfcAxis2Placement3D(origin, None, None))
    for attr, value in (("PredefinedType", predefined), ("ObjectType", object_type)):
        if value:
            try:
                setattr(element, attr, value)
            except Exception:
                pass
    return element


# ── property sets ────────────────────────────────────────────────────────

def _owner(ifc):
    hist = ifc.by_type("IfcOwnerHistory")
    return hist[0] if hist else None


def _pset(ifc, products, name, props):
    """Attach a property set. props: {name: (IfcTypeName, value)}; None values skipped."""
    values = []
    for key, (typ, val) in props.items():
        if val is None:
            continue
        try:
            values.append(ifc.createIfcPropertySingleValue(key, None, ifc.create_entity(typ, val), None))
        except Exception:
            continue
    if not values:
        return
    pset = ifc.createIfcPropertySet(ifcopenshell.guid.new(), _owner(ifc), name, None, values)
    ifc.createIfcRelDefinesByProperties(ifcopenshell.guid.new(), _owner(ifc), None, None, products, pset)


def _setting_out_props(p, info, elev, schema=IFC_SCHEMA_LABEL):
    return {
        "SchemaLabel": ("IfcLabel", schema),
        "CladdingType": ("IfcLabel", p["cladding_type"]),
        "BoardOrientation": ("IfcLabel", p["boards_run"]),
        "BattenOrientation": ("IfcLabel", p["battens"]),
        "BattenSection": ("IfcLabel", "%.0f x %.0f" % (p["batten_w"], p["batten_d"])),
        "BattenCentres": ("IfcPositiveLengthMeasure", float(p["batten_centres"])),
        "CounterBattens": ("IfcBoolean", bool(p["has_cb"])),
        "CounterBattenSection": ("IfcLabel", "%.0f x %.0f" % (p["cb_w"], p["cb_d"])) if p["has_cb"] else ("IfcLabel", None),
        "CounterBattenCentres": ("IfcPositiveLengthMeasure", float(p["cb_centres"]) if p["has_cb"] else None),
        "SheathingThickness": ("IfcPositiveLengthMeasure", float(p["sheathing_t"]) if p["sheathing"] else None),
        "InsulationThickness": ("IfcPositiveLengthMeasure", float(p["insulation_t"]) if p["insulation"] else None),
        "SplashZone": ("IfcLengthMeasure", float(p["splash"])),
        "BaseLevel": ("IfcLengthMeasure", float(info.get("base_level", 0))),
        "CoverWidth": ("IfcPositiveLengthMeasure", float(info.get("cover", 0)) or None),
        "HorizontalOffset": ("IfcLengthMeasure", float(elev.get("offset", 0))),
        "Courses": ("IfcInteger", int(info.get("n_courses", 0))),
        "ClosingCutLeft": ("IfcLengthMeasure", float(info.get("closing_cut_left", 0))),
        "ClosingCutRight": ("IfcLengthMeasure", float(info.get("closing_cut_right", 0))),
        "ClosingCutTop": ("IfcLengthMeasure", float(info.get("closing_cut_top", 0))),
        "ElevationWidth": ("IfcPositiveLengthMeasure", float(elev.get("width", 0)) or None),
        "ElevationHeight": ("IfcPositiveLengthMeasure", float(elev.get("height", 0)) or None),
        "ScopeNote": ("IfcText", SCOPE_NOTE),
        "QuantityNote": ("IfcText", QUANTITY_NOTE),
    }


# ── spatial hierarchy ────────────────────────────────────────────────────

def _place(ifc, product, relative_to=None, z=0.0):
    """Give a product a local placement: at height *z* in its parent's (the world's with
    none). Some importers will not take a site, building, storey or assembly that has no
    placement of its own."""
    at = ifc.createIfcAxis2Placement3D(ifc.createIfcCartesianPoint((0.0, 0.0, float(z))), None, None)
    product.ObjectPlacement = ifc.createIfcLocalPlacement(getattr(relative_to, "ObjectPlacement", None), at)


def _spatial(ifc, context, elevations):
    """Project → Site → Building → Storeys, named after the host model."""
    project = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcProject",
                                   name=context.get("project") or "%s Cladding" % TOOL_NAME)
    try:
        project.LongName = "%s cladding setting-out (%s)" % (TOOL_NAME, _schema_label(ifc))
        project.Phase = "Preliminary design"
    except Exception:
        pass
    ifcopenshell.api.run("unit.assign_unit", ifc, length={"is_metric": True, "raw": "MILLIMETERS"})
    ctx = ifcopenshell.api.run("context.add_context", ifc, context_type="Model")
    body = ifcopenshell.api.run("context.add_context", ifc, context_type="Model",
                                context_identifier="Body", target_view="MODEL_VIEW", parent=ctx)
    site = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcSite",
                                name=context.get("site") or "Site")
    building = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuilding",
                                    name=context.get("building") or "Building")
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=project, products=[site])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=site, products=[building])
    _place(ifc, site)
    _place(ifc, building, site)
    storeys = {}
    for elev in elevations:
        st = elev.get("storey") or {}
        key = st.get("name") or "Ground Floor"
        if key not in storeys:
            storey = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuildingStorey", name=key)
            level = float(st.get("elevation", 0.0) or 0.0)
            try:
                storey.Elevation = level
            except Exception:
                pass
            _place(ifc, storey, building, level)
            ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=building, products=[storey])
            storeys[key] = storey
    return project, body, site, building, storeys


def _unshift(meshes, offset):
    """Put the elements back on the host model. The viewer works near the origin so
    float32 keeps its millimetres, and reports the shift it applied as context.offset."""
    if not offset or not any(offset):
        return meshes
    frames, out = {}, []
    for mesh in meshes:
        frame = mesh.get("frame")
        key = id(frame)
        if key not in frames:
            frames[key] = dict(frame, origin=[frame["origin"][i] + float(offset[i]) for i in range(3)])
        out.append(dict(mesh, frame=frames[key]))
    return out


def meshes_to_ifc(meshes, params, infos=None):
    """Convert prism meshes into an IFC file, in the schema params["ifc_schema"] asks for
    (IFC4X3 by default, IFC4 or IFC2X3). Returns the temp file path."""
    p = _parse(params)
    if infos is None:
        from cladding_preview import build_all_groups
        infos = build_all_groups(params)[2]
    info_by_name = {i["elevation"]: i for i in infos}
    context = p.get("context") or {}
    meshes = _unshift(meshes, context.get("offset"))
    ifc = _create_file(params.get("ifc_schema"))

    person = ifcopenshell.api.run("owner.add_person", ifc, family_name="User")
    org = ifcopenshell.api.run("owner.add_organisation", ifc, identification="JWA",
                               name="Jake White Architecture")
    ifcopenshell.api.run("owner.add_person_and_organisation", ifc, person=person, organisation=org)
    ifcopenshell.api.run("owner.add_application", ifc, application_developer=org, version="1.0",
                         application_full_name=TOOL_NAME, application_identifier=TOOL_NAME.lower())
    ifcopenshell.api.owner.settings.get_user = lambda f: f.by_type("IfcPersonAndOrganization")[0]
    ifcopenshell.api.owner.settings.get_application = lambda f: f.by_type("IfcApplication")[0]

    elevations = [e for e in p["elevations"] if e.get("ok", True) and e.get("polygons")]
    project, body, site, building, storeys = _spatial(ifc, context, elevations)

    by_elev = {}
    for m in meshes:
        by_elev.setdefault(m.get("elevation", ""), []).append(m)

    elements, fingerprints, assemblies, by_material = [], {}, [], {}
    for elev in elevations:
        name = elev.get("name", "Elevation")
        pe = _parse(chain_params(params, elev))      # this face's chain settings
        storey = storeys[(elev.get("storey") or {}).get("name") or "Ground Floor"]
        assembly = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcElementAssembly",
                                        name="%s cladding" % name)
        for attr, value in (("PredefinedType", "USERDEFINED"), ("ObjectType", "Cladding system"),
                            ("Description", "%s %s buildup on %s" % (TOOL_NAME, pe["cladding_type"], name))):
            try:
                setattr(assembly, attr, value)
            except Exception:
                pass
        ifcopenshell.api.run("spatial.assign_container", ifc, relating_structure=storey, products=[assembly])
        # At the world origin, like its parts, which carry absolute coordinates.
        assembly.ObjectPlacement = ifc.createIfcLocalPlacement(None, ifc.createIfcAxis2Placement3D(
            ifc.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None))
        parts = []
        for mesh in by_elev.get(name, []):
            ifc_class, predefined, label = _IFC_TYPE_MAP.get(mesh.get("ifc_type"), ("IfcBuildingElementProxy", None, None))
            if mesh.get("ifc_type") == "plank" and pe["seam"]:
                label = "Standing seam tray"
            elif mesh.get("ifc_type") == "plank" and pe.get("section"):
                label = "Cladding plank (%s)" % pe["section"]["name"]
            if mesh.get("ifc_type") == "panel" and pe["panels_as_walls"]:
                ifc_class, predefined = "IfcWall", "USERDEFINED"
            try:
                elem = _make_element(ifc, body, ifc_class, mesh.get("name") or label, _body(ifc, mesh),
                                     predefined, label)
            except Exception:
                continue
            parts.append(elem)
            fingerprints[elem.id()] = "%s|%s|%s" % (ifc_class, mesh.get("name", ""), _mesh_fingerprint(mesh))
            seam_tray = mesh.get("ifc_type") == "plank" and pe["seam"]
            by_material.setdefault((mesh.get("ifc_type"), seam_tray), []).append(elem)
            common = {"Reference": ("IfcIdentifier", label), "IsExternal": ("IfcBoolean", True)}
            if ifc_class == "IfcCovering":
                _pset(ifc, [elem], "Pset_CoveringCommon", common)
            elif ifc_class == "IfcWall":
                _pset(ifc, [elem], "Pset_WallCommon", dict(common, LoadBearing=("IfcBoolean", False)))
            elif ifc_class == "IfcMember":
                _pset(ifc, [elem], "Pset_MemberCommon", common)
            info = mesh.get("profile_info")
            if info:                            # the corner profile's own properties
                _pset(ifc, [elem], "%s_CornerProfile" % TOOL_NAME, {
                    "ProfileName": ("IfcLabel", info["name"]), "NoseSize": ("IfcPositiveLengthMeasure", info["D"]),
                    "FlangeA": ("IfcPositiveLengthMeasure", info["flange_a"]),
                    "FlangeB": ("IfcPositiveLengthMeasure", info["flange_b"]),
                    "Thickness": ("IfcPositiveLengthMeasure", info["thickness"]),
                    "Length": ("IfcPositiveLengthMeasure", info["length"])})
            elif mesh.get("trim_info"):         # the plank corner profile's (Marley type)
                t = mesh["trim_info"]
                _pset(ifc, [elem], "%s_PlankCornerProfile" % TOOL_NAME, {
                    "ProfileName": ("IfcLabel", t["name"]), "NoseSize": ("IfcPositiveLengthMeasure", t["size"]),
                    "FlangeSetBack": ("IfcPositiveLengthMeasure", t["set_back"]),
                    "Flange": ("IfcPositiveLengthMeasure", t["flange"]),
                    "Thickness": ("IfcPositiveLengthMeasure", t["thickness"]),
                    "Length": ("IfcPositiveLengthMeasure", t["length"])})
            elif ifc_class == "IfcPlate":
                _pset(ifc, [elem], "Pset_PlateCommon", common)
        if parts:
            ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=assembly, products=parts)
        _pset(ifc, [assembly], "%s_SettingOut" % TOOL_NAME,
              _setting_out_props(pe, info_by_name.get(name, {}), elev, _schema_label(ifc)))
        elements += parts
        assemblies.append((name, assembly))

    for (ifc_type, seam_tray), elems in by_material.items():
        mat_name, category = _MATERIALS.get(ifc_type, (ifc_type, None))
        if seam_tray:
            mat_name, category = "Standing seam sheet", "metal"
        try:
            material = ifc.create_entity("IfcMaterial", Name=mat_name)
            if hasattr(material, "Category"):                   # IFC2X3 materials have a name only
                material.Category = category
            ifc.create_entity("IfcRelAssociatesMaterial", GlobalId=ifcopenshell.guid.new(), OwnerHistory=_owner(ifc),
                              RelatedObjects=elems, RelatingMaterial=material)
        except Exception:
            pass

    _pset(ifc, [project], "%s_Disclaimer" % TOOL_NAME, {
        "Notice": ("IfcText", DISCLAIMER), "ScopeNote": ("IfcText", SCOPE_NOTE),
        "QuantityNote": ("IfcText", QUANTITY_NOTE), "Schema": ("IfcLabel", _schema_label(ifc)),
        "ToolURL": ("IfcText", TOOL_URL)})
    try:
        hdr = _header(ifc)
        hdr.file_name.authorization = "User must verify all outputs before use."
        hdr.file_name.name = "%s cladding.ifc" % (context.get("project") or TOOL_NAME)
        view = _VIEW_DEFINITION.get(str(_schema_label(ifc)).upper(), "ReferenceView")
        hdr.file_description.description = ("ViewDefinition [%s]" % view,
                                            "%s %s cladding setting-out. %s" % (TOOL_NAME, _schema_label(ifc), SCOPE_NOTE))
    except Exception:
        pass

    # Stable GlobalIds: elements from their own geometry, containers from the design.
    design_fp = _stable_guid("design|" + "|".join(sorted(fingerprints.values())))
    assigned = set()
    for elem in elements:
        elem.GlobalId = _stable_guid("element|" + fingerprints[elem.id()])
        assigned.add(elem.id())
    spatial = [("project", project), ("site", site), ("building", building)]
    spatial += [("storey|" + k, v) for k, v in storeys.items()]
    spatial += [("assembly|" + n, a) for n, a in assemblies]
    for label, ent in spatial:
        ent.GlobalId = _stable_guid("spatial|%s|%s" % (design_fp, label))
        assigned.add(ent.id())
    counters = {}
    for ent in ifc.by_type("IfcRoot"):
        if ent.id() in assigned:
            continue
        cls = ent.is_a()
        counters[cls] = counters.get(cls, 0) + 1
        ent.GlobalId = _stable_guid("aux|%s|%s|%d" % (design_fp, cls, counters[cls]))

    tmp = tempfile.NamedTemporaryFile(suffix=".ifc", delete=False)
    tmp.close()
    ifc.write(tmp.name)
    return tmp.name
