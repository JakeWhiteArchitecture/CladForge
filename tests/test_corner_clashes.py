"""Chain corners in the round: two real wall faces meeting at a corner, placed in a chain
as the app places them, and every part of one face tested against every part of the
other on plan. Nothing of one face may stand where something of the other does, and at an
external corner vertical planks carry on round it: no gap wider than a joint, no plank
wider than a plank."""

import collections
import itertools
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from shapely.geometry import Polygon  # noqa: E402
from fabric_extract import extract_elevation, chain_link  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from cladding_primitives import ring_at  # noqa: E402
from cladding_constants import frame_to_world  # noqa: E402

def wall(name, p0, p1, h, outward, hole=None):
    """A wall face from plan point p0 to p1, h high. hole: (a0, a1, z0, z1) along it."""
    def P(t, z): return [p0[0] + (p1[0]-p0[0])*t, p0[1] + (p1[1]-p0[1])*t, z]
    L = math.dist(p0, p1)
    rects = [(0, L, 0, h)]
    if hole:
        a0, a1, z0, z1 = hole
        rects = [(0, a0, 0, h), (a1, L, 0, h), (a0, a1, 0, z0), (a0, a1, z1, h)]
    tris = []
    for a, b, z0, z1 in rects:
        q = [P(a/L, z0), P(b/L, z0), P(b/L, z1), P(a/L, z1)]
        tris += [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]
    return extract_elevation({"name": name, "faces": tris, "outward": list(outward), "context": [], "options": {"debug": False, "clad_depth": 150.0}})

def chained(a, b, detail=None, master_first=True, **extra):
    """Records for a then b as app.js placeInChain makes them."""
    link = chain_link(a, b)
    assert link, "no corner"
    ra = dict(a, offset=0, chain="Chain 1", chain_start=0.0, chain_reversed=False, **extra)
    rb = dict(b, offset=0, chain="Chain 1", **extra)
    W, Wo = b["width"], a["width"]
    cornerC = Wo if link["end_a"] == "right" else 0.0
    after = cornerC >= Wo - 1
    if after: rb.update(chain_start=cornerC, chain_reversed=link["end_b"] == "right")
    else: rb.update(chain_start=cornerC - W, chain_reversed=link["end_b"] == "left")
    clamp = lambda u, w: max(0.0, min(w, u))
    if link["end_a"] == "right": ra["clip_hi"] = clamp(link["corner_u_a"], Wo)
    else: ra["clip_lo"] = clamp(link["corner_u_a"], Wo)
    if link["end_b"] == "right": rb["clip_hi"] = clamp(link["corner_u_b"], W)
    else: rb["clip_lo"] = clamp(link["corner_u_b"], W)
    k = link["k"]
    if after:
        ra.update(corner_hi=k, master_hi=master_first, detail_hi=detail); rb.update(corner_lo=k, master_lo=not master_first, detail_lo=detail)
    else:
        ra.update(corner_lo=k, master_lo=master_first, detail_lo=detail); rb.update(corner_hi=k, master_hi=not master_first, detail_hi=detail)
    lo = min(ra["chain_start"], rb["chain_start"])
    ra["chain_start"] -= lo; rb["chain_start"] -= lo
    return link, ra, rb

def plan(m):
    """The part's footprint on plan (walls are vertical) and its height range."""
    near, far = m["depth"], m["depth"] + m["thickness"]
    pts = []
    for d in (near, far):
        us = [q[0] for q in ring_at(m, 0, d)]
        vs = [q[1] for q in ring_at(m, 0, d)]
        pts.append((min(us), max(us), d, min(vs), max(vs)))
    (a0, a1, d0, z0, z1), (b0, b1, d1, _z0, _z1) = pts
    f = m["frame"]
    xy = [frame_to_world(f, u, 0, d)[:2] for u, d in ((a0, d0), (a1, d0), (b1, d1), (b0, d1))]
    zs = [frame_to_world(f, 0, v, 0)[2] for v in (z0, z1)]
    return Polygon(xy).buffer(0), min(zs), max(zs)

def clashes(out, a="A", b="B", tol=20.0):
    A = [(m, *plan(m)) for m in out["geometry"] if m["elevation"] == a and m["frame"].get("v", [0,0,1])[2] == 1 and abs(m["frame"]["n"][2]) < 1e-9]
    B = [(m, *plan(m)) for m in out["geometry"] if m["elevation"] == b and abs(m["frame"]["n"][2]) < 1e-9]
    found = collections.Counter()
    worst = {}
    for (ma, pa, za0, za1), (mb, pb, zb0, zb1) in itertools.product(A, B):
        if min(za1, zb1) - max(za0, zb0) < 1.0: continue
        try: area = pa.intersection(pb).area
        except Exception: continue
        if area > tol:
            key = (ma["ifc_type"], mb["ifc_type"])
            found[key] += 1
            if area > worst.get(key, (0,))[0]: worst[key] = (round(area), ma["name"], mb["name"])
    return found, worst


KINDS = {"panel": dict(cladding_type="panel"), "boards": dict(cladding_type="plank", plank_orient="horizontal"),
         "vertical boards": dict(cladding_type="plank", plank_orient="vertical"),
         "standing seam": dict(cladding_type="plank", plank_profile="seam", plank_orient="vertical")}


