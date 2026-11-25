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
settings groups happen to be built in; except that a face set out on the client's own columns
leads, whatever the order (a skirt picked before the Rockpanel under it goes on its battens). The lines reach this face as "matched posts", through the same door as the columns a
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


def _follow(p, elev, others, side, lines, spans):
    """Put *elev*'s uprights on *lines* (its own u), the lines of the faces *others* (the
    first the nearest), which cover *spans* of it."""
    lo, hi = clip_bounds(elev)
    W, bw = float(elev["width"]), p["batten_w"]
    mine = [u for u in sorted(set(round(x, 1) for x in lines)) if lo - 0.5 <= u <= hi + 0.5 and bw / 2.0 - 0.5 <= u <= W - bw / 2.0 + 0.5]
    if not mine:
        return
    # past the ends of the faces it follows, this face's own set-out carries on
    centres = p["batten_centres"]
    own = [u for u in batten_positions(W, bw, centres, float(elev.get("offset") or 0.0), edges=False)
           if all(u < a - centres / 2.0 or u > b + centres / 2.0 for a, b in spans)]
    other = others[0]
    name = other.get("name") or "the other chain"
    elev["matched_posts"] = ([{"u": round(u, 1), "default": "on", "name": "%s batten" % name, "matched": True} for u in mine]
                             + [{"u": round(u, 1), "default": "on", "name": "set out past %s" % name, "matched": False} for u in own])
    elev["matched_to"] = {"name": other.get("name"), "chain": other.get("chain"), "side": side, "count": len(mine)}
    if len(others) > 1:
        elev["matched_to"]["also"] = [o.get("name") for o in others[1:]]


def _own_columns(p, elev):
    """Does the face set its battens out on columns of the client's model in use (not
    toggled off: cladding_geometry.post_state)?"""
    import math
    if not p.get("battens_from_model", True):
        return False
    states = elev.get("post_states") or {}
    for q in elev.get("posts") or []:
        st = states.get(str(int(math.floor(float(q["u"]) + 0.5))), q.get("default", "on"))
        if st != "off":
            return True
    return False


def plan_matches(groups, names, build):
    """Before anything is built: give each face that has one the lines of the face of
    another chain directly above or below it (module docstring), as elev["matched_posts"]
    and elev["matched_to"] {"name", "chain", "side", "count"}.

    *groups*: [(p, faces, layout)], each settings group parsed and made ready to build;
    *names*: every face's name in the order they were picked, which is who follows whom: a
    face follows the nearest one picked before it, so the chain already there never moves;
    faces on the client's own columns come first all the same (_own_columns).
    *build*: cladding_geometry.build_elevation. A face that is followed is built here once,
    for its lines alone (and with the lines it follows itself, so three chains stacked
    share one set); the groups are built as ever afterwards, in their own order, which is
    why the lines cannot simply be read off as each group is built."""
    place = {n: k for k, n in enumerate(names)}
    # A face set out on the client's own columns leads, whatever was picked first: they are
    # the battens the designer placed (the job's Rockpanel), and a skirt picked before it over
    # them goes on their lines, not they on its.
    faces = sorted(((0 if _own_columns(p, e) else 1, place.get(e.get("name"), len(names)), k, p, e, layout.get(e.get("name")))
                    for k, (p, live, layout) in enumerate(groups) for e in live), key=lambda f: f[:3])
    faces = [f[1:] for f in faces]
    leads, lines = {}, {}
    for k, (_at, _g, p, elev, _layout) in enumerate(faces):
        elev.pop("matched_posts", None)
        elev.pop("matched_to", None)
        if not p.get("match_battens", True):
            continue
        found_all = []
        for j in range(k):
            other = faces[j][3]
            if other.get("chain") and other.get("chain") == elev.get("chain"):
                continue
            found = _stacked(elev, other)
            if found:
                found_all.append((j, found))
        if found_all:
            # the nearest in height, and every other face at that height on the same side: a
            # skirt over two wings of a wall follows both
            gap, side = min(f[1][0] for f in found_all), None
            near = [f for f in found_all if f[1][0] <= gap + MATCH_LAP]
            side = min(near, key=lambda f: f[1][0])[1][1]
            leads[k] = [f for f in near if f[1][1] == side]

    def lines_of(k):
        if k not in lines:
            _at, _g, p, elev, layout = faces[k]
            settle(k)
            lines[k] = upright_lines(build(p, elev, layout)[0])
        return lines[k]

    def settle(k):
        if k in leads and "matched_to" not in faces[k][3]:
            got, spans = [], []
            for j, (_gap, side, here) in leads[k]:
                got += [here(u) for u in lines_of(j)]
                lo, hi = clip_bounds(faces[j][3])
                spans.append(tuple(sorted((here(lo), here(hi)))))
            _follow(faces[k][2], faces[k][3], [faces[j][3] for j, _f in leads[k]], leads[k][0][1][1], got, spans)
    for k in sorted(leads):
        settle(k)
