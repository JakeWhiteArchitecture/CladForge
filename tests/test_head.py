"""The head verge: the level top edge of a monopitch roof, after VMZINC's "Head verge G3".
Half a ridge: the trays stop short of a plumb plane and are turned up, a cap lies over
them and turns down over the wall below."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import ifcopenshell  # noqa: E402

from cladding_checks import check_rules  # noqa: E402
from cladding_constants import frame_to_world  # noqa: E402
from cladding_head import HEAD_BACK, HEAD_CLEAR, HEAD_COVER, HEAD_DROP  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from cladding_primitives import ring_at  # noqa: E402
from cladding_ridge import RIDGE_GAP, RIDGE_LEG  # noqa: E402
from fabric_extract import extract_elevation, head_link, eaves_link, valley_links  # noqa: E402
from ifc_generator import meshes_to_ifc, own_elements  # noqa: E402

SEAM = dict(cladding_type="plank", plank_profile="seam", plank_orient="vertical")
PANEL = dict(cladding_type="panel")
H = 2700.0


def _face(name, pts, out):
    quad = lambda q: [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]                                # noqa: E731
    return extract_elevation({"name": name, "faces": quad(pts), "outward": list(out), "context": [],
                              "options": {"debug": False, "clad_depth": 150.0}})


def _pair(pitch=15.0):
    """A wall along x, and a monopitch roof whose top edge is the wall's head: it falls
    away behind the wall."""
    t, s, c = math.tan(math.radians(pitch)), math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    wall = _face("Wall", [[0, 0, 0], [6000, 0, 0], [6000, 0, H], [0, 0, H]], (0, -1, 0))
    roof = _face("Roof", [[6000, 0, H], [0, 0, H], [0, 3000, H - 3000 * t], [6000, 3000, H - 3000 * t]], (0, s, c))
    return wall, roof


def _build(pitch=15.0, wall=None, picked=True, head=None, **kw):
    w, r = _pair(pitch)
    link = head_link(w, r)
    rec = lambda side, other: dict(link[side], **{"with": other, "k": link["k"], "master": side == "b"})    # noqa: E731
    roof = dict(r, offset=0, chain="Chain 2", settings=dict(SEAM), valleys=[rec("b", "Wall")] if picked else [], verge="none", eaves="none")
    if head:
        roof["head"] = head
    elevs = ([dict(w, offset=0, chain="Chain 1", settings=wall or dict(SEAM), valleys=[rec("a", "Roof")])] if picked else []) + [roof]
    params = dict({"elevations": elevs, "trim": True, "splash": 0}, **kw)
    return link, params, generate_preview(params)


def _section(out, face, kind, pick=None):
    """Corners of the face's parts of this kind, in section: (out from the wall's face on
    plan, height)."""
    pts = []
    for m in out["geometry"]:
        if m["elevation"] == face and m["ifc_type"] == kind and (pick is None or pick(m)):
            near = float(m["depth"])
            pts += [frame_to_world(m["frame"], u, v, d) for d in (near, near + float(m["thickness"])) for u, v in ring_at(m, 0, d)]
    return [(-q[1], q[2]) for q in pts]


def test_a_monopitch_that_falls_away_behind_the_head_of_a_wall_is_a_head_not_an_eaves():
    wall, roof = _pair()
    link = head_link(wall, roof)
    assert link["kind"] == "head" and link["a"]["roof"] is False and link["b"]["roof"] is True and link["length"] == 6000.0
    assert link["b"]["p1"][1] == link["b"]["p2"][1] == pytest.approx(roof["height"], abs=0.1)      # the roof's top edge
    assert eaves_link(wall, roof) is None
    assert [k and k["kind"] for k in valley_links(wall, [roof])] == ["head"]
    t = math.tan(math.radians(30.0))
    rising = _face("Rising", [[0, 0, H], [6000, 0, H], [6000, 3000, H + 3000 * t], [0, 3000, H + 3000 * t]], (0, -0.5, math.cos(math.radians(30.0))))
    assert head_link(wall, rising) is None and eaves_link(wall, rising)["kind"] == "eaves"


@pytest.mark.parametrize("pitch", [5.0, 15.0, 30.0])
@pytest.mark.parametrize("wall", [dict(SEAM), dict(PANEL)])
def test_the_roof_runs_on_over_the_wall_and_the_cap_turns_down_over_its_cladding(pitch, wall):
    _link, _params, out = _build(pitch, wall=wall)
    info = {i["elevation"]: i for i in out["info"]}
    c = math.cos(math.radians(pitch))
    outside = max([h for h, _z in _section(out, "Wall", "seam")] + [info["Wall"]["total_depth"]])      # the wall's cladding, seams and all
    wall_top = max(z for _h, z in _section(out, "Wall", "panel" if wall["cladding_type"] == "panel" else "plank"))
    assert wall_top == pytest.approx(H - 10.0, abs=0.2), "the wall is left as it is: its top offset under the roof"
    # the cap: its drop HEAD_CLEAR in front of that cladding, its foot HEAD_DROP below the top of it
    (cap,) = [m for m in out["geometry"] if m["ifc_type"] == "ridge_cap"]
    assert cap["name"] == "Roof Head Verge Cap" and cap["thickness"] == 6000.0
    sec = _section(out, "Roof", "ridge_cap")
    assert max(h for h, _z in sec) == pytest.approx(outside + HEAD_CLEAR, abs=1.0)
    assert min(z for _h, z in sec) == pytest.approx(wall_top - HEAD_DROP, abs=1.0)
    # the trays: turned up RIDGE_GAP / 2 short of a plumb plane HEAD_BACK inside the drop's line
    trays = _section(out, "Roof", "plank")
    plane = outside + HEAD_CLEAR - HEAD_BACK
    assert max(h for h, _z in trays) == pytest.approx(plane - RIDGE_GAP / 2.0, abs=1.5)
    assert [m for m in out["geometry"] if m["name"].endswith("Ridge Upstand")]
    # so the roof runs on over the wall's build-up, battens and all
    assert max(h for h, _z in _section(out, "Roof", "batten")) > 0.5 * (plane - 30.0) or plane < 30.0
    # the cap covers HEAD_COVER of the roof, down the slope from the plane; the strips under it and behind its drop
    rest = info["Roof"]["total_depth"] + 25.0 + 2.0                # on the seams: the cap's own line is that far off the roof's face
    assert min(h for h, _z in sec) == pytest.approx(plane - HEAD_COVER * c - rest * math.sin(math.radians(pitch)), abs=3.0)
    fillers = [m for m in out["geometry"] if m["ifc_type"] == "ridge_filler"]
    assert [m for m in fillers if m["name"] == "Roof Head Verge Filler"] and len(fillers) > 5
    behind = _section(out, "Roof", "ridge_filler", lambda m: m["name"] == "Roof Head Verge Filler")
    assert min(h for h, _z in behind) >= outside and max(h for h, _z in behind) <= outside + HEAD_CLEAR
    head = info["Roof"]["head"]
    assert head["over"] == "Wall" and head["drop"] == HEAD_DROP and head["standard"] == (head["width"] <= RIDGE_LEG + 0.5)
    assert head["run"] == pytest.approx(max(0.0, outside + HEAD_CLEAR - HEAD_BACK) / c, abs=0.5)


def test_it_is_built_over_a_picked_wall_or_where_the_chain_asks_for_it():
    _l, _p, out = _build()
    assert [m["name"] for m in out["geometry"] if m["ifc_type"] == "ridge_cap"] == ["Roof Head Verge Cap"]
    _l, _p, none = _build(head="none")
    assert not [m for m in none["geometry"] if m["ifc_type"] in ("ridge_cap", "ridge_filler")] and "head" not in none["info"][1]
    _l, _p, alone = _build(picked=False)                              # the wall not picked: nothing, by default
    assert not [m for m in alone["geometry"] if m["ifc_type"] == "ridge_cap"]
    _l, _p, asked = _build(picked=False, head="g3")                  # asked for on every level top edge
    (cap,) = [m for m in asked["geometry"] if m["ifc_type"] == "ridge_cap"]
    sec = _section(asked, "Roof", "ridge_cap")
    assert max(h for h, _z in sec) == pytest.approx(HEAD_CLEAR, abs=1.0), "in front of the roof's own edge"
    assert min(z for _h, z in sec) == pytest.approx(H - HEAD_DROP, abs=1.0), "and down past it"
    assert asked["info"][0]["head"]["over"] is None and asked["info"][0]["head"]["run"] == 0.0


def test_a_slope_with_a_ridge_has_no_head_verge():
    from cladding_head import heads_of
    from cladding_constants import _parse
    wall, roof = _pair()
    p = _parse(dict(SEAM))
    assert heads_of(dict(roof, head="g3"), p) and not heads_of(dict(roof, head="g3", ridge={"with": "B", "v": 1.0, "u0": 0, "u1": 1, "k": 1}), p)
    assert not heads_of(dict(roof, head="g3"), _parse(dict(PANEL))), "trays that run with the fall only"


def test_the_check_says_what_it_is_and_when_the_cap_is_wider_than_the_g3():
    _l, params, out = _build(15.0, wall=dict(PANEL))
    (row,) = [r for r in check_rules(params, out["info"]) if r["name"].endswith("Head verge")]
    head = out["info"][1]["head"]
    assert ("warn" if not head["standard"] else "pass") == row["status"] and "G3 head verge 6.00 m long over Wall" in row["message"]
    deep = dict(SEAM, insulation=True, insulation_t=150)
    _l, params, out = _build(15.0, wall=deep)
    (row,) = [r for r in check_rules(params, out["info"]) if r["name"].endswith("Head verge")]
    assert row["status"] == "warn" and "purpose-made head flashing" in row["message"]
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    assert [e.Name for e in ifc.by_type("IfcCovering") if "Head Verge Cap" in e.Name] == ["Roof Head Verge Cap"]
    assert len([e for e in ifc.by_type("IfcElement") if not e.is_a("IfcElementAssembly")]) == len(own_elements(out["geometry"]))
