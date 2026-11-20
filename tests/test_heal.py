"""Healing near misses: two faces modelled a little short of the line their planes meet on
are not joined (no corner, ridge, hip or valley). heal_link finds such a pair and the strips
that would bring each to the line; added to the picked faces, the two are joined."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from cladding_preview import generate_preview  # noqa: E402
from fabric_extract import extract_elevation, heal_link, heal_links, hip_link, valley_link, chain_link, HEAL_MAX  # noqa: E402

T = math.tan(math.radians(30.0))
S, C = math.sin(math.radians(30.0)), math.cos(math.radians(30.0))


def _extract(name, tris, normal, heals=()):
    """*heals*: strips healed onto the face, as the app sends them."""
    return extract_elevation({"name": name, "faces": tris, "heals": list(heals), "outward": list(normal), "context": [],
                              "options": {"debug": False, "clad_depth": 150.0}})


def _fan(plan, height):
    pts = [[x, y, height(x, y)] for x, y in plan]
    return [[pts[0], pts[i], pts[i + 1]] for i in range(1, len(pts) - 1)]


def _strips(quads):
    return [t for q in quads for t in ([q[0], q[1], q[2]], [q[0], q[2], q[3]])]


def _hip(short, heal=None):
    """The hipped corner of test_hip, each slope *short* of the hip line on plan; *heal*, a
    heal_link result, adds its strips to the faces as the app does."""
    a = _fan([(short, 0), (6000, 0), (6000, 3000), (3000 + short, 3000)], lambda x, y: y * T)
    c = _fan([(0, short), (3000, 3000 + short), (3000, 6000), (0, 6000)], lambda x, y: x * T)
    return (_extract("Roof A", a, (0, -S, C), heal["a"] if heal else ()),
            _extract("Roof C", c, (-S, 0, C), heal["b"] if heal else ()))


def _wall(name, p0, p1, outward, extra=()):
    q = [[p0[0], p0[1], 0.0], [p1[0], p1[1], 0.0], [p1[0], p1[1], 3000.0], [p0[0], p0[1], 3000.0]]
    return _extract(name, [[q[0], q[1], q[2]], [q[0], q[2], q[3]]] + list(extra), outward)


def test_two_slopes_short_of_their_hip_can_be_healed():
    a, c = _hip(200.0)
    assert hip_link(a, c) is None                                  # as modelled, they do not meet
    heal = heal_link(a, c)
    assert heal["kind"] == "hip" and heal["length"] == pytest.approx(3000.0 * math.sqrt(2.0 + T * T), abs=1.0)
    assert heal["gap"] == pytest.approx(151.2, abs=0.5) and len(heal["a"]) == 1 and len(heal["b"]) == 1
    # each strip runs from the face's short edge to the hip line itself, its own eaves and top edge carried straight on
    ends = sorted(tuple(round(x) for x in p) for p in heal["a"][0])
    assert ends == [(0, 0, 0), (200, 0, 0), (3000, 3000, 1732), (3200, 3000, 1732)]
    healed_a, healed_c = _hip(200.0, heal)
    for face in (healed_a, healed_c):
        assert face["ok"] and (face["width"], face["height"]) == (6000.0, pytest.approx(3464.1, abs=0.1))
        assert len(face["polygons"]) == 1 and len(face["polygons"][0]["exterior"]) == 4       # one clean outline, no notch
    link = hip_link(healed_a, healed_c)
    assert link["kind"] == "hip" and link["length"] == heal["length"]
    assert heal_link(healed_a, healed_c) is None                   # nothing left to heal


@pytest.mark.parametrize("short, offered", [(0.0, False), (60.0, True), (400.0, True), (700.0, False)])
def test_only_a_near_miss_is_offered(short, offered):
    """Joined already, there is nothing to heal; too far apart (HEAL_MAX), it is not a near miss."""
    a, c = _hip(short)
    assert bool(heal_link(a, c)) is offered
    assert short * math.cos(math.radians(40.9)) < HEAL_MAX or not offered


def test_one_face_short_and_the_other_on_the_line():
    a = _extract("Roof A", _fan([(150, 0), (6000, 0), (6000, 3000), (3150, 3000)], lambda x, y: y * T), (0, -S, C))
    c = _extract("Roof C", _fan([(0, 0), (3000, 3000), (3000, 6000), (0, 6000)], lambda x, y: x * T), (-S, 0, C))
    heal = heal_link(a, c)
    assert heal["kind"] == "hip" and len(heal["a"]) == 1 and heal["b"] == []
    back = heal_link(c, a)                                          # asked the other way round, the same strip
    assert back["b"] == heal["a"] and back["a"] == []


def test_a_valley_and_a_wall_corner_heal_too():
    va = _extract("A", _fan([(0, 0), (3000 - 150, 3000), (3000 - 150, 6000), (0, 6000)], lambda x, y: y * T), (0, -S, C))
    vc = _extract("C", _fan([(0, 0), (6000, 0), (6000, 3000 - 150), (3000, 3000 - 150)], lambda x, y: x * T), (-S, 0, C))
    assert valley_link(va, vc) is None and heal_link(va, vc)["kind"] == "valley"
    # two walls 450 mm short of their corner each: further than a wall's thickness, which joins anyway
    a, b = _wall("A", (0, 0), (3550, 0), (0, -1, 0)), _wall("B", (4000, 450), (4000, 3000), (1, 0, 0))
    assert chain_link(a, b) is None
    heal = heal_link(a, b)
    assert heal["kind"] == "corner" and heal["gap"] == 450.0 and heal["length"] == 3000.0
    a2 = _wall("A", (0, 0), (3550, 0), (0, -1, 0), _strips(heal["a"]))
    b2 = _wall("B", (4000, 450), (4000, 3000), (1, 0, 0), _strips(heal["b"]))
    assert a2["width"] == 4000.0 and b2["width"] == 3000.0 and chain_link(a2, b2)["external"]


def test_a_leaning_wall_and_a_vertical_one_heal_to_a_raking_corner():
    """A leaning wall and an upright one, each short of the corner they would turn."""
    lean = math.radians(15.0)
    dy = 3000.0 * math.tan(lean)
    a = _extract("Lean", [[[0, 0, 0], [5800, 0, 0], [5800, dy, 3000]], [[0, 0, 0], [5800, dy, 3000], [0, dy, 3000]]],
                 (0, -math.cos(lean), math.sin(lean)))
    b = _extract("Vert", [[[6000, 200, 0], [6000, 4000, 0], [6000, 4000, 3000]], [[6000, 200, 0], [6000, 4000, 3000], [6000, dy + 200, 3000]]],
                 (1, 0, 0))
    heal = heal_link(a, b)
    assert heal["kind"] == "rake" and heal["gap"] == pytest.approx(200.0, abs=0.5)
    on_line = [p for q in heal["a"] + heal["b"] for p in q if abs(p[0] - 6000.0) < 0.01 and abs(p[1] - p[2] * math.tan(lean)) < 0.01]
    assert len(on_line) == 4                                        # both strips end on the one line


def test_faces_that_cross_or_run_parallel_or_stand_apart_are_left_alone():
    a = _wall("A", (0, 0), (4000, 0), (0, -1, 0))
    assert heal_link(a, _wall("P", (0, 300), (4000, 300), (0, -1, 0))) is None            # parallel
    assert heal_link(a, _wall("X", (2000, -1000), (2000, 1000), (1, 0, 0))) is None       # runs through it
    assert heal_link(a, _wall("F", (4000, 3000), (4000, 6000), (1, 0, 0))) is None        # 3 m off
    c = _hip(200.0)[1]
    assert [h and h["kind"] for h in heal_links(_hip(200.0)[0], [c, a])] == ["hip", None]


def _box(x0, x1, y0, y1, top, thick):
    """A sloping prism between two plan lines y0..y1 and x0..x1: its top at top(y), *thick* deep plumb."""
    quad = lambda q: [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]         # noqa: E731
    up = [[x0, y0, top(y0)], [x1, y0, top(y0)], [x1, y1, top(y1)], [x0, y1, top(y1)]]
    down = [[x, y, h - thick] for x, y, h in up]
    tris = quad(up) + quad(down[::-1])
    for i in range(4):
        j = (i + 1) % 4
        tris += quad([up[j], up[i], down[i], down[j]])
    return up, tris


def test_a_wall_healed_up_to_a_roof_stops_under_the_plywood_that_oversails_it():
    """An eaves in 3D: a wall to its plate, rafters on it, and pitched plywood sheathing on
    them that oversails the wall's face. The plywood runs on through the wall's plane, so
    it has nothing to heal; the wall is offered up to the roof's plane, and healed up under
    it: its cladding stops under the plywood (the chain's top offset below it, when it is
    built), it does not run up through it. The rafters cut it, each right through to the
    top: the cladding is notched round every rafter tail. And both are cut where they stand
    at the cladding's outside surface, 150 mm out from the wall, which on a 30 degree
    pitch is 87 mm lower than at the wall's face: every layer is cut back level to that."""
    quad = lambda q: [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]         # noqa: E731
    z = lambda y: 2900.0 + y * T                                      # noqa: E731 — the plywood's top; 2900 at the wall's face
    t_ply, t_raft = 18.0 / C, 150.0 / C
    top, ply = _box(0, 6000, -400, 3000, z, t_ply)
    rafters = [{"type": "IFCMEMBER", "name": "Rafter %d" % i, "id": 10 + i,
                "tris": _box(x - 25, x + 25, -400, 3000, lambda y: z(y) - t_ply, t_raft)[1]} for i, x in enumerate((900, 3000, 5100))]
    context = [{"type": "IFCROOF", "name": "Plywood sheathing", "id": 7, "tris": ply}] + rafters

    def wall(heals=(), faces=()):
        return extract_elevation({"name": "Wall", "faces": quad([[0, 0, 0], [6000, 0, 0], [6000, 0, 2700], [0, 0, 2700]]) + list(faces),
                                  "heals": list(heals), "outward": [0, -1, 0], "seeds": [[3000, 0, 1350]], "context": context,
                                  "options": {"debug": False, "clad_depth": 150.0, "penetrations": True}})
    roof = _extract("Roof", quad(top), (0, -S, C))
    plain = wall()
    heal = heal_link(plain, roof)
    assert heal["kind"] == "abutment" and heal["gap"] == 200.0 and heal["length"] == 6000.0
    assert len(heal["a"]) == 1 and heal["b"] == []                     # the wall alone is extended
    assert heal_link(roof, plain)["a"] == [] and heal_link(roof, plain)["b"] == heal["a"]
    out = -150.0                                                       # the cladding's outside surface, on plan
    soffit, foot = z(out) - t_ply, z(out) - t_ply - t_raft             # the plywood's underside there, and a rafter's
    healed = wall([{"quad": heal["a"][0], "through": [7], "under": True}])     # as the app sends it: by id, healed up under it
    assert healed["height"] == pytest.approx(soffit, abs=0.1)          # under the plywood, where it is lowest over the build-up
    assert sorted(healed["healed"][0]) == [[0.0, 0.0, 2900.0], [6000.0, 0.0, 2900.0]] and len(healed["healed"]) == 1
    assert heal_link(healed, roof) is None                             # it stands at the line: not offered again
    assert heal_link(dict(healed, healed=[]), roof) is not None        # (which is what says so: its outline alone is short of it)
    # a notch for each rafter, as wide as it, from its underside at the cladding's outside surface right through to the top
    (outline,) = healed["polygons"]
    notch_feet = sorted(q[0] for q in outline["exterior"] if abs(q[1] - foot) < 0.5)
    assert notch_feet == pytest.approx([875, 925, 2975, 3025, 5075, 5125], abs=0.5)
    area = 6000.0 * soffit - 3 * 50.0 * (soffit - foot)
    assert healed["area"] == pytest.approx(area, abs=2.0) and not outline["holes"]
    assert wall([{"quad": heal["a"][0], "through": ["Plywood sheathing"], "under": True}])["area"] == pytest.approx(area, abs=2.0)   # the same by name
    # built, the boards stop the top offset below the plywood, and every layer is cut to the same level top and the same notches
    params = dict({"elevations": [dict(healed, offset=0, edges={"top": 10.0})], "trim": True, "splash": 0}, cladding_type="panel")
    tops, feet = {}, {}
    for m in generate_preview(params)["geometry"]:
        if m["ifc_type"] in ("panel", "batten"):
            vs = [q[1] for q in m["profile"]]
            tops[m["ifc_type"]] = max(tops.get(m["ifc_type"], 0.0), max(vs))
            under = [q[1] for q in m["profile"] if 2974.0 < q[0] < 3026.0 and q[1] > 2000.0]      # under the middle rafter
            if under:
                feet[m["ifc_type"]] = min(min(under), feet.get(m["ifc_type"], 1e9))
    assert tops == {"panel": pytest.approx(soffit - 10.0, abs=0.2), "batten": pytest.approx(soffit - 10.0, abs=0.2)}
    assert feet["panel"] == pytest.approx(foot - 10.0, abs=0.2)
    # A strip not healed up under anything (two faces brought together side by side, or a state
    # saved before this) still passes through what it was healed to; the rafters cut it level all the same.
    through = wall([{"quad": heal["a"][0], "through": [7]}])
    assert through["height"] == 2900.0
    assert through["area"] == pytest.approx(6000.0 * 2900.0 - 3 * 50.0 * (2900.0 - foot), abs=2.0)
    # and with nothing said about what it was healed to, the plywood cuts it
    for cut in (wall([heal["a"][0]]), wall(faces=_strips(heal["a"]))):
        assert cut["height"] <= 2900.0 - t_ply + 0.1
