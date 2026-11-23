"""One solid per element in the IFC (cladding_union): a tray with its seam folds and drip,
a flashing with its seam and end pieces, a cap with its stop ends, unioned into one closed
brep before the file is written."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import ifcopenshell  # noqa: E402

import cladding_union  # noqa: E402
from cladding_union import _ear_clip, _volume, faces, piece_mesh, union_pieces  # noqa: E402
from ifc_generator import meshes_to_ifc, tray_owners  # noqa: E402
from solid_checks import one_closed_solid, volume  # noqa: E402
from test_verge import _one, _roof, SEAM  # noqa: E402
from test_hip import _hipped_roof  # noqa: E402


def _box(x0, y0, z0, dx, dy, dz, name="Box"):
    return {"name": name, "profile": [[x0, y0], [x0 + dx, y0], [x0 + dx, y0 + dy], [x0, y0 + dy]], "depth": z0, "thickness": dz,
            "frame": {"origin": [0.0, 0.0, 0.0], "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0], "n": [0.0, 0.0, 1.0]}}


def _closed(verts, tris):
    edges = {}
    for a, b, c in tris:
        for e in ((a, b), (b, c), (c, a)):
            edges[e] = edges.get(e, 0) + 1
    return all(n == 1 and edges.get((e[1], e[0])) == 1 for e, n in edges.items())


# ── the pieces and their union ────────────────────────────────────────────

def test_a_piece_is_a_closed_mesh_facing_out_whatever_its_ring_or_frame():
    box = _box(0, 0, 0, 100, 20, 50)
    v, t = piece_mesh(box)
    assert _closed(v, t) and _volume(v, t) == pytest.approx(100 * 20 * 50)
    turned = dict(box, profile=list(reversed(box["profile"])))                  # a clockwise ring
    assert _volume(*piece_mesh(turned)) == pytest.approx(100 * 20 * 50)
    mirrored = dict(box, frame=dict(box["frame"], n=[0.0, 0.0, -1.0]))          # a mirrored frame
    assert _volume(*piece_mesh(mirrored)) == pytest.approx(100 * 20 * 50)
    sheared = dict(box, vshift=[[[0.0, 0.5, 0.0, 0.0]] * 4])                     # its end cut on a slope
    v, t = piece_mesh(sheared)
    assert _closed(v, t) and _volume(v, t) == pytest.approx(100 * 20 * 50)


def test_an_l_shaped_ring_is_cut_into_ears():
    ring = [(0, 0), (60, 0), (60, 10), (10, 10), (10, 40), (0, 40)]             # reflex at (10, 10)
    tris = _ear_clip(ring)
    area = sum(abs((ring[b][0] - ring[a][0]) * (ring[c][1] - ring[a][1]) - (ring[c][0] - ring[a][0]) * (ring[b][1] - ring[a][1])) / 2.0
               for a, b, c in tris)
    assert len(tris) == 4 and area == pytest.approx(60 * 10 + 10 * 30)


def test_overlapping_pieces_union_to_one_solid_with_merged_faces():
    a, b = piece_mesh(_box(0, 0, 0, 100, 100, 100)), piece_mesh(_box(50, 0, 0, 100, 100, 100))
    v, t = union_pieces([a, b])
    assert _closed(v, t) and _volume(v, t) == pytest.approx(150 * 100 * 100, rel=1e-5)
    assert len(faces(v, t)) == 6                                                 # one box: its coplanar triangles merged back
    far = piece_mesh(_box(5.0e5, 3.0e5, 0, 100, 100, 100))                       # site coordinates: still exact
    v, t = union_pieces([far, piece_mesh(_box(5.0e5 + 100, 3.0e5, 0, 100, 100, 100))])
    assert _volume(v, t) == pytest.approx(200 * 100 * 100, rel=1e-5)



def test_a_tray_far_from_the_origin_goes_out_as_flat_faces_not_triangles():
    """On the job the roof is metres from the origin, and the trays came out of the IFC as
    a hundred-odd triangles each, drawn as lines across them: two triangles a hair out of
    line, read as a plane's offset from the origin, were millimetres apart. Each tray of a
    pitched roof, moved 18 m off, is a closed shell of polygon faces with only the odd
    triangle the shape really has."""
    _params, out = _one()
    groups = _elements_of_pieces(out)
    trays = [g for n, g in groups.items() if " Tray " in n]
    assert trays
    for pieces in trays[:6]:
        moved = []
        for m in pieces:
            fr = dict(m["frame"], origin=[m["frame"]["origin"][0] + 18000.0, m["frame"]["origin"][1] - 14000.0,
                                          m["frame"]["origin"][2] + 3000.0])
            moved.append(dict(m, frame=fr))
        got = union_pieces([piece_mesh(m) for m in moved])
        assert got is not None
        fs = faces(*got)
        edges = {}
        for outer, inner in fs:
            for ring in [outer] + inner:
                for k in range(len(ring)):
                    e = (ring[k], ring[(k + 1) % len(ring)])
                    edges[e] = edges.get(e, 0) + 1
        assert all(n == 1 and edges.get((e[1], e[0])) == 1 for e, n in edges.items())     # still closed
        assert sum(1 for o, h in fs if len(o) == 3 and not h) <= 4, len(fs)
        assert len(fs) < len(got[1]) / 2


def test_without_a_union_the_pieces_go_out_as_they_were(monkeypatch):
    params, out = _one()
    monkeypatch.setattr(cladding_union, "_native", lambda pieces: None)
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    tray = [e for e in ifc.by_type("IfcCovering") if e.ObjectType == "Standing seam tray"][1]
    assert len([i for r in tray.Representation.Representations for i in r.Items]) > 1


# ── every element made of pieces, one closed solid ────────────────────────

def _elements_of_pieces(out):
    """{element name: its pieces} for every element made of more than one mesh."""
    groups = {}
    by = {}
    for m in out["geometry"]:
        by.setdefault(m.get("elevation", ""), []).append(m)
    for g in by.values():
        own = tray_owners(g)
        for m in g:
            if id(m) in own:
                groups.setdefault(own[id(m)]["name"], [own[id(m)]]).append(m)
    return groups


@pytest.mark.parametrize("roof", ["gable_g3", "gable_zi05", "ridge", "hipped"])
def test_every_element_of_pieces_is_one_closed_solid(roof):
    if roof == "gable_g3":
        params, out = _one()
    elif roof == "gable_zi05":
        params, out = _one(elev={"verge_type": "cladding"})
    elif roof == "ridge":
        _link, params, out = _roof(elev={"ridge_end": "v"}, **SEAM)
    else:
        _ridge, params, out = _hipped_roof()
    groups = _elements_of_pieces(out)
    assert groups
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    named = {e.Name: e for e in ifc.by_type("IfcElement")}
    for name, pieces in groups.items():
        if any(piece_mesh(m) is None for m in pieces):
            continue                                                             # a custom profile's sweep: not unioned
        # no more than its pieces hold, and no less than the biggest of them (an end piece overlaps the length it ends)
        solid = volume(one_closed_solid(named[name]))
        each = [_volume(*piece_mesh(m)) for m in pieces]
        assert max(each) < solid <= sum(each) * (1.0 + 1e-4) + 1.0, (name, solid, sum(each))
    if roof == "ridge":
        (cap,) = [n for n in groups if n.endswith("Ridge Cap")]
        assert any("Stop End" in m["name"] for m in groups[cap])                 # the cap and its stop ends, one solid


@pytest.mark.parametrize("roof", ["gable_g3", "gable_zi05", "ridge", "hipped"])
def test_every_square_ended_piece_meshes_to_its_own_volume(roof):
    """Each piece's ends cut into ears that tile its profile exactly: a square-ended piece
    holds its profile's area times its length."""
    from shapely.geometry import Polygon
    if roof == "gable_g3":
        _p, out = _one()
    elif roof == "gable_zi05":
        _p, out = _one(elev={"verge_type": "cladding"})
    elif roof == "ridge":
        _l, _p, out = _roof(elev={"ridge_end": "v"}, **SEAM)
    else:
        _r, _p, out = _hipped_roof()
    checked = 0
    for m in out["geometry"]:
        if m.get("vshift") or m.get("corner") or m.get("holes") or m.get("section_ring"):
            continue
        ring = Polygon(m["profile"])
        if not ring.is_valid or ring.area < 1e-6:
            continue
        got = piece_mesh(m)
        assert got is not None, m["name"]
        assert _volume(*got) == pytest.approx(ring.area * float(m["thickness"]), rel=1e-6), m["name"]
        checked += 1
    assert checked > 20
