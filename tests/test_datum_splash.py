"""A roof meeting a face along its foot, and courses that line up round a chain."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from synthetic import payload, corner_payload, wall_face, box_tris, world, N  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402


def _slab_dv(profile_dv, u0, u1):
    """A slab drawn as a (d, v) section and run along the wall from u0 to u1."""
    a = [world(u0, v, d) for d, v in profile_dv]
    b = [world(u1, v, d) for d, v in profile_dv]
    k = len(profile_dv)
    tris = [[a[0], a[i], a[i + 1]] for i in range(1, k - 1)]
    tris += [[b[0], b[i + 1], b[i]] for i in range(1, k - 1)]
    for i in range(k):
        j = (i + 1) % k
        tris += [[a[i], a[j], b[j]], [a[i], b[j], b[i]]]
    return [[list(p) for p in t] for t in tris]


def _dormer(context):
    return {"name": "Dormer", "faces": wall_face(3000.0, 2000.0, (1000.0, 800.0, 2000.0, 1600.0)),
            "outward": list(N), "context": context, "options": {"clad_depth": 100.0}}


def test_roof_along_the_foot_keeps_its_splash():
    """A dormer front stands on the roof, which meets it along the bottom edge and falls
    away in front. That is a real abutment, so the splash zone stays there."""
    slope = math.tan(math.radians(35))
    roof = _slab_dv([(-600.0, 600 * slope), (2000.0, -2000 * slope),
                     (2000.0, -2000 * slope - 200), (-600.0, 600 * slope - 200)], -500.0, 3500.0)
    e = extract_elevation(_dormer([{"type": "IfcRoof", "name": "Main roof", "tris": roof}]))
    assert e["ok"], e["warnings"]
    roofs = [a for a in e["abutments"] if a["source"] == "IfcRoof"]
    assert roofs and abs(roofs[0]["v"]) < 5, e["abutments"]
    # With the foot switched off in the wizard the roof still clears the boards by 150.
    e["abutments"] = [dict(a, enabled=a["source"] != "base") for a in e["abutments"]]
    out = generate_preview({"elevations": [e], "trim": True})
    lows = [min(v for _u, v in m["profile"]) for m in out["geometry"] if m["ifc_type"] == "plank"]
    assert lows and min(lows) >= 150 - 1, min(lows)


def test_slab_the_wall_stands_on_is_not_an_abutment():
    """A ground slab tops out at the foot too, but its edge stops at the wall: nothing
    stands in front of the boards, so the wizard's answer about the foot stands."""
    slab = box_tris(-500.0, 3500.0, -300.0, 0.0, -2000.0, 0.0)
    e = extract_elevation(_dormer([{"type": "IfcSlab", "name": "Ground slab", "tris": slab}]))
    assert [a["source"] for a in e["abutments"]] == ["base"], e["abutments"]


def _panel_seams(out, elev):
    z0 = float(elev["frame"]["origin"][2])
    return sorted({round(min(v for _u, v in m["profile"]) + z0, 1) for m in out["geometry"]
                   if m["ifc_type"] == "panel" and m["name"].startswith(elev["name"])})


def test_courses_share_one_datum_round_the_chain():
    """Two faces of a chain start cladding at different heights. The horizontal joints
    still run level round the corner: both are set out from the lower start, and the
    higher face's first course is cut at its base."""
    a = extract_elevation(payload())
    b = extract_elevation(corner_payload())
    ra = dict(a, chain="Chain 1", chain_start=0, chain_reversed=False, offset=0)
    rb = dict(b, chain="Chain 1", chain_start=a["width"], chain_reversed=False, offset=0, clip_v_lo=437.0)
    out = generate_preview({"elevations": [ra, rb], "cladding_type": "panel", "panel_h": 1200,
                            "panel_gap": 10, "trim": False})
    za, zb = _panel_seams(out, ra), _panel_seams(out, rb)
    assert za[0] == 250.0 and zb[0] == 537.0, (za, zb)    # 100 + 150 splash; 100 + 437 baserail
    assert zb[1:] and set(zb[1:]) <= set(za), (za, zb)     # every full course lines up
    # Left to itself the raised face would set out from its own base instead.
    alone = generate_preview({"elevations": [dict(rb, chain=None)], "cladding_type": "panel",
                              "panel_h": 1200, "panel_gap": 10, "trim": False})
    assert not set(_panel_seams(alone, rb)[1:]) & set(za)


def test_clicked_elevation_sets_the_chain_datum():
    """Click an elevation and the whole chain courses from its base instead: full panels
    start at that face's foot, and the lower face gets the cut course at its base."""
    a = extract_elevation(payload())
    b = extract_elevation(corner_payload())
    common = dict(chain="Chain 1", chain_reversed=False, offset=0, course_datum_from="Elevation B")
    ra = dict(a, chain_start=0, **common)
    rb = dict(b, chain_start=a["width"], clip_v_lo=437.0, **common)
    out = generate_preview({"elevations": [ra, rb], "cladding_type": "panel", "panel_h": 1200,
                            "panel_gap": 10, "trim": False})
    za, zb = _panel_seams(out, ra), _panel_seams(out, rb)
    assert zb == [537.0, 1747.0, 2957.0], zb      # full courses from B's base
    assert za == [250.0] + zb, za                  # A cut at its own base, then B's joints
    # A name that is not in the chain falls back to the lowest start.
    stale = [dict(r, course_datum_from="Elevation Z") for r in (ra, rb)]
    out = generate_preview({"elevations": stale, "cladding_type": "panel", "panel_h": 1200,
                            "panel_gap": 10, "trim": False})
    assert _panel_seams(out, ra)[0] == 250.0 and 1460.0 in _panel_seams(out, rb)
