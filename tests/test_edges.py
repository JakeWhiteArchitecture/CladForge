"""Edges: what each kind is, the offsets that pull the buildup back from them, the mitre
gap, and the head lining with its ventilation."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from synthetic import payload, corner_payload, ORIGIN  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from cladding_constants import frame_to_world  # noqa: E402

ZERO = {"edge_top": 0, "edge_side": 0, "edge_bottom": 0}


@pytest.fixture(scope="module")
def elevation():
    return extract_elevation(payload())      # 8000 x 3000, a 1200 x 1200 window at 2000..3200 x 900..2100


def _extent(out, types, axis, fn):
    return fn(q[axis] for m in out["geometry"] if m["ifc_type"] in types for q in m["profile"])


def _build(elevation, kind="plank", **kw):
    return generate_preview({"elevations": [dict(elevation, offset=0, **kw)], "cladding_type": kind,
                             "trim": True, "reveals": False})


def test_edges_are_classified(elevation):
    a, b = extract_elevation(payload()), extract_elevation(corner_payload())
    ra = dict(a, chain="Chain 1", chain_start=0, chain_reversed=False, offset=0, corner_hi=1.0)
    rb = dict(b, chain="Chain 1", chain_start=a["width"], chain_reversed=False, offset=0, corner_lo=1.0)
    out = generate_preview({"elevations": [ra, rb], "cladding_type": "panel"})
    edges = out["info"][0]["edges"]
    kinds = {e["kind"] for e in edges}
    assert kinds == {"corner", "jamb", "head", "top", "side", "bottom"}, kinds
    corner = [e for e in edges if e["kind"] == "corner"]
    assert corner and all(e["p1"][0] == e["p2"][0] == 8000.0 for e in corner)     # A's right end
    head = [e for e in edges if e["kind"] == "head"]
    assert len(head) == 1 and head[0]["p1"][1] == head[0]["p2"][1] == 2100.0
    assert sorted(e["p1"][0] for e in edges if e["kind"] == "jamb") == [2000.0, 3200.0]
    # the top of the cladding under the cill is a top edge
    assert any(e["kind"] == "top" and e["p1"][1] == e["p2"][1] == 900.0 for e in edges)
    # the pipe penetration has no edges of its own
    assert not any(6000.0 <= min(e["p1"][0], e["p2"][0]) and max(e["p1"][0], e["p2"][0]) <= 6100.0
                   and 300.0 <= min(e["p1"][1], e["p2"][1]) <= 400.0 for e in edges)


@pytest.mark.parametrize("kind", ["plank", "panel"])
def test_each_offset_pulls_back_battens_and_boards(elevation, kind):
    base = _build(elevation, kind, **ZERO)
    for field, value, axis, fn, sign in (("edge_top", 40, 1, max, -1), ("edge_bottom", 40, 1, min, 1),
                                         ("edge_side", 30, 0, min, 1), ("edge_side", 30, 0, max, -1)):
        out = _build(elevation, kind, **dict(ZERO, **{field: value}))
        for types in (("batten",), ("plank", "panel")):
            was, now = _extent(base, types, axis, fn), _extent(out, types, axis, fn)
            assert abs((now - was) - sign * value) < 1e-6, (kind, field, types, was, now)


def test_offsets_default_to_ten_top_and_bottom(elevation):
    # panels, whose closing row always reaches the top (a plank course may stop short of it)
    base, out = _build(elevation, "panel", **ZERO), _build(elevation, "panel")
    assert abs(_extent(out, ("panel",), 1, max) - (_extent(base, ("panel",), 1, max) - 10)) < 1e-6
    assert abs(_extent(out, ("panel",), 1, min) - (_extent(base, ("panel",), 1, min) + 10)) < 1e-6
    assert _extent(out, ("panel",), 0, min) == _extent(base, ("panel",), 0, min)     # free ends: 0


def _corner_pair(gap=None):
    a, b = extract_elevation(payload()), extract_elevation(corner_payload())
    extra = {} if gap is None else {"mitre_gap": gap}
    ra = dict(a, chain="Chain 1", chain_start=0, chain_reversed=False, offset=0, corner_hi=1.0, **extra)
    rb = dict(b, chain="Chain 1", chain_start=a["width"], chain_reversed=False, offset=0, corner_lo=1.0, **extra)
    return generate_preview({"elevations": [ra, rb], "cladding_type": "panel", "corner": "mitre",
                             "trim": False, "reveals": False, "set_out_from_openings": False})


@pytest.mark.parametrize("gap", [None, 16.0, 0.0])
def test_the_mitre_gap_opens_between_mitred_boards(gap):
    out = _corner_pair(gap)
    want = 10.0 if gap is None else gap
    ends = {"A": set(), "B": set(), "battens": set()}
    for m in out["geometry"]:
        c = m.get("corner") or {}
        if m["ifc_type"] == "panel" and "k_r" in c and m["elevation"] == "Elevation A":
            ends["A"].add(round(c["ext_r"], 6))
        if m["ifc_type"] == "panel" and "k_l" in c and m["elevation"] == "Elevation B":
            ends["B"].add(round(c["ext_l"], 6))
        if m["ifc_type"] == "batten" and ("k_r" in c or "k_l" in c):
            ends["battens"].add(round(c.get("ext_r", c.get("ext_l", 0.0)), 6))
    assert len(ends["A"]) == 1 and len(ends["B"]) == 1, ends
    # both mitre planes lie on the bisector, pulled back along each face: the joint between
    # them, measured square to the planes, is the gap
    ext_a, ext_b = ends["A"].pop(), ends["B"].pop()
    assert abs((abs(ext_a) + abs(ext_b)) / math.sqrt(2) - want) < 1e-6, (ext_a, ext_b)
    assert ends["battens"] == {0.0}                     # the layers behind still meet


def test_the_mitre_gap_at_a_reveal(elevation):
    out = generate_preview({"elevations": [dict(elevation, offset=0, mitre_gap=16.0)], "cladding_type": "panel",
                            "trim": True, "reveals": True})
    pull = 8.0 * math.sqrt(2)
    face = out["info"][0]["total_depth"]
    board = 9.0
    linings = [m for m in out["geometry"] if m["ifc_type"] == "reveal"]
    assert linings and all(m["corner"]["k_l"] == 1.0 and abs(m["corner"]["ext_l"] + board + pull) < 1e-6 for m in linings)
    shifts = {tuple(round(x, 6) for x in sh) for m in out["geometry"] if m.get("vshift")
              for ring in m["vshift"] for sh in ring if any(sh)}
    e = round(board - face - pull, 6)
    assert (e, 1.0, 0.0, 0.0) in shifts                              # left jamb
    assert (-e, -1.0, 0.0, 0.0) in shifts                            # right jamb
    assert (0.0, 0.0, -e, -1.0) in shifts                            # head


def _head(elevation, **kw):
    out = generate_preview({"elevations": [dict(elevation, offset=0, **kw)], "cladding_type": "panel",
                            "trim": True, "reveals": True})
    head = next(m for m in out["geometry"] if m["ifc_type"] == "reveal" and m["name"].endswith("H"))
    board = [sh for m in out["geometry"] if m.get("vshift") for ring in m["vshift"] for sh in ring if sh[3]]
    return head, board[0], out["info"][0]["total_depth"]


def test_head_air_space_only_when_vented_at_the_back(elevation):
    head_z = ORIGIN[2] + 2100.0
    front, board_front, face = _head(elevation)                            # the default: at front
    back, board_back, _f = _head(elevation, head_vent="back")
    wider, _b, _f2 = _head(elevation, head_vent="back", head_air=25.0)
    for m in (front, back, wider):
        assert m["frame"]["n"] == [0.0, 0.0, -1.0] and m["frame"]["v"][2] == 0.0   # lies flat, facing down
    # at front the lining is tight to the head, and runs back to the frame (no frame in
    # this model: the chain's default, 50 mm behind the wall face)
    assert abs(frame_to_world(front["frame"], 0, 0)[2] - head_z) < 1e-6
    assert max(q[0] for q in front["profile"]) == face + 50.0
    # at back there is air over it, and it stops the same distance short of the frame
    assert abs(frame_to_world(back["frame"], 0, 0)[2] - (head_z - 10.0)) < 1e-6
    assert max(q[0] for q in back["profile"]) == face + 50.0 - 10.0
    assert abs(frame_to_world(wider["frame"], 0, 0)[2] - (head_z - 25.0)) < 1e-6
    # the face board comes down to the lowered lining, so the air space stays closed at the front
    assert abs(board_back[2] - (board_front[2] - 10.0)) < 1e-6
    # the head lining is mitred at its front edge like the jambs, across the mitre gap
    assert front["corner"]["k_l"] == 1.0 and abs(front["corner"]["ext_l"] + 9.0 + 5 * math.sqrt(2)) < 1e-6


def test_head_lining_exports(elevation):
    import ifcopenshell
    from ifc_generator import meshes_to_ifc
    from dxf_generator import meshes_to_dxf_string
    params = {"elevations": [dict(elevation, offset=0, head_vent="back")], "cladding_type": "panel",
              "trim": True, "reveals": True}
    out = generate_preview(params)
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    names = [e.Name for e in ifc.by_type("IfcCovering")]
    assert "Elevation A Reveal 1H" in names
    assert len(ifc.by_type("IfcElement")) >= len(out["geometry"])
    dxf = meshes_to_dxf_string(out["geometry"], params, out["info"])
    assert "Reveal linings: 3 no." in dxf


def test_the_opening_mitre_is_on_the_true_bisector(elevation):
    """At a mitred jamb the board and the lining are cut on the bisector through the outer
    arris (the lining's opening face at the cladding face) and the inner corner, each
    half the mitre gap off it, so the gap is the mitre gap straight across and the arris
    is closed; and no board outline folds back on itself, panels or planks."""
    from shapely.geometry import Polygon
    from cladding_primitives import ring_at
    from cladding_constants import world_to_frame
    out = generate_preview({"elevations": [dict(elevation, offset=0)], "cladding_type": "panel",
                            "trim": True, "reveals": True})
    F, face, board, u0, V = elevation["frame"], out["info"][0]["total_depth"], 9.0, 2000.0, 1300.0
    pull = 5.0 * math.sqrt(2)
    edge = {}
    for m in out["geometry"]:
        if m["ifc_type"] == "panel" and m.get("vshift"):
            for d in (face - board, face):
                us = [q[0] for q in ring_at(m, 0, d) if abs(q[0] - u0) < 20 and 905 <= q[1] <= 2095]
                if us:
                    edge[d] = max(us)
    assert abs(edge[face - board] - (u0 - pull)) < 1e-6          # back of the board: pulled off
    assert abs(edge[face] - (u0 + board - pull)) < 1e-6          # face: out to the arris, less the gap
    (lining,) = [m for m in out["geometry"] if m["ifc_type"] == "reveal" and m["name"].endswith("1L")]
    front = {}
    for s in (0.0, board):
        ul = min(q[0] for q in ring_at(lining, 0, s))
        u, _v, d = world_to_frame(F, frame_to_world(lining["frame"], ul, 0.0, s))
        front[round(u - u0)] = d                                     # the frame is rounded to 1e-6
    assert abs(front[0] - (face - board - pull)) < 0.01          # its back face, behind the board
    assert abs(front[9] - (face - pull)) < 0.01                  # its opening face, to the arris
    for kind in ("panel", "plank"):
        out = generate_preview({"elevations": [dict(elevation, offset=0)], "cladding_type": kind,
                                "trim": True, "reveals": True})
        for m in out["geometry"]:
            if m.get("vshift"):
                for d in (m["depth"], m["depth"] + m["thickness"]):
                    for k in range(1 + len(m.get("holes") or [])):
                        assert Polygon(ring_at(m, k, d)).is_valid, (kind, m["name"], d)
