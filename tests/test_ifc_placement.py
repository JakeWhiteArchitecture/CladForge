"""Every part stands in the IFC where the build put it. The app draws its own geometry and
the IFC is written from the same parts by other code, so one can be right while the other
is not: a jamb lining on one side of each opening used to be written upside down about the
bottom of its clad area, hanging below its opening (and below the building) in any IFC
viewer while the app's own view showed it in place."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import ifcopenshell  # noqa: E402

from cladding_constants import frame_to_world  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from cladding_primitives import ring_at  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from ifc_generator import meshes_to_ifc, _handed  # noqa: E402

geom = pytest.importorskip("ifcopenshell.geom")

WINDOW = (2137.0, 3391.0, 800.0, 2600.0)      # along the wall, and in height: it crosses both clad areas
SEAM = dict(cladding_type="plank", plank_profile="seam", plank_orient="vertical")


def _face(name, z0, z1, length=6000.0):
    """A wall face between two levels, the window notched or cut out of it."""
    a0, a1, w0, w1 = WINDOW
    lo, hi = max(z0, w0), min(z1, w1)
    rects = [(0.0, a0, z0, z1), (a1, length, z0, z1)]
    rects += [(a0, a1, z0, lo)] if lo > z0 else []
    rects += [(a0, a1, hi, z1)] if hi < z1 else []
    tris = []
    for a, b, c, d in rects:
        q = [[a, 0.0, c], [b, 0.0, c], [b, 0.0, d], [a, 0.0, d]]
        tris += [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]
    return extract_elevation({"name": name, "faces": tris, "outward": [0, -1, 0], "context": [],
                              "options": {"debug": False, "clad_depth": 150.0}})


def _two_areas(upper, lower, **kw):
    """Standing seam (say) over panels on one wall, one window standing in both."""
    up = dict(_face("Upper", 1500.0, 4000.0), offset=0, chain="Chain 1", **upper)
    low = dict(_face("Lower", 0.0, 1500.0), offset=0, chain="Chain 2", **lower)
    params = dict({"elevations": [up, low], "trim": True, "splash": 0}, **dict(upper, **kw))
    out = generate_preview(params)
    return out, ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))


def _built_box(mesh):
    """The part's bounding box in the world, from the build's own geometry."""
    near = float(mesh["depth"])
    pts = [frame_to_world(mesh["frame"], u, v, d) for d in (near, near + float(mesh["thickness"]))
           for u, v in ring_at(mesh, 0, d)]
    return [min(q[i] for q in pts) for i in range(3)] + [max(q[i] for q in pts) for i in range(3)]


def _ifc_boxes(ifc):
    """{name: bounding box in the world} of every product with a body, as a reader makes it."""
    settings = geom.settings()
    settings.set("use-world-coords", True)
    out = {}
    for product in ifc.by_type("IfcProduct"):
        if not product.Representation:
            continue
        verts = geom.create_shape(settings, product).geometry.verts
        scale = 1000.0 if max(abs(c) for c in verts) < 100.0 else 1.0       # the kernel answers in metres
        xyz = [[verts[i + k] * scale for i in range(0, len(verts), 3)] for k in range(3)]
        out[product.Name] = [min(a) for a in xyz] + [max(a) for a in xyz]
    return out


BUILDS = [(SEAM, dict(cladding_type="panel"), {}),
          (dict(cladding_type="panel"), SEAM, {}),
          (dict(cladding_type="panel"), dict(cladding_type="plank", plank_orient="horizontal"), {"jamb_detail": "lap"}),
          (dict(cladding_type="plank", plank_orient="vertical"), dict(cladding_type="panel"), {"jamb_detail": "butt"})]


@pytest.mark.parametrize("upper, lower, kw", BUILDS)
def test_every_part_is_where_the_build_put_it(upper, lower, kw):
    out, ifc = _two_areas(upper, lower, **kw)
    boxes = _ifc_boxes(ifc)
    names = [m["name"] for m in out["geometry"]]
    checked = 0
    for mesh in out["geometry"]:
        if names.count(mesh["name"]) > 1 or mesh.get("section_ring"):
            continue                                    # told apart by name; a profiled plank is not its board
        if mesh["name"] not in boxes:
            assert mesh["ifc_type"] == "seam" or mesh.get("of")     # a tray's seam, or its head turned out under a sill: in the tray's element
            continue
        parts = [_built_box(mesh)] + [_built_box(m) for m in out["geometry"] if mesh["ifc_type"] == "plank"
                                      and m["name"].startswith(mesh["name"] + " ") and m["name"] not in boxes]
        want = [min(b[k] for b in parts) for k in range(3)] + [max(b[k] for b in parts) for k in range(3, 6)]
        got = boxes[mesh["name"]]
        assert got == pytest.approx(want, abs=0.6), mesh["name"]
        checked += 1
    assert checked > 40


def test_the_linings_of_one_window_in_two_clad_areas():
    """Two linings a jamb, one for each clad area, each in its own: none hangs below."""
    out, ifc = _two_areas(SEAM, dict(cladding_type="panel"))
    boxes = _ifc_boxes(ifc)
    for side in "LR":
        upper, lower = boxes["Upper Reveal 1%s" % side], boxes["Lower Reveal 1%s" % side]
        assert (upper[2], upper[5]) == pytest.approx((1500.0, WINDOW[3]), abs=40.0)
        assert (lower[2], lower[5]) == pytest.approx((WINDOW[2], 1500.0), abs=40.0)
    jambs = [n for n in boxes if "Reveal 1L" in n or "Reveal 1R" in n]
    assert sorted(jambs) == ["Lower Reveal 1L", "Lower Reveal 1R", "Upper Reveal 1L", "Upper Reveal 1R"]
    # the frames themselves: one side of each opening is the mirror of the other
    hands = {m["name"]: _handed(m["frame"]) for m in out["geometry"] if m["ifc_type"] == "reveal"}
    assert {hands["Upper Reveal 1L"], hands["Upper Reveal 1R"]} == {1, -1}


def test_the_download_has_a_reference_cross_at_the_origin():
    """The cladding goes out in the host model's coordinates; the app's download adds a cross
    at the IFC's origin (0, 0, 0) to snap it back onto the model by. Not asked for, none."""
    up = dict(_face("Upper", 0.0, 3000.0), offset=0, chain="Chain 1")
    params = {"elevations": [up], "trim": True, "splash": 0, "context": {"offset": [1000.0, 2000.0, 0.0], "reference_point": True}}
    out = generate_preview(params)
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    (ref,) = [e for e in ifc.by_type("IfcBuildingElementProxy") if "reference point" in (e.Name or "")]
    s = geom.settings()
    s.set(s.USE_WORLD_COORDS, True)
    v = geom.create_shape(s, ref).geometry.verts
    xs, ys, zs = v[0::3], v[1::3], v[2::3]
    k = 1000.0 if max(map(abs, v)) < 10 else 1.0           # the geometry in metres or mm
    assert min(xs) * k == pytest.approx(-300.0, abs=1.0) and max(xs) * k == pytest.approx(300.0, abs=1.0)
    assert min(ys) * k == pytest.approx(-300.0, abs=1.0) and max(zs) * k == pytest.approx(300.0, abs=1.0)
    plain = dict(params, context={"offset": [1000.0, 2000.0, 0.0]})
    ifc2 = ifcopenshell.open(meshes_to_ifc(out["geometry"], plain, out["info"]))
    assert not [e for e in ifc2.by_type("IfcBuildingElementProxy") if "reference point" in (e.Name or "")]
