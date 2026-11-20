"""
CladForge — battens that follow the chain above or below.

Two chains can clad one wall, one over the other: panels over a plinth of boards, standing
seam over panels. Each sets its upright battens out across its own width, so left alone the
two sets land wherever their own centres put them and nothing lines up across the joint
between the chains. A face built later (a new chain) looks for a face of another chain
already built that stands **directly above or below it on the same wall**, and puts its
own uprights on that face's lines:

  - the same wall: the two planes parallel, and within MATCH_PLANE of each other (a set-back
    storey still lines up);
  - one above the other: sharing at least MATCH_OVERLAP of the wall's length, and not
    overlapping in height by more than MATCH_LAP (side by side is a corner or a butt joint,
    not a stack). The nearest in height wins.

Who follows whom is the order the faces were picked in (plan_matches), not the order the
settings groups happen to be built in. The lines reach this face as "matched posts", through the same door as the columns a
client's model has standing on the face (cladding_geometry._posts): the battens go on them,
panel joints and plank ends land on them, the offset slider has nothing to move, and each
can be set on, fixing only or off in the 2D view. Where this face runs on past the end of
the one it follows, its own set-out carries on from there. A client's columns on the face
come first; the chain's "match_battens" setting turns the matching off.

Which uprights: whichever layer stands upright in each build. The battens under horizontal
boards, panels and standing seam; the counter-battens under vertical boards.
"""

from cladding_constants import frame_to_world, frame_v
from cladding_primitives import batten_positions, clip_bounds, clip_bounds_v, dedupe

MATCH_PLANE = 500.0      # mm – faces whose planes are this close are one wall
MATCH_PARALLEL = 0.9995  # the least their normals agree by (about 2 degrees)
MATCH_OVERLAP = 300.0    # mm of the wall's length the two must share
MATCH_LAP = 50.0         # mm they may overlap in height and still be one over the other
UPRIGHTS = ("batten", "counter_batten")


def upright_lines(meshes):
    """The centreline u of every upright batten among a face's parts as built, untrimmed (a
    batten an opening will cut still marks its line above and below it)."""
    us = []
    for m in meshes:
        if m.get("ifc_type") not in UPRIGHTS or m.get("clip_v_at") or len(m.get("profile") or []) != 4:
            continue
        xs, ys = [q[0] for q in m["profile"]], [q[1] for q in m["profile"]]
        if max(ys) - min(ys) > max(xs) - min(xs):
            us.append((min(xs) + max(xs)) / 2.0)
    return dedupe(us, 1.0)


def _dot(a, b):
    return sum(float(x) * float(y) for x, y in zip(a, b))


def _stacked(elev, other):
    """None, or (how far apart in height, "above" | "below", a function taking a u of
    *other* to this face's u) where the face *other* stands directly over or under *elev*."""
    f, g = elev["frame"], other["frame"]
    O, U, V, N = f["origin"], f["u"], frame_v(f), f["n"]
    if _dot(N, g["n"]) < MATCH_PARALLEL or abs(_dot([a - b for a, b in zip(g["origin"], O)], N)) > MATCH_PLANE:
        return None

    def here(u, v):
        p = frame_to_world(g, u, v, 0.0)
        d = [a - b for a, b in zip(p, O)]
        return _dot(d, U), _dot(d, V)
    span, band = clip_bounds(other), clip_bounds_v(other)
    (ua, va), (ub, vb) = here(span[0], band[0]), here(span[1], band[1])
    (ua, ub), (va, vb) = sorted((ua, ub)), sorted((va, vb))
    lo, hi = clip_bounds(elev)
    v0, v1 = clip_bounds_v(elev)
    if min(hi, ub) - max(lo, ua) < MATCH_OVERLAP:
        return None
    lap = min(v1, vb) - max(v0, va)
    if lap > MATCH_LAP:
        return None
    mid = (band[0] + band[1]) / 2.0
    return -lap, ("above" if (va + vb) / 2.0 > (v0 + v1) / 2.0 else "below"), lambda u: here(u, mid)[0]


def _follow(p, elev, other, side, lines):
    """Put *elev*'s uprights on *lines* (its own u), the lines of the face *other*."""
    lo, hi = clip_bounds(elev)
    W, bw = float(elev["width"]), p["batten_w"]
    mine = [u for u in sorted(lines) if lo - 0.5 <= u <= hi + 0.5 and bw / 2.0 - 0.5 <= u <= W - bw / 2.0 + 0.5]
    if not mine:
        return
    # past the end of the face it follows, this face's own set-out carries on
    centres = p["batten_centres"]
    own = [u for u in batten_positions(W, bw, centres, float(elev.get("offset") or 0.0), edges=False)
           if u < mine[0] - centres / 2.0 or u > mine[-1] + centres / 2.0]
    name = other.get("name") or "the other chain"
    elev["matched_posts"] = ([{"u": round(u, 1), "default": "on", "name": "%s batten" % name, "matched": True} for u in mine]
                             + [{"u": round(u, 1), "default": "on", "name": "set out past %s" % name, "matched": False} for u in own])
    elev["matched_to"] = {"name": other.get("name"), "chain": other.get("chain"), "side": side, "count": len(mine)}


def plan_matches(groups, names, build):
    """Before anything is built: give each face that has one the lines of the face of
    another chain directly above or below it (module docstring), as elev["matched_posts"]
    and elev["matched_to"] {"name", "chain", "side", "count"}.

    *groups*: [(p, faces, layout)], each settings group parsed and made ready to build;
    *names*: every face's name in the order they were picked, which is who follows whom: a
    face follows the nearest one picked before it, so the chain already there never moves.
    *build*: cladding_geometry.build_elevation. A face that is followed is built here once,
    for its lines alone (and with the lines it follows itself, so three chains stacked
    share one set); the groups are built as ever afterwards, in their own order, which is
    why the lines cannot simply be read off as each group is built."""
    place = {n: k for k, n in enumerate(names)}
    faces = sorted(((place.get(e.get("name"), len(names)), k, p, e, layout.get(e.get("name")))
                    for k, (p, live, layout) in enumerate(groups) for e in live), key=lambda f: f[:2])
    leads, lines = {}, {}
    for k, (_at, _g, p, elev, _layout) in enumerate(faces):
        elev.pop("matched_posts", None)
        elev.pop("matched_to", None)
        if not p.get("match_battens", True):
            continue
        best = None
        for j in range(k):
            other = faces[j][3]
            if other.get("chain") and other.get("chain") == elev.get("chain"):
                continue
            found = _stacked(elev, other)
            if found and (best is None or found[0] < best[1][0]):
                best = (j, found)
        if best:
            leads[k] = best

    def lines_of(k):
        if k not in lines:
            _at, _g, p, elev, layout = faces[k]
            settle(k)
            lines[k] = upright_lines(build(p, elev, layout)[0])
        return lines[k]

    def settle(k):
        if k in leads and "matched_to" not in faces[k][3]:
            j, (_gap, side, here) = leads[k]
            _follow(faces[k][2], faces[k][3], faces[j][3], side, [here(u) for u in lines_of(j)])
    for k in sorted(leads):
        settle(k)
