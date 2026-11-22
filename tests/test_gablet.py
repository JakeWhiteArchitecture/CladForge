"""A Dutch gable (gablet) roof: hips up from the eaves corners to a small gable wall standing
on the hip end, under the main slopes' verges. The verge along the gable's rake starts at
the top of the hip, not at a corner of the slope's outline."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from cladding_preview import generate_preview  # noqa: E402
from cladding_verge import join_verges  # noqa: E402
from fabric_extract import chain_link, extract_elevation, valley_links  # noqa: E402
from test_ridge import _paired  # noqa: E402
from test_valley import _face  # noqa: E402

SEAM = dict(cladding_type="plank", plank_profile="seam", plank_orient="vertical")
PITCH = 30.0
HIP_TOP = 900.0           # the hip end's level top edge, where the gable wall stands on it


def gablet(hg=HIP_TOP, **kw):
    """9 x 6 m on plan, 30° all round: two long slopes on a ridge along x, and at the west end
    a hip end rising to a level top edge *hg* up, a gable wall standing on it under the main
    slopes' verges. Returns (the links found, params, the preview)."""
    t, s, c = math.tan(math.radians(PITCH)), math.sin(math.radians(PITCH)), math.cos(math.radians(PITCH))
    d = hg / t                                    # the hip end's run on plan, to its top edge
    faces = {   # each outline listed from its inward corner, so its fan of triangles stays inside it
        "South": _face("South", [(d, d), (0, 0), (9000, 0), (9000, 3000), (d, 3000)], lambda x, y: y * t, (0, -s, c)),
        "North": _face("North", [(d, 6000 - d), (d, 3000), (9000, 3000), (9000, 6000), (0, 6000)], lambda x, y: (6000 - y) * t, (0, s, c)),
        "West": _face("West", [(0, 6000), (0, 0), (d, d), (d, 6000 - d)], lambda x, y: x * t, (-s, 0, c)),
    }
    wall = extract_elevation({"name": "Gable", "faces": [[[d, d, hg], [d, 6000 - d, hg], [d, 3000, 3000 * t]]],
                              "outward": [-1.0, 0.0, 0.0], "context": [], "options": {"debug": False, "clad_depth": 150.0}})
    ridge = chain_link(faces["South"], faces["North"])
    south, north = _paired(faces["South"], faces["North"], ridge)
    recs = {"South": dict(south, valleys=[]), "North": dict(north, valleys=[]),
            "West": dict(faces["West"], offset=0, chain="West", valleys=[]),
            "Gable": dict(wall, offset=0, chain="Gable", valleys=[])}
    src = dict(faces, Gable=wall)
    names, found = list(recs), []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for x, y in ((a, b), (b, a)):
                link = valley_links(src[x], [src[y]])[0]
                if link:
                    recs[x]["valleys"].append(dict(link["a"], **{"with": y, "k": link["k"], "master": True}))
                    recs[y]["valleys"].append(dict(link["b"], **{"with": x, "k": link["k"], "master": False}))
                    found.append((x, y, link["kind"]))
                    break
    for name, extra in kw.pop("elev", {}).items():
        recs[name].update(extra)
    params = dict({"elevations": list(recs.values()), "trim": True, "splash": 0}, **dict(SEAM, **kw))
    return found, params, generate_preview(params)


def test_the_gablet_is_joined_two_hips_and_the_gable_wall_at_the_hip_end_s_top():
    found, _params, _out = gablet()
    assert sorted(found) == [("North", "West", "hip"), ("South", "West", "hip"), ("West", "Gable", "abut")]


