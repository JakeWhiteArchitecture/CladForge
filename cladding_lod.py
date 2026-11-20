"""CladForge — level of development: the same job shown, and exported, at LOD 100, 200 or 300.

  * **LOD 100**: the cladding only signified, but **exact at its extremities**. One plain
    mass per face, tinted by what it is clad in (panels, planks, standing seam): the
    cladding's own outer face, edges, corners and opening edges, with no joints, battens,
    layers, closers, corner profiles, seams or junction metalwork. (masses)
  * **LOD 200**: approximate quantity, size, shape and location. The individual boards,
    panels and trays, the battens and the layers behind them, each where it will be, but
    plain: a standing seam is one plain upstand with no folds, a profiled plank is its
    plain board, and there are no closers, gaskets, corner profiles, caps, strips, gutters,
    drips or other junction metalwork.
  * **LOD 300**: everything the tool builds, exactly.

Every mesh the engine builds is tagged with the lowest level it appears at ("lod": 200 or
300; tag), so the app and the exporters choose with one rule (at_lod). The cutting list,
the checks and the setting-out always come from the full build, whatever is shown."""

import shapely
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from cladding_booleans import ROBUST_GRID

from cladding_constants import _parse, _prism
from cladding_primitives import ring_at

LODS = (100, 200, 300)
MASS_T = 2.0            # mm – the sheet on a face nothing is built on yet: enough to be a solid in the IFC
MASS_COLORS = {"panel": "#6b8fa3", "plank": "#c08552", "seam": "#9aa7b1"}
MASS_LABELS = {"panel": "panel cladding", "plank": "plank cladding", "seam": "standing seam"}
# The parts that are a board, a batten or a layer: there at LOD 200.
PLAIN = ("sheathing", "insulation", "counter_batten", "batten", "cross_batten", "panel", "plank", "deck", "reveal")


def _same_frame(a, b):
    return all(abs(x - y) < 1e-6 for k in ("origin", "u", "n") for x, y in zip(a[k], b[k]))


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


def _sheet(e, kind):
    """A face nothing is built on yet: a flat sheet on the picked surface itself."""
    out = []
    for k, poly in enumerate(e["polygons"]):
        ring = poly.get("exterior") or []
        if len(ring) >= 3:
            out.append((ring, [], 0.0, MASS_T, None))
    return out


def _skin(e, pieces, gap):
    """The face's cladding as one mass: the outline its boards, panels or trays make
    together at their outer faces, the joints between them closed, from the back of the
    cladding to its outer face. Its edges, its corners (it carries the boards' own corner
    mitre, cladding_primitives.corner_ring) and the edges of its openings are the
    cladding's own, exactly."""
    back = min(float(m["depth"]) for m in pieces)
    far = max(float(m["depth"]) + float(m["thickness"]) for m in pieces)
    shapes = []
    for m in pieces:
        flat = dict(m, corner=None)                           # the corner's shear goes on the mass, by position
        t = float(m["depth"]) + float(m["thickness"])
        try:
            g = Polygon(ring_at(flat, 0, t), [ring_at(flat, k + 1, t) for k in range(len(m.get("holes") or []))])
            shapes.append(g if g.is_valid else g.buffer(0))
        except Exception:   # noqa: BLE001 — a degenerate outline adds nothing to the mass
            continue
    shapes = [g for g in shapes if not g.is_empty and g.geom_type in ("Polygon", "MultiPolygon")]
    if not shapes:
        return []
    # On a 0.01 mm grid, which always nodes: boards cut to a hip or a valley nearly meet, and
    # a floating-point union of those can fail in GEOS, which in the browser is not an
    # error but the end of the engine (cladding_booleans, fabric_extract._union).
    whole = shapely.union_all(shapes, grid_size=ROBUST_GRID)
    if whole.is_empty:
        return []
    close = gap / 2.0 + 1.0                                   # across a joint, and back: the outer edges stay put
    whole = whole.buffer(close, join_style=2, mitre_limit=5.0).buffer(-close, join_style=2, mitre_limit=5.0)
    corner = {}
    for m in pieces:                                          # each end's boards carry that end's mitre: the mass has both
        corner.update(m.get("corner") or {})
    out = []
    for g in getattr(whole, "geoms", [whole]):
        if g.is_empty or g.geom_type != "Polygon" or g.area < 1.0:
            continue
        g = orient(g.simplify(0.01), 1.0)
        tidy = lambda ring: [[round(x, 3), round(y, 3)] for x, y in list(ring.coords)[:-1]]   # noqa: E731
        out.append((tidy(g.exterior), [tidy(h) for h in g.interiors if Polygon(h).area > 1.0], back, far - back, corner))
    return out


def masses(geometry, groups):
    """LOD 100: one plain mass per face, the cladding only signified but **exact at its
    extremities** (_skin). No battens, layers, closers, corner profiles, seams or junction
    metalwork. A face with nothing built on it yet gets a flat sheet on the picked surface.
    *groups*: [(params for the group, its elevation records)], as
    cladding_preview.setting_groups gives them."""
    out = []
    for sub, elevs in groups:
        p = _parse(dict(sub, elevations=[]))
        kind = clad_kind(p)
        gap = max(float(p.get("panel_gap") or 0.0), float(p.get("plank_gap") or 0.0), 0.0)
        for e in elevs:
            if not e.get("ok", True) or not e.get("polygons"):
                continue
            name = e.get("name", "Elevation")
            pieces = [m for m in geometry if m.get("elevation") == name and m.get("ifc_type") in ("panel", "plank")
                      and _same_frame(m["frame"], e["frame"])]
            parts = (_skin(e, pieces, gap) if pieces else []) or _sheet(e, kind)
            for k, (ring, holes, depth, thickness, corner) in enumerate(parts):
                title = "%s Cladding zone%s" % (name, " %d" % (k + 1) if len(parts) > 1 else "")
                m = _prism(ring, depth, thickness, e["frame"], "mass", title, name, holes)
                m.update(color=MASS_COLORS[kind], opacity=1.0, lod=100, mass_kind=kind, mass_label=MASS_LABELS[kind])
                if corner:
                    m["corner"] = corner
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
