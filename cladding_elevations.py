"""CladForge — building elevations: the whole model seen square from the north, east, south
and west, hidden lines removed, for the DXF (dxf_generator).

Every part is a prism (cladding_primitives.ring_at gives its rings at any depth); each of its
faces is projected onto the vertical plane square to the view, (across, height), and the
faces are taken nearest first. An edge is drawn where nothing nearer covers it, so what
stands behind the cladding, or behind a nearer slope, does not show. A face seen edge on
covers nothing; its edges are its neighbours'. Faces are covered a hair inside their own
outline, so the edges two faces share still show.

North is the project's: the model's +Y. The views read as seen: the north elevation is
looked at from the north, so east is on its left. The translucent layers (sheathing,
insulation) are left out: the cladding hides them."""

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from cladding_constants import frame_to_world
from cladding_primitives import ring_at

# view: (the across axis, as seen; the direction towards the viewer)
VIEWS = {"north": ((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0)), "east": ((0.0, 1.0, 0.0), (1.0, 0.0, 0.0)),
         "south": ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0)), "west": ((0.0, -1.0, 0.0), (-1.0, 0.0, 0.0))}
SEE_THROUGH = ("sheathing", "insulation", "mass")
BATCH = 150          # faces gathered before they are merged into what covers the rest


def _faces(m):
    d0, d1 = float(m["depth"]), float(m["depth"]) + float(m["thickness"])
    a = [frame_to_world(m["frame"], x, y, d0) for x, y in ring_at(m, 0, d0)]
    b = [frame_to_world(m["frame"], x, y, d1) for x, y in ring_at(m, 0, d1)]
    yield a
    yield b
    for i in range(len(a)):
        j = (i + 1) % len(a)
        yield [a[i], a[j], b[j], b[i]]


def elevation(meshes, view):
    """The model's visible edges from *view* ("north", "east", "south" or "west"):
    [(a list of (across, height) points, the part's ifc_type)], polylines in mm."""
    across, toward = VIEWS[view]
    dot = lambda p, q: p[0] * q[0] + p[1] * q[1] + p[2] * q[2]            # noqa: E731
    faces = []
    for m in meshes:
        if m.get("type", "prism") != "prism" or m.get("ifc_type") in SEE_THROUGH or not m.get("profile"):
            continue
        for f in _faces(m):
            pts = [(dot(q, across), q[2]) for q in f]
            g = Polygon(pts)
            if not g.is_valid:
                g = g.buffer(0)
            if g.is_empty or g.area < 0.5:
                continue                                 # seen edge on
            faces.append((-max(dot(q, toward) for q in f), g, LineString(pts + pts[:1]), m.get("ifc_type")))
    faces.sort(key=lambda t: t[0])                       # nearest first
    cover, batch, out = None, [], []
    for _near, g, edge, kind in faces:
        seen = edge if cover is None else edge.difference(cover)
        for o in batch:
            if seen.is_empty:
                break
            if o.intersects(seen):
                seen = seen.difference(o)
        for ln in getattr(seen, "geoms", [seen]):
            if ln.geom_type == "LineString" and ln.length > 0.5:
                out.append((list(ln.coords), kind))
        batch.append(g.buffer(-0.05) if g.area > 1.0 else g)
        if len(batch) >= BATCH:
            cover = unary_union(([cover] if cover is not None else []) + batch)
            batch = []
    return out
