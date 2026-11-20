"""The head-wall abutment: a roof slope run up to a wall that stands on above it. After
VMZINC's "G3 head verge wall abutment" (a flashing turned up the wall) and "Head wall
abutment below cladding" (the trays turned up behind a standing seam wall's cladding)."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import ifcopenshell  # noqa: E402

from cladding_abut import (ABUT_CHASE, ABUT_COVER, ABUT_EDGE, ABUT_HOOK, ABUT_LAP, ABUT_PLY, ABUT_SADDLE, ABUT_UP, ABUT_UPSTAND,  # noqa: E402
                           abuts_of)
from cladding_checks import check_rules  # noqa: E402
from cladding_constants import _parse, frame_to_world  # noqa: E402
from cladding_head import HEAD_COVER  # noqa: E402
from cladding_nesting import chain_pieces  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from cladding_primitives import ring_at  # noqa: E402
from cladding_ridge import RIDGE_GAP  # noqa: E402
from fabric_extract import abut_link, eaves_link, extract_elevation, head_link, heal_link, valley_links  # noqa: E402
from ifc_generator import meshes_to_ifc, own_elements  # noqa: E402

SEAM = dict(cladding_type="plank", plank_profile="seam", plank_orient="vertical")
PANEL = dict(cladding_type="panel")
HR, HW = 2700.0, 2400.0            # the roof's top edge up the wall, and the wall above it


def _face(name, pts, out, context=()):
    quad = lambda q: [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]                                # noqa: E731
    return extract_elevation({"name": name, "faces": quad(pts), "outward": list(out), "context": list(context),
                              "options": {"debug": False, "clad_depth": 150.0}})


def _box(name, x0, y0, z0, x1, y1, z1, kind="IfcWall"):
    c = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    quads = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return {"type": kind, "name": name, "tris": [[list(c[a]), list(c[b]), list(c[d])] for f in quads
                                                 for a, b, d in ((f[0], f[1], f[2]), (f[0], f[2], f[3]))]}


def _pair(pitch=15.0, through=False, context=(), wide=0.0):
    """A wall along x that faces -y, and a lean-to roof in front of it whose top edge lies on
    the wall's face HR up. *through*: the wall's face runs on down behind the roof. *wide*:
    the wall runs this far on past each end of the roof."""
    t, s, c = math.tan(math.radians(pitch)), math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    z0 = 0.0 if through else HR
    wall = _face("Wall", [[-wide, 0, z0], [6000 + wide, 0, z0], [6000 + wide, 0, HR + HW], [-wide, 0, HR + HW]], (0, -1, 0))
    roof = _face("Roof", [[0, 0, HR], [0, -3000, HR - 3000 * t], [6000, -3000, HR - 3000 * t], [6000, 0, HR]], (0, -s, c), context)
    return wall, roof


def _build(pitch=15.0, wall=None, picked=True, abutment=None, through=False, context=(), wide=0.0, roof=None, **kw):
    w, r = _pair(pitch, through, context, wide)
    link = abut_link(w, r)
    rec = lambda side, other: dict(link[side], **{"with": other, "k": link["k"], "master": side == "b"})    # noqa: E731
    slope = dict(r, offset=0, chain="Chain 2", settings=dict(SEAM), valleys=[rec("b", "Wall")] if picked else [], verge="none", eaves="none")
    slope.update(roof or {})
    if abutment:
        slope["abutment"] = abutment
    elevs = ([dict(w, offset=0, chain="Chain 1", settings=wall or dict(SEAM), valleys=[rec("a", "Roof")])] if picked else []) + [slope]
    params = dict({"elevations": elevs, "trim": True, "splash": 0}, **kw)
    return link, params, generate_preview(params)


def _section(out, pick):
    """Corners of the chosen parts in section: (out from the wall's face, height above the
    line the roof's surface meets it on)."""
    pts = []
    for m in out["geometry"]:
        if pick(m):
            near = float(m["depth"])
            pts += [frame_to_world(m["frame"], u, v, d) for d in (near, near + float(m["thickness"])) for u, v in ring_at(m, 0, d)]
    return [(-q[1], q[2] - HR) for q in pts]


def _kind(kind, face="Roof"):
    return lambda m: m["elevation"] == face and m["ifc_type"] == kind


def _named(text):
    return lambda m: text in m["name"]


def test_a_roof_run_up_to_a_wall_that_stands_on_above_it_is_an_abutment():
    wall, roof = _pair()
    link = abut_link(wall, roof)
    assert link["kind"] == "abut" and link["a"]["roof"] is False and link["b"]["roof"] is True and link["length"] == 6000.0
    assert link["b"]["p1"][1] == link["b"]["p2"][1] == pytest.approx(roof["height"], abs=0.1)      # the roof's top edge
    assert link["a"]["p1"][1] == link["a"]["p2"][1] == pytest.approx(0.0, abs=0.1)                  # the foot of the wall as picked
    assert eaves_link(wall, roof) is None and head_link(wall, roof) is None and heal_link(wall, roof) is None
    assert [k and k["kind"] for k in valley_links(wall, [roof])] == ["abut"]
    # the wall's face may run on down behind the roof: the line is where the roof meets it
    wall, roof = _pair(through=True)
    link = abut_link(wall, roof)
    assert link["kind"] == "abut" and link["a"]["p1"][1] == pytest.approx(HR, abs=0.1) and link["length"] == 6000.0
    # a wall that stops at the roof's top edge is not stood on above it; nor is a roof that falls away behind the wall
    t = math.tan(math.radians(15.0))
    low = _face("Low", [[0, 0, 0], [6000, 0, 0], [6000, 0, HR], [0, 0, HR]], (0, -1, 0))
    assert abut_link(low, roof) is None
    behind = _face("Behind", [[6000, 0, HR], [0, 0, HR], [0, 3000, HR - 3000 * t], [6000, 3000, HR - 3000 * t]], (0, 0.2588, 0.9659))
    assert abut_link(low, behind) is None and head_link(low, behind)["kind"] == "head"


def test_a_wall_of_the_model_that_stands_over_the_roof_is_found_though_it_is_not_picked():
    ctx = [_box("High wall", 0, 0, 0, 6000, 300, 6000), _box("Low wall", 0, -3000, 0, 6000, -2700, 1800),
           _box("Side wall", -300, -3000, 0, 0, 0, 6000), _box("Slab", 0, 0, 0, 6000, 300, 6000, "IfcSlab")]
    _wall, roof = _pair(context=ctx[:3])
    high = [w for w in roof["walls_above"] if w["name"] == "High wall"]
    assert len(high) == 1 and high[0]["u0"] == 0.0 and high[0]["u1"] == 6000.0 and high[0]["v"] == pytest.approx(roof["height"], abs=1.0)
    assert not [w for w in roof["walls_above"] if w["name"] == "Low wall"], "the wall the roof stands on does not rise over it"
    p = _parse(dict(SEAM))
    (h,) = abuts_of(roof, p)
    assert h["kind"] == "g3" and h["over"] is None and h["wall"] == "High wall"
    assert not abuts_of(dict(roof, walls_above=[w for w in roof["walls_above"] if w["name"] == "Side wall"]), p), "a wall along its side is no head wall"
    assert not abuts_of(dict(roof, abutment="none"), p) and not abuts_of(roof, _parse(dict(PANEL))), "trays that run with the fall only"
    assert not abuts_of(dict(roof, ridge={"with": "B", "v": 1.0, "u0": 0, "u1": 1, "k": 1}), p)


@pytest.mark.parametrize("pitch", [5.0, 15.0, 30.0])
def test_under_boards_or_panels_a_g3_flashing_turns_up_the_wall_and_the_wall_stops_over_it(pitch):
    _link, _params, out = _build(pitch, wall=dict(PANEL))
    info = {i["elevation"]: i for i in out["info"]}
    s, c = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    ab = info["Roof"]["abut"]
    assert ab["kind"] == "g3" and ab["over"] == "Wall" and ab["chase"] is False and ab["standard"] == (ab["width"] <= 185.5)
    # the trays stop RIDGE_GAP / 2 short of the wall and are turned up, as at a ridge
    assert min(x for x, _z in _section(out, _kind("plank"))) == pytest.approx(RIDGE_GAP / 2.0, abs=1.5)
    assert len([m for m in out["geometry"] if m["name"].endswith("Ridge Upstand")]) > 5
    # the flashing: one length, against the wall, HEAD_COVER down the slope, ABUT_UP up the wall above its corner
    (cap,) = [m for m in out["geometry"] if m["ifc_type"] == "ridge_cap"]
    assert cap["name"] == "Roof Abutment Flashing" and cap["thickness"] == 6000.0
    sec = _section(out, _kind("ridge_cap"))
    rest = info["Roof"]["total_depth"] + 25.0 + 2.0                    # on the seams
    assert min(x for x, _z in sec) == pytest.approx(0.0, abs=0.05), "against the wall's face"
    corner = rest / c                                                  # where its line on the seams reaches the wall
    assert max(z for _x, z in sec) == pytest.approx(corner + ABUT_UP, abs=1.0)
    assert max(x for x, _z in sec) == pytest.approx(HEAD_COVER * c + rest * s, abs=3.0)
    assert len([m for m in out["geometry"] if m["ifc_type"] == "ridge_filler"]) > 5, "the decompression strips between the seams"
    # the wall: every layer stops level, ABUT_LAP under the flashing's top
    over = info["Wall"]["abut_over"][0]
    assert over["kind"] == "g3" and over["stop"] == pytest.approx(corner + ABUT_UP - ABUT_LAP, abs=1.0)
    for kind in ("panel", "batten"):
        assert min(z for _x, z in _section(out, _kind(kind, "Wall"))) == pytest.approx(over["stop"], abs=0.2), kind
    assert not [m for m in out["geometry"] if "Edge Flashing" in m["name"] or "Abutment Upstand" in m["name"]]


def test_against_a_wall_that_is_not_picked_the_flashing_s_top_is_turned_into_a_chase():
    ctx = [_box("High wall", 0, 0, 0, 6000, 300, 6000)]
    _l, params, out = _build(picked=False, context=ctx)
    ab = out["info"][0]["abut"]
    assert ab["kind"] == "g3" and ab["wall"] == "High wall" and ab["over"] is None and ab["chase"] is True
    sec = _section(out, _kind("ridge_cap"))
    assert min(x for x, _z in sec) == pytest.approx(-ABUT_CHASE, abs=0.5), "into the wall"
    (row,) = [r for r in check_rules(params, out["info"]) if r["name"].endswith("Wall abutment")]
    assert row["status"] == "pass" and "a wall that is not picked (High wall)" in row["message"] and "chase" in row["message"]
    # no wall found and none picked: nothing, unless the chain asks for it on every level top edge
    _l, _p, alone = _build(picked=False)
    assert not [m for m in alone["geometry"] if m["ifc_type"] == "ridge_cap"] and "abut" not in alone["info"][0]
    _l, _p, asked = _build(picked=False, abutment="g3")
    assert [m["name"] for m in asked["geometry"] if m["ifc_type"] == "ridge_cap"] == ["Roof Abutment Flashing"]
    assert asked["info"][0]["abut"]["chase"] is True and asked["info"][0]["abut"]["wall"] is None
    # and it takes the place of a head verge asked for on every level top edge
    _l, _p, both = _build(picked=False, abutment="g3", roof={"head": "g3"})
    assert [m["name"] for m in both["geometry"] if m["ifc_type"] == "ridge_cap"] == ["Roof Abutment Flashing"] and "head" not in both["info"][0]


@pytest.mark.parametrize("pitch", [5.0, 15.0, 30.0])
@pytest.mark.parametrize("through", [False, True])
def test_under_standing_seam_the_trays_turn_up_behind_the_wall_s_cladding(pitch, through):
    _link, _params, out = _build(pitch, wall=dict(SEAM), through=through)
    info = {i["elevation"]: i for i in out["info"]}
    t_, c = math.tan(math.radians(pitch)), math.cos(math.radians(pitch))
    ab = info["Roof"]["abut"]
    assert ab["kind"] == "below" and ab["over"] == "Wall" and ab["upstand"] == ABUT_UPSTAND
    sheet = 0.6
    line = ABUT_PLY + sheet                                         # the upstand's outer face, off the wall
    # the trays run up to the upstand, on the discontinuous plywood
    trays = _section(out, _kind("plank"))
    assert min(x for x, _z in trays) == pytest.approx(line, abs=1.0)
    tray = info["Roof"]["total_depth"] / c - line * t_              # the trays' top there, above the line
    ups = [m for m in out["geometry"] if m["name"].endswith("Abutment Upstand")]
    assert len(ups) == ab["trays"] > 5 and not [m for m in out["geometry"] if m["name"].endswith("Ridge Upstand")]
    assert all(m["of"] == m["name"][:-len(" Abutment Upstand")] and m["tray_sheet"] and m["ifc_type"] == "seam" for m in ups)
    assert sum(m["thickness"] for m in ups) == pytest.approx(6000.0, abs=1.0), "one to a tray, the whole length between them"
    sec = _section(out, _named("Abutment Upstand"))
    assert max(z for _x, z in sec) == pytest.approx(tray + ABUT_UPSTAND, abs=1.0)
    assert min(x for x, _z in sec) == pytest.approx(ABUT_PLY, abs=0.1) and min(z for _x, z in sec) == pytest.approx(tray - 1.5 * sheet, abs=1.0)
    back = _section(out, _named("Abutment Backing"))
    assert (min(x for x, _z in back), max(x for x, _z in back)) == pytest.approx((0.0, ABUT_PLY), abs=0.05)
    assert max(z for _x, z in back) >= tray + ABUT_UPSTAND
    # a saddle piece over the end of every seam, cut plumb at the upstand
    assert ab["saddles"] == len({m["abut_saddle"] for m in out["geometry"] if m.get("abut_saddle")}) == ab["trays"] - 1
    sad = _section(out, lambda m: m.get("abut_saddle") == 1)
    assert min(x for x, _z in sad) == pytest.approx(line, abs=0.5)
    run = max(x for x, _z in sad) - min(x for x, _z in sad)
    assert ABUT_SADDLE * c - 1.0 <= run <= ABUT_SADDLE * c + 30.0 * math.sin(math.radians(pitch)) + 1.0
    assert not [m for m in out["geometry"] if m["ifc_type"] == "ridge_filler" or "Abutment Flashing" in m["name"]]
    # the wall: every layer stops level, its trays lapping the upstand by ABUT_COVER; nothing of it below, behind the roof
    over = info["Wall"]["abut_over"][0]
    assert over["kind"] == "below" and over["stop"] == pytest.approx(tray + ABUT_UPSTAND - ABUT_COVER, abs=0.5)
    for kind in ("plank", "seam", "deck", "batten"):
        assert min(z for _x, z in _section(out, _kind(kind, "Wall"))) == pytest.approx(over["stop"], abs=0.2), kind
    # room behind the wall's plywood for the upstand and the vent
    deck = min(x for x, _z in _section(out, _kind("deck", "Wall")))
    assert ab["room"] == pytest.approx(deck - line, abs=0.1) and ab["room"] >= 10.0
    # and the edge flashing on that plywood, at the foot of the wall's trays
    (edge,) = [m for m in out["geometry"] if m.get("abut_edge")]
    es = _section(out, lambda m: m.get("abut_edge"))
    assert edge["name"] == "Wall Abutment Edge Flashing" and edge["thickness"] == 6000.0
    assert max(z for _x, z in es) == pytest.approx(over["stop"] + ABUT_EDGE, abs=0.6) and min(z for _x, z in es) < over["stop"]
    assert min(x for x, _z in es) == pytest.approx(max(x for x, _z in _section(out, _kind("deck", "Wall"))), abs=0.05)


def test_a_wall_wider_than_the_roof_runs_on_down_beside_it():
    _l, _p, out = _build(wall=dict(SEAM), through=True, wide=1200.0)
    stop = out["info"][0]["abut_over"][0]["stop"]
    lows = {}
    for m in out["geometry"]:
        if m["elevation"] == "Wall" and m["ifc_type"] == "plank":
            us = [frame_to_world(m["frame"], u, v, 0.0)[0] for u, v in m["profile"]]
            zs = [frame_to_world(m["frame"], u, v, 0.0)[2] for u, v in m["profile"]]
            lows[round(sum(us) / len(us))] = min(min(zs), lows.get(round(sum(us) / len(us)), 1e9))
    beside = [z for x, z in lows.items() if x < -200 or x > 6200]
    over = [z for x, z in lows.items() if 300 < x < 5700]
    assert beside and max(beside) < 50.0, "to the ground, beside the roof"
    assert over and min(over) == pytest.approx(HR + stop, abs=0.5), "and stopped over it"


def test_the_trays_are_cut_longer_for_the_upstand_and_it_is_part_of_its_tray():
    _l, params, out = _build(wall=dict(SEAM))
    ab = out["info"][1]["abut"]
    assert ab["sheet"] == pytest.approx(ABUT_UPSTAND + ABUT_HOOK + 1.2, abs=0.01)
    trays = [m for m in out["geometry"] if m["elevation"] == "Roof" and m["ifc_type"] == "plank"]
    assert trays and all(m["cut_extra"] == ab["sheet"] for m in trays)
    (piece,) = [q for q in chain_pieces(out["geometry"], ["Roof"], "plank") if q["name"] == trays[0]["name"]]
    bare = dict(trays[0], cut_extra=0.0)
    (plain,) = chain_pieces([bare], ["Roof"], "plank")
    assert piece["h"] == pytest.approx(plain["h"] + ab["sheet"], abs=0.2)
    # the eaves at the other end adds its own turn to the same tray
    _l, _p, both = _build(wall=dict(SEAM), roof={"eaves": "on"})
    tray = next(m for m in both["geometry"] if m["elevation"] == "Roof" and m["ifc_type"] == "plank")
    assert tray["cut_extra"] > ab["sheet"] + 20.0
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    assert not [e for e in ifc.by_type("IfcElement") if "Abutment Upstand" in (e.Name or "")], "in its tray's element"
    assert len([e for e in ifc.by_type("IfcElement") if not e.is_a("IfcElementAssembly")]) == len(own_elements(out["geometry"]))
    types = {e.Name: e.ObjectType for e in ifc.by_type("IfcElement") if "Abutment" in (e.Name or "")}
    assert types["Wall Abutment Edge Flashing"] == "Edge flashing" and types["Roof Abutment Backing"] == "Discontinuous plywood"
    assert types["Roof Abutment Saddle 1 Top"] == "Saddle piece"


def test_the_chain_s_setting_chooses_the_detail():
    _l, params, out = _build(wall=dict(SEAM), abutment="g3")          # the flashing, though the wall is standing seam
    assert out["info"][1]["abut"]["kind"] == "g3" and out["info"][0]["abut_over"][0]["kind"] == "g3"
    (row,) = [r for r in check_rules(params, out["info"]) if r["name"].endswith("Wall abutment") and "Roof:" in r["message"]]
    assert "The wall above is standing seam" in row["message"]
    _l, _p, out = _build(wall=dict(PANEL), abutment="below")           # only under standing seam: the flashing here
    assert out["info"][1]["abut"]["kind"] == "g3"
    _l, _p, out = _build(wall=dict(SEAM), abutment="none")
    assert "abut" not in out["info"][1] and "abut_over" not in out["info"][0]
    assert not [m for m in out["geometry"] if "Abutment" in m["name"]]
    assert min(z for _x, z in _section(out, _kind("plank", "Wall"))) < 20.0, "the wall is left as it is"


def test_the_checks_say_what_was_built_and_when_the_wall_s_cavity_is_too_shallow():
    _l, params, out = _build(wall=dict(SEAM))
    rows = [r for r in check_rules(params, out["info"]) if r["name"].endswith("Wall abutment")]
    assert len(rows) == 2 and all(r["status"] == "pass" for r in rows)
    text = " ".join(r["message"] for r in rows)
    assert "a head-wall abutment below cladding 6.00 m long, under Wall" in text and "lap the roof's upstand by 60 mm" in text
    _l, params, out = _build(wall=dict(SEAM, batten_d=25))
    (row,) = [r for r in check_rules(params, out["info"]) if r["name"].endswith("Wall abutment") and "Roof:" in r["message"]]
    assert row["status"] == "warn" and "too shallow" in row["message"]
    _l, params, out = _build(wall=dict(PANEL))
    rows = [r for r in check_rules(params, out["info"]) if r["name"].endswith("Wall abutment")]
    assert len(rows) == 2 and "a G3 head-wall abutment 6.00 m long against Wall" in " ".join(r["message"] for r in rows)


def test_a_splash_zone_over_the_roof_is_kept_under_boards_and_dropped_under_standing_seam():
    def with_abutment(wall):
        w, r = _pair()
        link = abut_link(w, r)
        rec = lambda side, other: dict(link[side], **{"with": other, "k": link["k"], "master": side == "b"})    # noqa: E731
        roof = dict(r, offset=0, chain="Chain 2", settings=dict(SEAM), valleys=[rec("b", "Wall")], verge="none", eaves="none")
        face = dict(w, offset=0, chain="Chain 1", settings=wall, valleys=[rec("a", "Roof")],
                    abutments=[{"line": [[0.0, 0.0], [6000.0, 0.0]], "source": "IfcRoof", "name": "Roof", "enabled": True}])
        return generate_preview({"elevations": [face, roof], "trim": True, "splash": 150})
    panels = with_abutment(dict(PANEL))
    assert min(z for _x, z in _section(panels, _kind("panel", "Wall"))) >= 150.0 - 0.5
    seam = with_abutment(dict(SEAM))
    stop = seam["info"][0]["abut_over"][0]["stop"]
    assert stop < 150.0 and min(z for _x, z in _section(seam, _kind("plank", "Wall"))) == pytest.approx(stop, abs=0.5)
