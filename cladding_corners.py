"""CladForge — the outer corner profile (a Rockpanel Profile D type), and the square
timber corner trim that plays its part for plank cladding.

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

With planks the Profile corner is an external corner profile of the Marley type
instead: in plan a hollow square nose TRIM_SIZE (31) each way with 1 mm walls at the
outer corner, and two flanges FLANGE_P (34) long and 1 mm thick, one each side, set
TRIM_LEG (26) back from the nose's outer faces so they lie flush with the backs of the
planks, on the batten face; the nose's back is the flanges' line, so nothing of it lies
behind the batten faces. Each face's planks butt the side of the nose, TRIM_BACK (5)
plus the chain's profile gap short of where the two batten faces meet, and the nose
stands TRIM_LEG - plank thickness proud of them. Placed at chain corners, and at the
jambs and heads of windows and doors (the nose's outer corner then TRIM_LEG out from the
face board's back and from the reveal lining's back).
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

TRIM_NAME = "External corner profile (Marley type)"
TRIM_SIZE = 31.0            # mm, the nose each way
TRIM_LEG = 26.0             # mm, from the nose's outer face to the front of its flange
TRIM_WALL = 1.0             # mm, nose walls and flanges
FLANGE_P = 34.0             # mm, each flange, from the side of the nose
TRIM_BACK = TRIM_SIZE - TRIM_LEG    # how far the side of the nose stands past the batten faces' corner


def offered(k, panel):
    """Is the profile offered at a corner of slope k = tan(turn / 2)? External right-angled
    corners only: never at a re-entrant one."""
    return k is not None and k > 0 and abs(k - 1.0) < RIGHT_ANGLE_K


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


def trim_section():
    """The Marley-type profile in plan, in the panel profile's (a, b): a runs along the
    face before the corner towards it (the nose's outer corner at a = 0), b inward from
    the nose's outer face on that side. The nose is TRIM_SIZE across each face, hollow,
    but its back is the flanges' line: flange A runs along the face before at
    b = TRIM_LEG, flange B along the face after at a = -TRIM_LEG, each on from the side
    of the nose by FLANGE_P and in to meet the other, so nothing of it lies behind the
    batten faces and the battens run right up to it."""
    S, L, w, F = TRIM_SIZE, TRIM_LEG, TRIM_WALL, FLANGE_P
    nose = box(-S, 0.0, 0.0, S).difference(box(-S - 1.0, L + w, -(L + w), S + 1.0))     # no corner behind the flanges
    hollow = box(-S + w, w, -w, S - w).difference(box(-S - 1.0, L, -L, S + 1.0))
    flange_a = box(-(S + F), L, -L, L + w)                  # behind the planks before the corner
    flange_b = box(-(L + w), L, -L, S + F)                  # behind the ones after it
    shape = orient(unary_union([nose, flange_a, flange_b]).difference(hollow), 1.0)
    return list(shape.exterior.coords)[:-1], [list(h.coords)[:-1] for h in shape.interiors]


def _mapped(D, inward, trim=False):
    """The section with b turned to the frame's v (*inward* = +1 where v points into the
    building, -1 where it points out), rings re-oriented."""
    ext, holes = trim_section() if trim else section(D)
    pg = orient(Polygon([(a, inward * b) for a, b in ext], [[(a, inward * b) for a, b in h] for h in holes]), 1.0)
    return ([[round(a, 3), round(b, 3)] for a, b in list(pg.exterior.coords)[:-1]],
            [[[round(a, 3), round(b, 3)] for a, b in list(h.coords)[:-1]] for h in pg.interiors])


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def profile_mesh(origin, u_sec, n, length, D, inward_hint, name, elevation, trim=False):
    """The profile prism: section in the plane of u_sec and v = n × u_sec, extruded along n
    by *length* from *origin* (the outer corner line where it starts). *inward_hint* is
    the direction into the building from the face before the corner. *trim*: the timber
    corner trim's section instead (planks)."""
    v = _cross(n, u_sec)
    inward = 1.0 if sum(a * b for a, b in zip(v, inward_hint)) > 0 else -1.0
    profile, holes = _mapped(D, inward, trim)
    m = _prism(profile, 0.0, length, {"origin": origin, "u": list(u_sec), "v": v, "n": list(n)},
               "corner_trim" if trim else "corner_profile", name, elevation, holes=holes)
    if trim:
        m["trim_info"] = {"name": TRIM_NAME, "size": TRIM_SIZE, "set_back": TRIM_LEG, "flange": FLANGE_P,
                          "thickness": TRIM_WALL, "length": round(length, 1)}
    else:
        m["profile_info"] = {"name": PROFILE_NAME, "D": D, "flange_a": FLANGE_A, "flange_b": FLANGE_B,
                             "thickness": WALL, "length": round(length, 1)}
    return m


def vertical_profile(frame, u_corner, toward, v_from, v_to, face, D, name, elevation, trim=False):
    """A profile (or, *trim*, the timber trim) standing at a vertical corner of an
    elevation: its outer corner line at u_corner and depth *face*, the face before the
    corner running towards it in the direction *toward* (+1 along u, -1 against)."""
    if v_to - v_from < 1.0:
        return None
    U, N = frame["u"], frame["n"]
    return profile_mesh(frame_to_world(frame, u_corner, v_from, face), [toward * U[0], toward * U[1], 0.0],
                        [0.0, 0.0, 1.0], v_to - v_from, D, [-N[0], -N[1], 0.0], name, elevation, trim)


def head_profile(frame, u_from, u_to, v_corner, face, D, name, elevation, trim=False):
    """A profile lying along a head: the outer corner line at v_corner and depth *face*,
    the face board above running down to it, extruded along the wall. *trim*: the
    Marley-type plank profile."""
    if u_to - u_from < 1.0:
        return None
    U, N = frame["u"], frame["n"]
    return profile_mesh(frame_to_world(frame, u_from, v_corner, face), [0.0, 0.0, -1.0],
                        [U[0], U[1], 0.0], u_to - u_from, D, [-N[0], -N[1], 0.0], name, elevation, trim)


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
