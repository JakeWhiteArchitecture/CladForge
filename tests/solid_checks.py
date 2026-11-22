"""Checks on the solids written to an IFC: that an element made of several pieces went out
as one closed solid (cladding_union), and how much it holds."""


def _loops(brep):
    for face in brep.Outer.CfsFaces:
        for bound in face.Bounds:
            pts = [tuple(float(c) for c in p.Coordinates) for p in bound.Bound.Polygon]
            yield pts if bound.Orientation else list(reversed(pts))


def _point_loops(brep):
    for face in brep.Outer.CfsFaces:
        for bound in face.Bounds:
            ids = [p.id() for p in bound.Bound.Polygon]
            yield ids if bound.Orientation else list(reversed(ids))


def is_closed(brep):
    """True if every edge of the brep's faces (between its point entities: where two pieces
    only touch along a line the union keeps two points there) is met once each way: a
    closed, consistently oriented shell."""
    edges = {}
    for pts in _point_loops(brep):
        for i in range(len(pts)):
            e = (pts[i], pts[(i + 1) % len(pts)])
            edges[e] = edges.get(e, 0) + 1
    return all(n == 1 and edges.get((b, a)) == 1 for (a, b), n in edges.items())


def volume(brep):
    """The brep's volume from its faces (each loop fanned from its first point)."""
    s = 0.0
    for pts in _loops(brep):
        o = pts[0]
        for i in range(1, len(pts) - 1):
            (ax, ay, az), (bx, by, bz), (cx, cy, cz) = o, pts[i], pts[i + 1]
            s += ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)
    return s / 6.0


def one_closed_solid(element):
    """The element's single closed brep: asserts it has one solid, faceted and closed."""
    items = [i for r in element.Representation.Representations for i in r.Items]
    assert len(items) == 1, (element.Name, len(items))
    (item,) = items
    assert item.is_a("IfcFacetedBrep"), (element.Name, item.is_a())
    assert is_closed(item), element.Name
    return item