def test_the_verge_over_the_gable_wall_is_the_flashing_over_its_cladding():
    """Auto, the default: the main slopes' verge along the gable's rake, from the top of the hip
    up to the ridge, stands over the picked gable wall, so it is the verge with cladding, its
    plywood oversailing that wall's cladding; the open gable at the other end keeps the G3 strip."""
    _found, params, out = gablet()
    by = {e["name"]: e for e in params["elevations"]}
    assert by["South"]["verge_walls"] == {"l": "Gable"} and by["North"]["verge_walls"] == {"r": "Gable"}
    for name, gable_side, open_side in (("South", "l", "r"), ("North", "r", "l")):
        info = next(i for i in out["info"] if i["elevation"] == name)
        verges = {v["side"]: v for v in info["verges"]}
        assert verges[gable_side]["type"] == "cladding" and verges[gable_side]["over"] == pytest.approx(83.6, abs=0.1)
        assert verges[open_side]["type"] == "g3"
        rake = (3000.0 - HIP_TOP / math.tan(math.radians(PITCH))) / math.cos(math.radians(PITCH))
        assert verges[gable_side]["length"] == pytest.approx(rake, abs=60.0)       # the rake above the hip, not the whole side
    geo = out["geometry"]
    assert [m for m in geo if m["name"] == "South Verge Flashing L"] and not [m for m in geo if m["name"] == "South Verge Support L"]


def test_the_chain_can_still_ask_for_the_g3_strip_or_set_the_oversail():
    _found, params, out = gablet(elev={"South": {"verge_type": "g3"}, "North": {"verge_oversail": 40}})
    by = {e["name"]: e for e in params["elevations"]}
    assert "verge_walls" not in by["South"]
    assert {v["side"]: v["type"] for i in out["info"] if i["elevation"] == "South" for v in i["verges"]} == {"l": "g3", "r": "g3"}
    join_verges(params)
    assert by["North"]["verge_over"] == {"r": 40.0}                          # auto with an oversail set: only where a wall is below


def test_a_seam_is_set_on_the_gable_line_so_no_verge_falls_part_way_across_a_tray():
    """The gablet's rake stands in from the slope's own end (the slope runs on out past it
    lower down, to the hip), so the trays are set out from the rake: the verge's last tray
    ends at it, and the trays beyond it run up to the hip."""
    from cladding_verge import VERGE_SEAM, verge_seam_out
    _found, params, out = gablet()
    d = HIP_TOP / math.tan(math.radians(PITCH))
    by = {e["name"]: e for e in params["elevations"]}
    for name in ("South", "North"):
        e = by[name]
        side = next(iter(e["verge_walls"]))
        over = e["verge_over"][side]
        edge = d if side == "l" else float(e["width"]) - d
        line = edge - over - verge_seam_out() if side == "l" else edge + over + verge_seam_out()
        trays = [m for m in out["geometry"] if m.get("elevation") == name and m["ifc_type"] == "plank"]
        spans = [(min(q[0] for q in m["profile"]), max(q[0] for q in m["profile"]), max(q[1] for q in m["profile"])) for m in trays]
        rake_v = HIP_TOP / math.sin(math.radians(PITCH))                     # the top of the hip, up the slope
        # no tray that runs up past the top of the hip crosses the rake's line
        assert not [s for s in spans if s[0] < line - 1.0 < line + 1.0 < s[1] and s[2] > rake_v + 50.0], (name, line)
        # the verge's last tray ends at the line, and a tray beyond it runs up to the hip
        assert any(abs((s[0] if side == "l" else s[1]) - line) < 1.5 and s[2] > rake_v + 50.0 for s in spans), (name, line)
        assert any(abs((s[1] if side == "l" else s[0]) - line) < 1.5 and s[2] < rake_v + 50.0 for s in spans), (name, line)


