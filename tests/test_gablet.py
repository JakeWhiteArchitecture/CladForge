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