def _corner(internal):
    a = wall("A", (0, 0), (4000, 0), 3000, (0, -1, 0))
    b = wall("B", (4000, -3000), (4000, 0), 3000, (-1, 0, 0)) if internal else wall("B", (4000, 0), (4000, 3000), 3000, (1, 0, 0))
    return a, b


def _build(a, b, detail, kw, offset=0.0, master_first=True):
    _link, ra, rb = chained(a, b, detail, master_first)
    ra["offset"] = rb["offset"] = offset
    return generate_preview(dict({"elevations": [ra, rb], "trim": True, "splash": 0, "corner_type": detail}, **kw))


@pytest.mark.parametrize("kind", list(KINDS))
@pytest.mark.parametrize("detail", ["mitre", "lap", "butt"])
@pytest.mark.parametrize("master_first", [True, False])
def test_nothing_runs_through_anything_at_an_internal_corner(kind, detail, master_first):
    a, b = _corner(internal=True)
    assert chain_link(a, b)["k"] == -1.0
    found, worst = clashes(_build(a, b, detail, KINDS[kind], master_first=master_first))
    assert not found, worst


@pytest.mark.parametrize("detail", ["mitre", "lap", "butt"])
def test_the_face_that_is_not_the_master_stands_clear_of_the_masters_cladding(detail):
    """Master or Square at an internal corner: the master is built right into the corner,
    and everything of the other face starts beyond the master's cladding face."""
    if detail == "mitre":
        pytest.skip("a mitre meets on the bisector instead")
    a, b = _corner(internal=True)
    out = _build(a, b, detail, KINDS["panel"])
    depth = out["info"][0]["total_depth"]
    near = min(min(q[0] for q in ring_at(m, 0, d)) for m in out["geometry"] if m["elevation"] == "B"
               and m["ifc_type"] in ("batten", "panel", "gasket") for d in (m["depth"], m["depth"] + m["thickness"]))
    assert near >= depth - 0.05, near
    ends = max(max(q[0] for q in ring_at(m, 0, m["depth"])) for m in out["geometry"] if m["elevation"] == "A" and m["ifc_type"] == "batten")
    assert abs(ends - 4000.0) < 0.6                                   # the master's battens, into the corner


def _spans(out, name):
    """Each plank of a face as (start, end) across it, at the planks' backs."""
    return sorted((round(min(q[0] for q in ring_at(m, 0, m["depth"])), 1), round(max(q[0] for q in ring_at(m, 0, m["depth"])), 1))
                  for m in out["geometry"] if m["ifc_type"] == "plank" and m["elevation"] == name)


@pytest.mark.parametrize("detail", ["profile", "mitre", "butt"])
def test_vertical_planks_carry_on_round_an_external_corner(detail):
    """Whatever the setting-out, the boards run to where that corner's boards stop: the
    closing plank is there however narrow, it is never wider than a plank, and no joint
    is wider than the plank gap."""
    a, b = _corner(internal=False)
    ends = set()
    for offset in range(0, 160, 8):
        out = _build(a, b, detail, KINDS["vertical boards"], offset=float(offset))
        for name in "AB":
            spans = _spans(out, name)
            assert max(e - s for s, e in spans) <= 150.0 + 1e-6, (offset, name)
            assert max(n[0] - p[1] for p, n in zip(spans, spans[1:])) <= 8.0 + 1e-6, (offset, name)
        ends.add(_spans(out, "A")[-1][1])
        found, worst = clashes(out)
        assert not [k for k in found if "plank" in k], (offset, worst)
    # A's last plank always ends within a joint and the narrowest closer of one place
    assert max(ends) - min(ends) <= 8.0 + 3.0 + 1e-6, sorted(ends)
    if detail == "butt":
        assert max(ends) == 4000.0                                    # square: on the wall's corner line
    else:
        assert min(ends) > 4050.0                                     # round the corner, out to the other face's boards


def test_a_closing_plank_too_narrow_to_cut_is_left_out():
    a, b = _corner(internal=False)
    for offset in range(0, 160, 2):
        out = _build(a, b, "mitre", KINDS["vertical boards"], offset=float(offset))
        assert min(e - s for s, e in _spans(out, "A") + _spans(out, "B")) >= 3.0, offset


@pytest.mark.parametrize("detail", ["mitre", "lap", "profile"])
def test_a_standing_seam_tray_at_a_corner_is_never_wider_than_a_tray(detail):
    """A tray that turns an external corner runs on past the wall's corner line by the
    depth of the buildup. That is counted in the setting-out, so with it the tray is still
    no wider than the tray width, and never a sliver either."""
    a, b = _corner(internal=False)
    for offset in range(0, 530, 20):
        out = _build(a, b, detail, KINDS["standing seam"], offset=float(offset))
        for name in "AB":
            trays = sorted((m for m in out["geometry"] if m["ifc_type"] == "plank" and m["elevation"] == name),
                           key=lambda m: min(q[0] for q in m["profile"]))
            widths = [max(q[0] for q in ring_at(m, 0, m["depth"])) - min(q[0] for q in ring_at(m, 0, m["depth"])) for m in trays]
            assert max(widths) <= 530.0 + 1e-6, (detail, offset, name, max(widths))
            at_corner = widths[-1] if name == "A" else widths[0]          # the far ends are free: any closing cut
            assert at_corner >= 100.0 - 1e-6, (detail, offset, name, at_corner)
        found, worst = clashes(out)
        assert not found, (detail, offset, worst)