def test_the_hip_cap_is_cut_on_the_rake_seam_s_plane_and_nothing_stands_above_the_slope():
    """At the top of each hip, under the gable: the seam on the rake's line meets the hip a
    little below its top and stands in the gable cladding's face. The cap is cut on that
    plane: the hip end's leg runs on up to it, the slope's leg ends against the seam; the
    upstands along the hip are cut on it too, and the verge's edge seam comes down to the
    hip. The hip end's abutment parts at the gable's foot stop under the slope's plywood."""
    from cladding_constants import frame_to_world
    from cladding_hip import _hip_tops
    from cladding_primitives import ring_at
    _found, params, out = gablet()
    geo = out["geometry"]
    by = {e["name"]: e for e in params["elevations"]}
    info = {i["elevation"]: i for i in out["info"]}
    pts = lambda m: [frame_to_world(m["frame"], x, y, dd) for dd in (float(m["depth"]), float(m["depth"]) + float(m["thickness"]))   # noqa: E731
                     for x, y in ring_at(m, 0, dd)]
    t = math.tan(math.radians(PITCH))
    for name, sign in (("South", 1.0), ("North", -1.0)):
        ((h, S, T, u_s),) = _hip_tops(by[name], dict(SEAM, seam=True, seam_h=25.0), info[name])
        assert 0.0 < T[1] - S[1] < 200.0
        plane_x = frame_to_world(by[name]["frame"], u_s, S[1], 0.0)[0]           # the seam's plane: x = const here
        legs = [m for m in geo if m.get("cap_leg") and " Hip Cap " in m["name"]
                and (m["elevation"] == name and m["name"].endswith("West") or m["elevation"] == "West" and m["name"].endswith(name))]
        assert len(legs) == 2
        for m in legs + [m for m in geo if m["elevation"] in (name, "West") and " Hip Upstand" in m["name"]]:
            near = [q for q in pts(m) if math.dist(q, frame_to_world(by[name]["frame"], S[0], S[1], 0.0)) < 800.0]
            slack = 5.0 if " Hip Upstand" in m["name"] else 1.0                  # an upstand is cut at its mid-height, under the cap
            assert all(q[0] <= plane_x + slack for q in near), m["name"]         # nothing past the seam's plane
        west_leg = next(m for m in legs if m["elevation"] == "West")
        assert max(q[0] for q in pts(west_leg)) == pytest.approx(plane_x, abs=1.0)   # the hip end's leg runs on to it
        t_y = frame_to_world(by[name]["frame"], T[0], T[1], 0.0)[1]
        ys = [q[1] for m in legs for q in pts(m)]
        assert (max(ys) if sign > 0 else min(ys)) == pytest.approx(t_y, abs=1.0)    # and is cut across at the drip's foot
        # past the rake's foot the hip end's flange runs on along the gable's foot, in under the drip
        (flange,) = [m for m in geo if m["elevation"] == "West" and m["name"] == "West Hip Cap %s Flange" % name]
        fy = [q[1] for q in pts(flange)]
        assert (min(fy) if sign > 0 else max(fy)) == pytest.approx(t_y, abs=1.0) and abs(max(fy) - min(fy)) > 100.0
        drip_low = min(q[2] - (q[1] if sign > 0 else 6000.0 - q[1]) * t for m in geo if m["elevation"] == name
                       and " Verge Flashing " in m["name"] + " " for q in pts(m))
        far = [q for q in pts(flange) if abs(q[1] - t_y) > 50.0]
        assert all(q[2] - (q[1] if sign > 0 else 6000.0 - q[1]) * t >= drip_low - 1.0 for q in far)    # no lower than the drip's edge
        # the verge's flashing runs only up the rake, from its foot: not down to the eaves
        assert all(min(sign * (q[1] - t_y) for q in pts(m)) > -100.0
                   for m in geo if m["elevation"] == name and " Verge Flashing " in m["name"] + " ")
    assert not [m for m in geo if "Hip Dressing" in m["name"] or "Hip Top Stop End" in m["name"]]
    # nothing of the abutment at the gable's foot (the hip end's, the gable's own) stands above the slopes' plywood
    for m in geo:
        if m["elevation"] in ("West", "Gable") and "Abutment" in m["name"]:
            for q in pts(m):
                above = max(q[2] - q[1] * t, q[2] - (6000.0 - q[1]) * t)
                assert above < 45.0, (m["name"], above)
