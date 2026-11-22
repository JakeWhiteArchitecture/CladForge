"""Coated galvanised steel standing seam: GreenCoat's Hip 1 on the hips (cladding_greencoat)."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cladding_constants import _parse, frame_to_world  # noqa: E402
from cladding_greencoat import HIP1_H, HIP1_W  # noqa: E402
from cladding_primitives import ring_at  # noqa: E402
from test_gablet import gablet  # noqa: E402
from test_hip import _hipped_roof  # noqa: E402


def _corners(m):
    d0, d1 = float(m["depth"]), float(m["depth"]) + float(m["thickness"])
    return [frame_to_world(m["frame"], x, y, d) for d in (d0, d1) for x, y in ring_at(m, 0, d)]


def test_the_metal_is_zinc_unless_the_chain_says_steel():
    assert _parse({})["seam_metal"] == "zinc"
    assert _parse({"seam_metal": "steel"})["seam_metal"] == "steel"
    assert _parse({"seam_metal": "VMZinc"})["seam_metal"] == "zinc"


def test_zinc_hips_keep_the_g3_cap():
    g = _hipped_roof()[2]["geometry"]
    assert not [m for m in g if m.get("hip1")]
    assert [m for m in g if m.get("cap_leg") and " Hip Cap " in m["name"]]


def test_steel_hips_are_hip_1_a_batten_and_flashing_on_each_hip():
    out = _hipped_roof(seam_metal="steel")[2]
    g = out["geometry"]
    assert not [m for m in g if m.get("cap_leg") and " Hip Cap " in m["name"]]
    battens = {m["name"] for m in g if m.get("hip1") and m["ifc_type"] == "batten" and not m.get("of")}
    flashings = {m["name"] for m in g if m.get("hip1") and m["ifc_type"] == "ridge_cap" and not m.get("of") and not m.get("stop_end")}
    assert len(battens) == 4 and len(flashings) == 4            # one of each per hip, built by its master
    hips = [h for i in out["info"] for h in i.get("hips", [])]
    assert len(hips) == 8 and all(h["detail"] == "Hip 1" for h in hips)
    # the ridge cap runs whole: no legs mitred to hip caps
    assert [m for m in g if m["name"].endswith("Ridge Cap") and not m.get("cap_leg")]


def test_the_batten_stands_its_depth_off_the_deck_and_the_deck_meets_on_the_hip():
    g = _hipped_roof(seam_metal="steel")[2]["geometry"]
    b = next(m for m in g if m.get("hip1") and m["ifc_type"] == "batten" and not m.get("of"))
    xs = [q[0] for q in b["profile"]]
    ys = [q[1] for q in b["profile"]]
    assert abs(max(xs) - min(xs) - HIP1_W) < 0.5
    assert max(ys) - min(ys) >= HIP1_H


def test_at_the_eaves_the_hip_comes_to_a_point_on_plan():
    """Each half is cut on its own slope's eaves line: the South half on a plane facing south,
    the West half on one facing west, so the two stop ends meet at the corner like a mitre."""
    g = _hipped_roof(seam_metal="steel")[2]["geometry"]
    stops = [m for m in g if m.get("stop_end") and "Hip Stop End West" in m["name"] and m["elevation"] == "South"]
    assert len(stops) == 2
    normals = sorted(tuple(round(c, 3) for c in m["frame"]["n"]) for m in stops)
    assert normals == [(-1.0, 0.0, 0.0), (0.0, -1.0, 0.0)]       # facing west and facing south
    # and they meet at the corner: their nearest points within a few mm of each other, out past the eaves
    a, b = (_corners(m) for m in stops)
    assert min(math.dist(p, q) for p in a for q in b) < 3.0
    # 50 past the front of the eaves bullnose, which stands about 60 out from the wall line on plan
    assert -160.0 < min(p[0] for p in a + b) < -80.0 and -160.0 < min(p[1] for p in a + b) < -80.0


def test_hips_meeting_at_the_top_of_a_hip_end_are_mitred():
    g = _hipped_roof(seam_metal="steel")[2]["geometry"]
    # batten, flashing and the two slopes' upstands, at both ends of the ridge
    assert len([m for m in g if m.get("hip1") and m["name"].endswith("Mitre End")]) == 16


def test_under_a_gablet_the_batten_tapers_to_nothing_at_level_plus_three_degrees():
    out = gablet(seam_metal="steel")[2]
    g = out["geometry"]
    tapers = [m for m in g if m.get("hip1") and m["name"].endswith("Taper")]
    assert len(tapers) == 8                                       # batten, flashing and both upstands, each hip
    hip = next(h for i in out["info"] for h in i.get("hips", []) if h.get("taper"))
    # 30° slopes: the hip rises at atan(tan30 / √2); the batten's depth over its rise less 3°
    rise = math.sin(math.atan(math.tan(math.radians(30)) / math.sqrt(2)))
    run = math.cos(math.atan(math.tan(math.radians(30)) / math.sqrt(2)))
    want = HIP1_H / (rise - run * math.tan(math.radians(3.0)))
    assert abs(hip["taper"] - want) < 1.0
    b = next(m for m in tapers if m["ifc_type"] == "batten")
    end = float(b["depth"]) + float(b["thickness"])
    ys = [q[1] for q in ring_at(b, 0, end)]
    assert max(ys) - min(ys) < 30.0                               # flat on the decks at its end


def test_the_trays_are_turned_up_to_the_batten_s_top_and_the_flashing_is_a_flat_cap():
    g = _hipped_roof(seam_metal="steel")[2]["geometry"]
    ups = [m for m in g if m.get("hip1") and m["ifc_type"] == "seam" and not m.get("of")]
    assert len(ups) == 8                                          # each slope's, on each hip
    b = next(m for m in g if m.get("hip1") and m["ifc_type"] == "batten" and not m.get("of"))
    top = max(q[1] for q in b["profile"])
    for u in ups:
        assert top - 2.0 < max(q[1] for q in u["profile"]) < top
    cap = next(m for m in g if m.get("hip1") and m["name"].startswith("South Hip Flashing") and not m.get("of"))
    ys = [q[1] for q in cap["profile"]]
    assert max(ys) - min(ys) < 15.0                               # a flat cap, its edges turned down 10


def test_a_seam_running_out_on_a_steel_hip_is_not_folded():
    g = _hipped_roof(seam_metal="steel")[2]["geometry"]
    z = _hipped_roof()[2]["geometry"]
    name = "South Tray C3-1 Seam overcloak"
    steel = next(m for m in g if m["name"] == name)
    zinc = next(m for m in z if m["name"] == name)
    def top_shift(m):            # how far its top end moves up the slope with depth: a 45° fold is 1 more than the hip's lean
        q, r = max(zip(m["profile"], m["vshift"][0]), key=lambda qr: qr[0][1])
        return r[3]
    assert abs(top_shift(steel) - top_shift(zinc) - 1.0) < 0.05    # zinc: the hip's lean less the fold; steel: the lean alone
