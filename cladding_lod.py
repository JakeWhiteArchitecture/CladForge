"""CladForge — level of development: the same job shown, and exported, at LOD 100, 200 or 300.

  * **LOD 100**: the cladding only signified. One flat coloured sheet on each picked face,
    tinted by what it is to be clad in (panels, planks, standing seam). Openings are not
    cut out of it. (masses)
  * **LOD 200**: approximate quantity, size, shape and location. The individual boards,
    panels and trays, the battens and the layers behind them, each where it will be, but
    plain: a standing seam is one plain upstand with no folds, a profiled plank is its
    plain board, and there are no closers, gaskets, corner profiles, caps, strips, gutters,
    drips or other junction metalwork.
  * **LOD 300**: everything the tool builds, exactly.

Every mesh the engine builds is tagged with the lowest level it appears at ("lod": 200 or
300; tag), so the app and the exporters choose with one rule (at_lod). The cutting list,
the checks and the setting-out always come from the full build, whatever is shown."""

from cladding_constants import _parse, _prism

LODS = (100, 200, 300)
MASS_T = 2.0            # mm – a sheet's thickness: enough to be a solid in the IFC and to stand clear of the wall's face
MASS_COLORS = {"panel": "#6b8fa3", "plank": "#c08552", "seam": "#9aa7b1"}
MASS_LABELS = {"panel": "panel cladding", "plank": "plank cladding", "seam": "standing seam"}
# The parts that are a board, a batten or a layer: there at LOD 200.
PLAIN = ("sheathing", "insulation", "counter_batten", "batten", "cross_batten", "panel", "plank", "deck", "reveal")


def lod_of(mesh):
    """The lowest level a built mesh appears at: 200 or 300."""
    kind = mesh.get("ifc_type")
    if kind in PLAIN:
        return 200
    if kind == "seam" and mesh.get("seam_part") == "over_up" and not (mesh.get("hip_part") or mesh.get("ridge_part")):
        return 200                                # a seam's upstand: at 200 it stands for the whole seam
    return 300


def tag(meshes):
    for m in meshes:
        m["lod"] = lod_of(m)
    return meshes


def clad_kind(p):
    return "seam" if p.get("seam") else ("plank" if p.get("cladding_type") == "plank" else "panel")


def masses(groups):
    """LOD 100's sheets: one per outline of each picked face. *groups*: [(params for the
    group, its elevation records)], as cladding_preview.setting_groups gives them."""
    out = []
    for sub, elevs in groups:
        kind = clad_kind(_parse(dict(sub, elevations=[])))
        for e in elevs:
            if not e.get("ok", True) or not e.get("polygons"):
                continue
            many = len(e["polygons"]) > 1
            for k, poly in enumerate(e["polygons"]):
                ring = poly.get("exterior") or []
                if len(ring) < 3:
                    continue
                name = "%s Cladding zone%s" % (e.get("name", "Elevation"), " %d" % (k + 1) if many else "")
                m = _prism(ring, 0.0, MASS_T, e["frame"], "mass", name, e.get("name", ""))
                m.update(color=MASS_COLORS[kind], opacity=0.9, lod=100, mass_kind=kind, mass_label=MASS_LABELS[kind])
                out.append(m)
    return out


def _plain(m):
    """A mesh as LOD 200 has it: a seam's upstand square-ended, a profiled plank its board."""
    if m.get("ifc_type") == "seam" and m.get("vshift"):
        return dict(m, vshift=None)
    if m.get("parts") or m.get("section_ring"):
        return dict(m, parts=None, section_ring=None)
    return m


def at_lod(geometry, sheets, lod):
    """The meshes to show or export at *lod*: LOD 100's sheets, LOD 200's plain parts, or
    everything. Anything but 100 or 200 is 300."""
    lod = int(lod or 300)
    if lod == 100:
        return list(sheets or [])
    if lod == 200:
        return [_plain(m) for m in geometry if m.get("lod", lod_of(m)) <= 200]
    return list(geometry)
