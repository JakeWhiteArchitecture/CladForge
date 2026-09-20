"""Synthetic IFC-style geometry for tests: a wall face at an angle with a window
opening, a balcony slab crossing the face, and a pipe penetration. Coordinates
are IFC mm, Z-up, like the payload the browser sends."""

import math

ANGLE = math.radians(30.0)       # wall runs at 30 degrees to the X axis
U = (math.cos(ANGLE), math.sin(ANGLE), 0.0)      # along the wall
N = (math.sin(ANGLE), -math.cos(ANGLE), 0.0)     # outward normal (u = z x n)
ORIGIN = (10000.0, 5000.0, 100.0)               # bottom-left corner of the face


def world(u, v, d=0.0):
    return (ORIGIN[0] + U[0] * u + N[0] * d, ORIGIN[1] + U[1] * u + N[1] * d, ORIGIN[2] + v)


def rect_tris(u0, v0, u1, v1, d=0.0):
    a, b, c, e = world(u0, v0, d), world(u1, v0, d), world(u1, v1, d), world(u0, v1, d)
    return [[a, b, c], [a, c, e]]


def box_tris(u0, u1, v0, v1, d0, d1):
    """Closed box in wall-local coords (u along, v up, d outward)."""
    tris = []
    tris += rect_tris(u0, v0, u1, v1, d0) + rect_tris(u0, v0, u1, v1, d1)
    for u in (u0, u1):
        p = [world(u, v0, d0), world(u, v1, d0), world(u, v1, d1), world(u, v0, d1)]
        tris += [[p[0], p[1], p[2]], [p[0], p[2], p[3]]]
    for v in (v0, v1):
        p = [world(u0, v, d0), world(u1, v, d0), world(u1, v, d1), world(u0, v, d1)]
        tris += [[p[0], p[1], p[2]], [p[0], p[2], p[3]]]
    return tris


def wall_face(width=8000.0, height=3000.0, window=(2000.0, 900.0, 3200.0, 2100.0)):
    """Outer face triangles with a rectangular window hole (4 rects around it)."""
    wu0, wv0, wu1, wv1 = window
    tris = []
    tris += rect_tris(0, 0, width, wv0)
    tris += rect_tris(0, wv1, width, height)
    tris += rect_tris(0, wv0, wu0, wv1)
    tris += rect_tris(wu1, wv0, width, wv1)
    # duplicate inverted twin of one triangle (double-sided mesh noise)
    a, b, c = tris[0]
    tris.append([a, c, b])
    return [[list(p) for p in t] for t in tris]


def payload(name="Elevation A"):
    slab = box_tris(4500.0, 8000.0, 2000.0, 2200.0, -300.0, 1500.0)     # balcony slab through face
    roof = box_tris(-500.0, 1500.0, 1400.0, 1600.0, -100.0, 2500.0)     # lower flat roof abutting left
    pipe = box_tris(6000.0, 6100.0, 300.0, 400.0, -200.0, 200.0)        # pipe penetration
    floor = box_tris(0.0, 8000.0, 1450.0, 1700.0, -900.0, -300.0)       # internal slab, does not reach face
    return {
        "name": name,
        "faces": wall_face(),
        "outward": list(N),
        "context": [
            {"type": "IfcSlab", "name": "Balcony", "tris": [[list(p) for p in t] for t in slab]},
            {"type": "IfcRoof", "name": "Lower roof", "tris": [[list(p) for p in t] for t in roof]},
            {"type": "IfcPipeSegment", "name": "SVP", "tris": [[list(p) for p in t] for t in pipe]},
            {"type": "IfcSlab", "name": "First floor", "tris": [[list(p) for p in t] for t in floor]},
        ],
        "options": {"penetrations": True},
    }
