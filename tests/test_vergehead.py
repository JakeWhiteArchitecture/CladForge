"""A verge between two mono ridges (cladding_vergehead): the mono ridge run on down it,
mitred onto both, with no verge built there."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cladding_preview import generate_preview  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402

SEAM = dict(cladding_type="plank", plank_profile="seam", plank_orient="vertical")
Z0 = 3000.0


def _face(name, pts, out):
    tris = [[pts[0], pts[1], pts[2]], [pts[0], pts[2], pts[3]]]
    return extract_elevation({"name": name, "faces": tris, "outward": list(out), "context": [],
                              "options": {"debug": False, "clad_depth": 150.0}})


def _job_corner(mirror=False):
    """The job's corner: a low monopitch's top edge ends where a higher one's raking side
    starts, that side running up to the higher one's own top. Both fall the same way.
    *mirror*: the same, handed the other way (x to -x)."""
    t, s, c = math.tan(math.radians(20.0)), math.sin(math.radians(20.0)), math.cos(math.radians(20.0))
    m = (lambda pts: [[-x, y, z] for x, y, z in reversed(pts)]) if mirror else (lambda pts: pts)   # noqa: E731
    low = _face("Low", m([[0, -3000, Z0 - 3000 * t], [4000, -3000, Z0 - 3000 * t], [4000, 0, Z0], [0, 0, Z0]]), (0, -s, c))
    high = _face("High", m([[4000, 0, Z0], [8000, 0, Z0], [8000, 3000, Z0 + 3000 * t], [4000, 3000, Z0 + 3000 * t]]), (0, -s, c))
    elevs = [dict(low, offset=0, chain="Low", settings=dict(SEAM), verge="none", eaves="none", head="g3"),
             dict(high, offset=0, chain="High", settings=dict(SEAM), verge="both", verge_type="cladding", eaves="none", head="g3")]
    return elevs, generate_preview({"elevations": elevs, "trim": True, "splash": 0})


def test_a_verge_with_a_mono_ridge_at_both_ends_is_a_mono_ridge_mitred_onto_both():
    elevs, out = _job_corner()
    names = [m["name"] for m in out["geometry"]]
    raking = [m for m in out["geometry"] if m["name"].startswith("High Verge Head Cap")]
    assert raking and any(not m.get("drip_end") for m in raking)
    assert sum(1 for m in raking if m.get("drip_end")) == 2                       # a mitre at each end
    # no verge built on that side; the far side, its foot at an open eaves, is still a verge
    side = next(iter(elevs[1]["verge_caps"]))
    other = "R" if side == "l" else "L"
    assert not [n for n in names if n.startswith("High Verge Flashing %s" % side.upper())]
    assert [n for n in names if n.startswith("High Verge Flashing %s" % other)]
    # the two head caps it meets are cut on the same mitres, no stop end at those ends
    for face in ("Low", "High"):
        caps = [m for m in out["geometry"] if m["elevation"] == face and m["name"].startswith("%s Head Verge Cap" % face)]
        assert any(m.get("mitred_ends") for m in caps), face
        assert any(m.get("drip_end") and "Mitre End" in m["name"] for m in caps), face
    lows = [n for n in names if n.startswith("Low Head Verge Stop End")]
    assert len(lows) <= 1                                                          # only at its far end, if anything
    # the trays' edges turned up along it
    assert [n for n in names if n.endswith("Verge Upstand")]
    info = next(i for i in out["info"] if i["elevation"] == "High")
    assert info["verge_caps"][0]["length"] > 2900.0


def _ends(m):
    f = m["frame"]
    d0, d1 = float(m["depth"]), float(m["depth"]) + float(m["thickness"])
    return [[float(o) + d * float(n) for o, n in zip(f["origin"], f["n"])] for d in (d0, d1)]


def test_a_mitred_head_cap_ends_at_its_stop_end_not_past_it():
    """A head cap cut on a mitre at one end starts part way along its frame; its stop end at
    the other end is where the cap ends (the cap ran on ~500 past it: the job's roof E).
    Both hands: the mitre at the cap's start one way, at its far end the other."""
    seen = 0
    for mirror in (False, True):
        geo = _job_corner(mirror)[1]["geometry"]
        for stop in [m for m in geo if m.get("stop_end") and "Head Verge Stop End" in m["name"]]:
            cap = next(m for m in geo if m["name"] == stop["of"])
            if not cap.get("mitred_ends"):
                continue
            seen += 1
            far = min(math.dist(e, s) for e in _ends(cap) for s in _ends(stop))
            assert far < 2.0, (mirror, cap["name"], far)
    assert seen
