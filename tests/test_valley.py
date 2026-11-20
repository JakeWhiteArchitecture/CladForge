"""The valley: two roof slopes falling towards each other and meeting on a sloping line.
The cladding stands back so 200 mm is clear across the valley, the layers under it meet on
the valley's bisecting plane, and one gutter lies on both decks (VMZINC's valley detail)."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import ifcopenshell  # noqa: E402

from cladding_checks import check_rules  # noqa: E402
from cladding_constants import frame_to_world, world_to_frame  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from cladding_primitives import ring_at  # noqa: E402
from cladding_valley import VALLEY_CLEAR, VALLEY_LAP  # noqa: E402
from dxf_generator import meshes_to_dxf_string  # noqa: E402
from fabric_extract import extract_elevation, valley_link, valley_links, chain_link  # noqa: E402
from ifc_generator import meshes_to_ifc, own_elements  # noqa: E402

SEAM = dict(cladding_type="plank", plank_profile="seam", plank_orient="vertical")


def _face(name, plan, height, normal):
    """A flat roof face from its outline on plan [(x, y)] and its height z = height(x, y)."""
    pts = [[x, y, height(x, y)] for x, y in plan]
    tris = [[pts[0], pts[i], pts[i + 1]] for i in range(1, len(pts) - 1)]
    return extract_elevation({"name": name, "faces": tris, "outward": list(normal), "context": [],
                              "options": {"debug": False, "clad_depth": 150.0}})


def _pair(deg=30.0, deg_c=None):
    """Two slopes at right angles on plan, A rising with y and C rising with x, meeting on
    the valley over the plan line x·tan(c) = y·tan(a) (x = y when the pitches are equal)."""
    ta, tc = math.tan(math.radians(deg)), math.tan(math.radians(deg if deg_c is None else deg_c))
    r = ta / tc                                   # on the valley x = r·y
    a = _face("Roof A", [(0, 0), (3000 * r, 3000), (3000 * r, 6000), (0, 6000)], lambda x, y: y * ta,
              (0.0, -math.sin(math.atan(ta)), math.cos(math.atan(ta))))
    c = _face("Roof C", [(0, 0), (6000, 0), (6000, 3000), (3000 * r, 3000)], lambda x, y: x * tc,
              (-math.sin(math.atan(tc)), 0.0, math.cos(math.atan(tc))))
    return a, c


def _records(a, c, link):
    """The two records as the app sends them (app.js linkValleys): each its own chain."""
    return (dict(a, offset=0, verge="none", valleys=[dict(link["a"], **{"with": c["name"], "k": link["k"], "master": True})]),
            dict(c, offset=0, verge="none", valleys=[dict(link["b"], **{"with": a["name"], "k": link["k"], "master": False})]))


def _build(deg=30.0, deg_c=None, **kw):
    a, c = _pair(deg, deg_c)
    link = valley_link(a, c)
    a, c = _records(a, c, link)
    params = dict({"elevations": [a, c], "trim": True, "splash": 0}, **kw)
    return link, params, generate_preview(params)


def _world(mesh, depths=None):
    return [frame_to_world(mesh["frame"], u, v, s) for s in (depths or (mesh["depth"], mesh["depth"] + mesh["thickness"]))
            for u, v in ring_at(mesh, 0, s)]


# ── finding the valley ────────────────────────────────────────────────────

def test_two_slopes_falling_together_meet_at_a_valley():
    a, c = _pair(30.0)
    link = valley_link(a, c)
    assert link["kind"] == "valley"
    t = math.tan(math.radians(30.0))
    assert abs(link["length"] - 3000.0 * math.sqrt(2.0 + t * t)) < 1.0            # (0,0,0) to (3000, 3000, 3000·tan)
    for side, e in (("a", a), ("b", c)):
        ends = sorted(tuple(round(x) for x in frame_to_world(e["frame"], *link[side][k])) for k in ("p1", "p2"))
        assert ends == [(0, 0, 0), (3000, 3000, round(3000 * t))]
    # the angle between the two normals: cos = cos²(pitch) for slopes at right angles on plan
    assert abs(link["angle"] - math.degrees(math.acos(math.cos(math.radians(30.0)) ** 2))) < 0.1
    assert abs(link["k"] - math.tan(math.radians(link["angle"]) / 2.0)) < 1e-3
    # each side is told the way up the other slope, square to the valley
    up, n = link["a"]["other_up"], link["a"]["other_n"]
    assert abs(sum(x * y for x, y in zip(up, n))) < 1e-6 and up[0] > 0 and up[2] > 0
    assert valley_links(a, [c, a]) == [link, None]


def test_what_is_not_a_valley_is_not_joined():
    a, c = _pair(30.0)
    assert chain_link(a, c) is None                                    # nor a ridge
    t = math.tan(math.radians(30.0))
    # a hip: the same two planes, each face on the other side of the line (behind the other's plane)
    hip_a = _face("Hip A", [(0, 0), (3000, 0), (3000, 3000)], lambda x, y: y * t, (0.0, -0.5, math.cos(math.radians(30.0))))
    hip_c = _face("Hip C", [(0, 0), (3000, 3000), (0, 3000)], lambda x, y: x * t, (-0.5, 0.0, math.cos(math.radians(30.0))))
    assert valley_link(hip_a, hip_c) is None
    # one roof run through the other: no edge of A's outline lies on the line
    through = _face("Through", [(-2000, 0), (4000, 0), (4000, 6000), (-2000, 6000)], lambda x, y: y * t,
                    (0.0, -0.5, math.cos(math.radians(30.0))))
    assert valley_link(through, c) is None
    wall = extract_elevation({"name": "Wall", "faces": [[[0, 0, 0], [3000, 0, 0], [3000, 0, 2000]], [[0, 0, 0], [3000, 0, 2000], [0, 0, 2000]]],
                              "outward": [0, -1, 0], "context": [], "options": {"debug": False}})
    assert valley_link(a, wall) is None and valley_link(wall, a) is None


# ── the cladding stands back, the layers under it meet ────────────────────

@pytest.mark.parametrize("deg, deg_c", [(30.0, None), (20.0, 40.0)])
def test_200_is_left_clear_across_the_valley(deg, deg_c):
    link, params, out = _build(deg, deg_c, **SEAM)
    a, c = params["elevations"]
    p1 = frame_to_world(a["frame"], *link["a"]["p1"])
    p2 = frame_to_world(a["frame"], *link["a"]["p2"])
    line = [(p2[i] - p1[i]) / link["length"] for i in range(3)]

    def off_line(P):                     # square to the valley line
        d = [P[i] - p1[i] for i in range(3)]
        t = sum(d[i] * line[i] for i in range(3))
        return math.sqrt(max(0.0, sum((d[i] - t * line[i]) ** 2 for i in range(3))))
    back = VALLEY_CLEAR / 2.0 * math.sqrt(1.0 + link["k"] ** 2)
    for e in (a, c):
        trays = [m for m in out["geometry"] if m["elevation"] == e["name"] and m["ifc_type"] == "plank"]
        along = lambda P: sum((P[i] - p1[i]) * line[i] for i in range(3))       # noqa: E731
        near = min(off_line(P) for P in (frame_to_world(e["frame"], u, v, 0.0) for m in trays for u, v in m["profile"])
                   if 0.0 <= along(P) <= link["length"])                   # beside the valley, not past its ends
        assert abs(near - back) < 0.5, (e["name"], near, back)       # on the face, back from the line
    # and straight across, tray end to tray end at the same place along the valley: 200
    ends = {}
    for e in (a, c):
        pts = [frame_to_world(e["frame"], u, v, 0.0) for m in out["geometry"] if m["elevation"] == e["name"]
               and m["ifc_type"] == "plank" for u, v in m["profile"]]
        ends[e["name"]] = [P for P in pts if abs(off_line(P) - back) < 0.5]
    P = ends["Roof A"][0]
    tP = sum((P[i] - p1[i]) * line[i] for i in range(3))
    foot = [p1[i] + tP * line[i] for i in range(3)]
    Q = min(ends["Roof C"], key=lambda q: abs(sum((q[i] - p1[i]) * line[i] for i in range(3)) - tP))
    tQ = sum((Q[i] - p1[i]) * line[i] for i in range(3))
    Q = [Q[i] + (tP - tQ) * line[i] for i in range(3)]              # slid along the valley to stand opposite P
    assert abs(math.dist(P, Q) - VALLEY_CLEAR) < 1.0, (math.dist(P, Q), math.dist(P, foot))


def test_the_deck_and_battens_meet_on_the_valley_without_running_through():
    link, params, out = _build(30.0, **SEAM)
    a, c = params["elevations"]
    n = [x + y for x, y in zip(a["frame"]["n"], c["frame"]["n"])]
    p1 = frame_to_world(a["frame"], *link["a"]["p1"])
    p2 = frame_to_world(a["frame"], *link["a"]["p2"])
    line = [(p2[i] - p1[i]) / link["length"] for i in range(3)]
    across = [n[1] * line[2] - n[2] * line[1], n[2] * line[0] - n[0] * line[2], n[0] * line[1] - n[1] * line[0]]
    size = math.sqrt(sum(x * x for x in across))
    across = [x / size for x in across]                              # square to the bisecting plane
    side_a = sum((frame_to_world(a["frame"], *link["a"]["inside"])[i] - p1[i]) * across[i] for i in range(3))
    sign = 1.0 if side_a > 0 else -1.0
    seen = set()
    for m in out["geometry"]:
        if m["ifc_type"] not in ("deck", "batten"):
            continue
        s = sign if m["elevation"] == "Roof A" else -sign
        nearest = min(s * sum((P[i] - p1[i]) * across[i] for i in range(3)) for P in _world(m))
        assert nearest >= -0.05, (m["name"], nearest)                # nothing crosses the plane
        if m["ifc_type"] == "deck":
            seen.add(m["elevation"])
            assert nearest < 0.05                                    # and the deck comes right up to it
            for d in (m["depth"], m["depth"] + m["thickness"]):      # at its back and at its face
                assert abs(min(s * sum((P[i] - p1[i]) * across[i] for i in range(3)) for P in _world(m, (d,)))) < 0.05
    assert seen == {"Roof A", "Roof C"}


def test_a_seam_that_runs_out_at_the_valley_is_ended_there():
    _link, _params, out = _build(30.0, **SEAM)
    cut = [m for m in out["geometry"] if m.get("seam_part") == "over_cap" and m["elevation"] == "Roof A"]
    assert cut and any(m.get("vshift") for m in cut)                 # folded down, like any free end


# ── the gutter ────────────────────────────────────────────────────────────

def test_one_gutter_lies_on_both_decks_and_runs_on_under_the_trays():
    link, params, out = _build(30.0, **SEAM)
    (gutter,) = [m for m in out["geometry"] if m["ifc_type"] == "valley_gutter"]
    assert gutter["elevation"] == "Roof A" and abs(gutter["thickness"] - link["length"]) < 0.5
    back = VALLEY_CLEAR / 2.0 * math.sqrt(1.0 + link["k"] ** 2)
    assert abs(gutter["valley_info"]["width"] - 2.0 * (back + VALLEY_LAP)) < 0.5
    a, c = params["elevations"]
    for e in (a, c):
        deck = next(m for m in out["geometry"] if m["ifc_type"] == "deck" and m["elevation"] == e["name"])
        tray = next(m for m in out["geometry"] if m["ifc_type"] == "plank" and m["elevation"] == e["name"])
        local = [world_to_frame(e["frame"], frame_to_world(gutter["frame"], x, y, d)) for x, y in gutter["profile"] for d in (0.0, 500.0)]
        on = [q for q in local if 0 <= q[2] <= 200]                  # the part of it over this face
        depth = [q[2] for q in on if q[2] > 1.0]
        mine = [q for q in local if abs(q[2] - (deck["depth"] + deck["thickness"] + 1.0)) < 0.5]
        assert mine, e["name"]                                       # a leg lying on this deck, in the tray's stand-off
        assert all(q[2] <= tray["depth"] + 0.01 for q in mine)       # under the trays
    info = [v for i in out["info"] for v in i["valleys"]]
    assert [v["master"] for v in info] == [True, False] and all(v["lap"] == VALLEY_LAP and v["clear"] == VALLEY_CLEAR for v in info)


def test_the_valley_is_checked_against_14_degrees_and_the_cladding():
    _l, params, out = _build(30.0, **SEAM)
    (row,) = [c for c in check_rules(params, out["info"]) if c["name"].endswith("Valley")]
    assert row["status"] == "pass" and "Roof A and Roof C" in row["message"] and "200 mm clear" in row["message"]
    _l, params, out = _build(12.0, **SEAM)
    (row,) = [c for c in check_rules(params, out["info"]) if c["name"].endswith("Valley")]
    assert row["status"] == "warn" and "12.0°" in row["message"] and "over 14°" in row["message"]
    _l, params, out = _build(30.0, cladding_type="panel")
    (row,) = [c for c in check_rules(params, out["info"]) if c["name"].endswith("Valley")]
    assert row["status"] == "warn" and "panels" in row["message"]
    assert len([m for m in out["geometry"] if m["ifc_type"] == "valley_gutter"]) == 1


def test_the_valley_exports_to_ifc_and_dxf():
    _l, params, out = _build(30.0, **SEAM)
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    (gutter,) = [e for e in ifc.by_type("IfcCovering") if e.Name == "Roof A Valley Gutter"]
    assert gutter.PredefinedType == "ROOFING" and gutter.ObjectType == "Valley gutter"
    assert len([e for e in ifc.by_type("IfcElement") if not e.is_a("IfcElementAssembly")]) == len(own_elements(out["geometry"]))
    assert all(len(i["valley_strips"]) == 1 for i in out["info"])
    assert "ROOF A" in meshes_to_dxf_string(out["geometry"], params, out["info"])
