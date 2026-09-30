"""CladForge — the outer corner profile (a Rockpanel Profile D type).

An aluminium profile for external corners of panel cladding. In plan section it is a
hollow square nose at the outer corner, D × D with 1.1 mm walls and a small outer
radius, whose outside faces are flush with the two panel faces, and two flanges lying
in the plane of the back of the panels, on the batten face: flange A behind the panel
before the corner in the run, flange B behind the one after it, both 35 mm. D is the
panel thickness, and each panel stops D plus the chain's profile gap (1 mm by default)
short of the outer corner line.

The profile is a prism in the general frame (cladding_constants.frame_to_world): its
section lies in plan and it is extruded along world Z over the clad height of the
corner — or, under a head, along the wall. Placed at right-angled external corners
only: chain corners, and the arris of a window or door jamb or head, with flange A on
the elevation face.
"""

import math

from shapely.geometry import Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from cladding_constants import _prism, frame_to_world

PROFILE_NAME = "Outer corner profile (Rockpanel Profile D type)"
WALL = 1.1                  # mm, aluminium
FLANGE_A, FLANGE_B = 35.0, 35.0      # both legs the long one
OUTER_RADIUS = 1.0          # mm, the nose's outer arris
PANEL_SIZES = (6.0, 8.0, 10.0)
RIGHT_ANGLE_K = 0.05        # |k - 1| within this is a right-angled external corner


def offered(k, panel):
    """Is the profile offered at a corner of slope k = tan(turn / 2)? Panels only, and
    external right-angled corners only: never at a re-entrant one."""
    return bool(panel) and k is not None and k > 0 and abs(k - 1.0) < RIGHT_ANGLE_K


def section(D):
    """The profile in plan as (exterior, holes) in (a, b): a runs along the face before
    the corner towards it (the outer corner at a = 0, that face's panels at a <= -D), b
    runs inward from that face's outer plane (the face after the corner lies along
    a = 0, its panels at b >= D)."""
    r, w = min(OUTER_RADIUS, D / 3.0), WALL
    arc = [(-r + r * math.cos(t), r - r * math.sin(t))
           for t in [math.radians(90 - 90 * i / 6) for i in range(7)]]   # (-r, 0) round to (0, r)
    nose = Polygon([(-D, 0.0)] + arc + [(0.0, D), (-D, D)], [[(-D + w, w), (-w, w), (-w, D - w), (-D + w, D - w)]])
    flange_a = box(-(D + FLANGE_A), D, -D, D + w)             # behind the first panel
    flange_b = box(-(D + w), D, -D, D + FLANGE_B)             # behind the second
    joint = box(-(D + w), D - w / 2, -D + w / 2, D + w)       # where both leave the nose
    shape = orient(unary_union([nose, flange_a, flange_b, joint]), 1.0)
    return list(shape.exterior.coords)[:-1], [list(h.coords)[:-1] for h in shape.interiors]


def _mapped(D, inward):
    """The section with b turned to the frame's v (*inward* = +1 where v points into the
    building, -1 where it points out), rings re-oriented."""
    ext, holes = section(D)
    pg = orient(Polygon([(a, inward * b) for a, b in ext], [[(a, inward * b) for a, b in h] for h in holes]), 1.0)
    return ([[round(a, 3), round(b, 3)] for a, b in list(pg.exterior.coords)[:-1]],
            [[[round(a, 3), round(b, 3)] for a, b in list(h.coords)[:-1]] for h in pg.interiors])


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def profile_mesh(origin, u_sec, n, length, D, inward_hint, name, elevation):
    """The profile prism: section in the plane of u_sec and v = n × u_sec, extruded along n
    by *length* from *origin* (the outer corner line where it starts). *inward_hint* is
    the direction into the building from the face before the corner."""
    v = _cross(n, u_sec)
    inward = 1.0 if sum(a * b for a, b in zip(v, inward_hint)) > 0 else -1.0
    profile, holes = _mapped(D, inward)
    m = _prism(profile, 0.0, length, {"origin": origin, "u": list(u_sec), "v": v, "n": list(n)},
               "corner_profile", name, elevation, holes=holes)
    m["profile_info"] = {"name": PROFILE_NAME, "D": D, "flange_a": FLANGE_A, "flange_b": FLANGE_B,
                         "thickness": WALL, "length": round(length, 1)}
    return m


def vertical_profile(frame, u_corner, toward, v_from, v_to, face, D, name, elevation):
    """A profile standing at a vertical corner of an elevation: the outer corner line at
    u_corner on the cladding face, the face before the corner running towards it in the
    direction *toward* (+1 along u, -1 against)."""
    if v_to - v_from < 1.0:
        return None
    U, N = frame["u"], frame["n"]
    return profile_mesh(frame_to_world(frame, u_corner, v_from, face), [toward * U[0], toward * U[1], 0.0],
                        [0.0, 0.0, 1.0], v_to - v_from, D, [-N[0], -N[1], 0.0], name, elevation)


def head_profile(frame, u_from, u_to, v_corner, face, D, name, elevation):
    """A profile lying along a head: the outer corner line at v_corner on the cladding face,
    the face board above running down to it, extruded along the wall."""
    if u_to - u_from < 1.0:
        return None
    U, N = frame["u"], frame["n"]
    return profile_mesh(frame_to_world(frame, u_from, v_corner, face), [0.0, 0.0, -1.0],
                        [U[0], U[1], 0.0], u_to - u_from, D, [-N[0], -N[1], 0.0], name, elevation)


def strip(u0, u1, v0, v1):
    """The nose as the flattened elevation sees it: a strip D wide."""
    return [[u0, v0], [u1, v0], [u1, v1], [u0, v1]]


def flange_supported(meshes, elevation, u_span, v_span, plane, tol=1.0):
    """Does a flange covering *u_span* × *v_span* of the face at the back-of-panel depth
    *plane* lie over a batten (or counter-batten, noggin or cavity closer) of *elevation*?
    Supports are judged at their outer face, where the flange sits, corner shears
    included."""
    from cladding_primitives import corner_ring
    for m in meshes:
        if m.get("elevation") != elevation or m.get("ifc_type") not in ("batten", "counter_batten", "cross_batten", "closer"):
            continue
        s = float(m["depth"]) + float(m["thickness"])
        if abs(s - plane) > tol:
            continue
        pts = corner_ring(m["profile"], m.get("corner"), s)
        us, vs = [q[0] for q in pts], [q[1] for q in pts]
        if min(u_span[1], max(us)) - max(u_span[0], min(us)) > tol and \
                min(v_span[1], max(vs)) - max(v_span[0], min(vs)) > tol:
            return True
    return False
