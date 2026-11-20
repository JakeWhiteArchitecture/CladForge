"""The eaves: the level bottom edge of a roof slope in standing seam. The trays and their
seams run out over an eaves apron, the layers under them run to the roof's edge, and the
chain can switch it off. No drawing was supplied for it: its sizes are assumptions."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import ifcopenshell  # noqa: E402

from cladding_checks import check_rules  # noqa: E402
from cladding_constants import frame_to_world  # noqa: E402
from cladding_eaves import (EAVES_TRAY, EAVES_NOSE, EAVES_LAP, EAVES_DROP, EAVES_CLEAR, EAVES_KICK, EAVES_OVER, EAVES_WALL_LAP,  # noqa: E402
                            apron_drop, wall_stop, has_eaves)
from cladding_constants import _parse  # noqa: E402
from cladding_primitives import ring_at  # noqa: E402
from dxf_generator import meshes_to_dxf_string  # noqa: E402
from ifc_generator import meshes_to_ifc  # noqa: E402
from test_hip import _build as _hipped, _pair, SEAM  # noqa: E402
from test_verge import _one  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from fabric_extract import extract_elevation, eaves_link, valley_links  # noqa: E402


def _aprons(out):
    return [m for m in out["geometry"] if m["ifc_type"] == "eaves_strip"]


def _bottoms(out, kind, face=None):
    return sorted({round(min(q[1] for q in m["profile"]), 1) for m in out["geometry"]
                   if m["ifc_type"] == kind and (face is None or m["elevation"] == face) and not m.get("hip_part")})


def test_the_trays_run_out_over_an_apron_and_the_layers_under_run_to_the_edge():
    _params, out = _one(elev={"verge": "none"})
    _p0, plain = _one(elev={"verge": "none", "eaves": "none"})
    (apron,) = _aprons(out)
    assert not _aprons(plain)
    assert _bottoms(plain, "plank")[0] == 10.0 and _bottoms(plain, "deck") == [10.0]       # the bottom edge offset
    assert _bottoms(out, "plank")[0] == -EAVES_TRAY and _bottoms(out, "deck") == [0.0]
    assert _bottoms(out, "seam")[0] == -EAVES_TRAY and _bottoms(out, "batten")[0] == 0.0
    # nothing else moved: the same parts, the same tops
    assert [m["name"] for m in out["geometry"] if m["ifc_type"] != "eaves_strip"] == [m["name"] for m in plain["geometry"]]
    tops = lambda o: [max(q[1] for q in m["profile"]) for m in o["geometry"] if m["ifc_type"] == "plank"]   # noqa: E731
    assert tops(out) == tops(plain)
    info = out["info"][0]["eaves"][0]
    assert info["tray"] == EAVES_TRAY and info["length"] == pytest.approx(out["info"][0]["width"], abs=1.0)
    assert apron["thickness"] == pytest.approx(info["length"])


def test_the_apron_lies_on_the_deck_under_the_trays_and_drops_clear_of_the_edge():
    _params, out = _one(elev={"verge": "none"})
    (apron,) = _aprons(out)
    face = [m for m in out["geometry"] if m["ifc_type"] == "plank"][0]
    elev_frame = face["frame"]
    pts = [frame_to_world(apron["frame"], u, v, s) for s in (0.0, apron["thickness"]) for u, v in ring_at(apron, 0, s)]
    local = [[sum((q[i] - elev_frame["origin"][i]) * axis[i] for i in range(3)) for axis in
              (elev_frame["u"], elev_frame.get("v") or [0, 0, 1], elev_frame["n"])] for q in pts]
    vs, ds = [q[1] for q in local], [q[2] for q in local]
    assert max(vs) == pytest.approx(EAVES_LAP, abs=1.0) and max(ds) <= face["depth"] + 0.01          # on the deck, under the trays
    nose = min(q[1] for q in local if q[2] > face["depth"] - 3.0)
    assert nose == pytest.approx(-EAVES_NOSE, abs=1.0) and -EAVES_NOSE < -EAVES_TRAY + 10.0          # the trays' ends lap the nose
    # The drop hangs plumb in front of the edge of the roof's own surface and runs down past it:
    # seen in section, out from that edge on plan and up from it.
    n = elev_frame["n"]
    c, s = n[2], (1.0 - n[2] ** 2) ** 0.5
    section = [(-v * c + d * s, v * s + d * c) for _u, v, d in local]
    assert min(z for _h, z in section) == pytest.approx(-(EAVES_DROP + EAVES_KICK), abs=1.0)         # below the edge, with its drip
    t = apron["eaves_info"]["thickness"]
    out, below = apron_drop(face["depth"] - 1.0, t, math.degrees(math.acos(c)))
    plumb = [z for h, z in section if abs(h - out) <= t]                                             # the drop's own corners
    assert min(plumb) <= -EAVES_DROP + 1.0 and max(plumb) > 0.0, "one plumb line from under the nose to below the edge"
    assert out - t / 2.0 >= EAVES_CLEAR and below == EAVES_DROP                                      # clear of the edge
    assert out > (face["depth"] - 1.0) * s, "past the build-up's end, which leans out over the edge"
    assert apron["eaves_info"]["drop"] == EAVES_DROP and apron["eaves_info"]["over"] is None


def test_only_a_roof_slope_in_trays_that_run_with_the_fall_has_an_eaves():
    seam = _parse(dict(SEAM))
    roof = {"pitch": 30.0}
    assert has_eaves(roof, seam) and not has_eaves(dict(roof, eaves="none"), seam)
    assert not has_eaves({"pitch": 90.0}, seam) and not has_eaves({}, seam)             # a wall
    assert not has_eaves(dict(roof, faces_down=True), seam)                              # a soffit
    assert not has_eaves(roof, _parse(dict(SEAM, plank_orient="horizontal")))
    assert not has_eaves(roof, _parse(dict(cladding_type="panel")))


def test_at_a_hip_the_eaves_ends_follow_the_hip_cut():
    """Each slope of a hipped corner has its eaves; a tray cut to the hip carries that cut on
    down its overhang, and only a level bottom edge is an eaves: the hip is not one."""
    _link, params, out = _hipped(elev={"eaves": "on"}, **SEAM)
    assert sorted(m["name"] for m in _aprons(out)) == ["Roof A Eaves Apron 1", "Roof C Eaves Apron 1"]
    for face in ("Roof A", "Roof C"):
        assert _bottoms(out, "plank", face) == [-EAVES_TRAY]
    corner = min((m for m in out["geometry"] if m["ifc_type"] == "plank" and m["elevation"] == "Roof A"),
                 key=lambda m: min(q[0] for q in m["profile"]))
    low = min(corner["profile"], key=lambda q: (q[1], q[0]))
    assert low[1] == -EAVES_TRAY and low[0] < 0.0                 # on past the corner, down the line of the hip
    rows = [r for r in check_rules(params, out["info"]) if r["name"].endswith("Eaves")]
    assert len(rows) == 2 and all(r["status"] == "pass" and "sizes are assumed" in r["message"] for r in rows)
    _l, _p, off = _hipped(**SEAM)                                  # the chain's eaves switched off
    assert not _aprons(off) and not [r for r in check_rules(_p, off["info"]) if r["name"].endswith("Eaves")]


def test_an_eaves_exports():
    _params, out = _one(elev={"verge": "none"})
    params = _params
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    (apron,) = [e for e in ifc.by_type("IfcCovering") if "Eaves Apron" in e.Name]
    assert apron.PredefinedType == "ROOFING" and apron.ObjectType == "Eaves apron"
    assert out["info"][0]["eaves_strips"] and "CORNER_PROFILE" in meshes_to_dxf_string(out["geometry"], params, out["info"])


# ── an eaves on a wall, with no overhang (DL-41: the roof comes down to the head of a clad wall) ──

def _flush(pitch=30.0, wall=None, roof=None, wall_x=(0.0, 6000.0), **kw):
    """A wall along x, 2.7 m high, and a roof slope whose bottom edge is the wall's top
    edge: a knuckle with no overhang. Each its own chain, with its own settings."""
    t = math.tan(math.radians(pitch))
    s, c = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    quad = lambda q: [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]                                # noqa: E731
    face = lambda name, pts, out: extract_elevation({"name": name, "faces": quad(pts), "outward": list(out), "context": [],   # noqa: E731
                                                     "options": {"debug": False, "clad_depth": 150.0}})
    x0, x1 = wall_x
    w = face("Wall", [[x0, 0, 0], [x1, 0, 0], [x1, 0, 2700], [x0, 0, 2700]], (0, -1, 0))
    r = face("Roof", [[0, 0, 2700], [6000, 0, 2700], [6000, 3000, 2700 + 3000 * t], [0, 3000, 2700 + 3000 * t]], (0, -s, c))
    link = eaves_link(w, r)
    rec = lambda side, other: dict(link[side], **{"with": other, "k": link["k"], "master": side == "b"})    # noqa: E731
    elevs = [dict(w, offset=0, chain="Chain 1", settings=wall or dict(cladding_type="panel"), valleys=[rec("a", "Roof")]),
             dict(r, offset=0, chain="Chain 2", settings=roof or dict(SEAM), valleys=[rec("b", "Wall")], verge="none")]
    params = dict({"elevations": elevs, "trim": True, "splash": 0}, **kw)
    return link, params, generate_preview(params)


def _section(out, face, kind, pick=None):
    """Every corner of the face's parts of this kind, in section: (out from the wall's face
    on plan, height). The wall's face is y = 0 and looks along -y."""
    pts = []
    for m in out["geometry"]:
        if m["elevation"] == face and m["ifc_type"] == kind and (pick is None or pick(m)):
            near = float(m["depth"])
            pts += [frame_to_world(m["frame"], u, v, d) for d in (near, near + float(m["thickness"])) for u, v in ring_at(m, 0, d)]
    return [(-q[1], q[2]) for q in pts]


def test_a_roof_on_the_head_of_a_wall_is_an_eaves_with_no_overhang():
    link, _params, _out = _flush()
    assert link["kind"] == "eaves" and link["length"] == 6000.0 and link["angle"] == 60.0
    assert link["a"]["roof"] is False and link["b"]["roof"] is True
    assert link["a"]["p1"][1] == link["a"]["p2"][1] == 2700.0 and link["b"]["p1"][1] == link["b"]["p2"][1] == 0.0
    quad = lambda q: [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]                                # noqa: E731
    face = lambda name, pts, out: extract_elevation({"name": name, "faces": quad(pts), "outward": list(out), "context": [],   # noqa: E731
                                                     "options": {"debug": False}})
    t, s, c = math.tan(math.radians(30.0)), 0.5, math.cos(math.radians(30.0))
    wall = face("Wall", [[0, 0, 0], [6000, 0, 0], [6000, 0, 2700], [0, 0, 2700]], (0, -1, 0))
    over = face("Over", [[0, -400, 2700 - 400 * t], [6000, -400, 2700 - 400 * t], [6000, 3000, 2700 + 3000 * t], [0, 3000, 2700 + 3000 * t]], (0, -s, c))
    assert eaves_link(wall, over) is None, "a roof that sails on over the wall has its eaves out at its own edge"
    steep = math.radians(70.0)
    mansard = face("Mansard", [[0, 0, 2700], [6000, 0, 2700], [6000, 1000, 2700 + 1000 * math.tan(steep)], [0, 1000, 2700 + 1000 * math.tan(steep)]],
                   (0, -math.sin(steep), math.cos(steep)))
    assert eaves_link(wall, mansard) is None, "steeper than a leaning wall may be, it is a corner on a level line"
    hung = face("Hung", [[0, 0, 0], [6000, 0, 0], [6000, -3000, -3000 * t], [0, -3000, -3000 * t]], (0, -s, c))
    assert eaves_link(wall, hung) is None, "a roof that falls away from the foot of a wall is an abutment"
    assert [k and k["kind"] for k in valley_links(wall, [over, face("Roof", [[0, 0, 2700], [6000, 0, 2700], [6000, 3000, 2700 + 3000 * t],
                                                                              [0, 3000, 2700 + 3000 * t]], (0, -s, c))])] == [None, "eaves"]


@pytest.mark.parametrize("pitch", [20.0, 30.0, 45.0])
@pytest.mark.parametrize("wall", [dict(cladding_type="panel"), dict(SEAM), dict(cladding_type="plank", plank_orient="horizontal")])
def test_the_roof_runs_on_over_the_wall_cladding_and_the_wall_stops_under_it(pitch, wall):
    _link, _params, out = _flush(pitch, wall=wall)
    info = {i["elevation"]: i for i in out["info"]}
    t, c = math.tan(math.radians(pitch)), math.cos(math.radians(pitch))
    depth = info["Wall"]["total_depth"]                           # the outside of the wall's cladding, out from the wall
    # the roof's plywood: its underside's end EAVES_OVER beyond the outside of the wall's cladding, on plan
    deck = _section(out, "Roof", "deck")
    end = min(deck, key=lambda q: q[1])                           # its lowest corner: the underside at its end
    assert end[0] == pytest.approx(depth + EAVES_OVER, abs=0.5)
    soffit = lambda h: end[1] + (end[0] - h) * t                  # noqa: E731 — the height of its underside, h out from the wall
    # the trays run on round the apron's nose, and the battens stay at the roof's own edge
    trays = _section(out, "Roof", "plank")
    assert max(h for h, _z in trays) > max(h for h, _z in deck) + 0.8 * EAVES_TRAY * c
    assert min(z for _h, z in _section(out, "Roof", "batten")) == pytest.approx(2700.0, abs=0.5)
    # every layer of the wall stops at one level: the top offset under that plywood where it is lowest over
    # the wall's build-up, at the outside of the cladding (or under the line the two meet on, if that is lower)
    boards = "panel" if wall["cladding_type"] == "panel" else "plank"
    tops = {kind: max(z for _h, z in _section(out, "Wall", kind)) for kind in ("batten", boards)}
    stop = min(soffit(depth), 2700.0) - 10.0
    assert tops["batten"] == pytest.approx(stop, abs=0.3)
    if wall.get("plank_orient") == "horizontal":      # (a sliver of a top course left with nothing is left out, with its joint)
        assert stop - 10.0 <= tops[boards] <= stop + 0.3
    else:
        assert tops[boards] == pytest.approx(stop, abs=0.3)
    (under,) = info["Wall"]["eaves_under"]
    assert under["with"] == "Roof" and under["stop"] == pytest.approx(stop - 2700.0, abs=0.3) and under["gap"] == 10.0
    assert under["stop"] == pytest.approx(wall_stop(depth, (soffit(0.0) - 2700.0) * c, 10.0, pitch), abs=0.3)
    # the drip: plumb, in front of the wall's cladding, its foot EAVES_WALL_LAP below the top of that cladding
    apron = _section(out, "Roof", "eaves_strip")
    foot = min(z for _h, z in apron)
    assert foot == pytest.approx(stop - EAVES_WALL_LAP - EAVES_KICK, abs=1.0)
    drop = sorted(h for h, z in apron if z < stop - 5.0 and z > foot + EAVES_KICK + 2.0) or [h for h, z in apron if abs(z - (foot + EAVES_KICK)) < 1.0]
    assert min(drop) > depth + 5.0, "clear of the wall's cladding"
    assert min(drop) > max(h for h, _z in _section(out, "Wall", "seam") or [(0, 0)]), "and of its seams"
    (eaves,) = info["Roof"]["eaves"]
    assert eaves["over"] == "Wall" and eaves["run"] > 0.0


def test_only_the_part_of_the_eaves_over_the_wall_runs_on():
    """A roof wider than the wall under it: over the wall the plywood runs on past its
    cladding; beside it the roof's edge is an eaves in the open, as ever."""
    _link, _params, out = _flush(wall_x=(0.0, 3180.0))
    eaves = sorted({i["elevation"]: i for i in out["info"]}["Roof"]["eaves"], key=lambda e: e["u0"])
    assert [(e["u0"], e["u1"], e["over"]) for e in eaves] == [(0.0, 2820.0, None), (2820.0, 6000.0, "Wall")] \
        or [(e["u0"], e["u1"], e["over"]) for e in eaves] == [(0.0, 3180.0, "Wall"), (3180.0, 6000.0, None)]
    assert sorted(e["run"] > 0 for e in eaves) == [False, True]
    assert len(_aprons(out)) == 2


def test_the_check_tells_both_faces():
    _link, params, out = _flush()
    rows = [r["message"] for r in check_rules(params, out["info"]) if r["name"].endswith("Eaves")]
    assert len(rows) == 2 and any("over Wall" in m and "laps it" in m for m in rows) and any("under the eaves of Roof" in m for m in rows)
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    assert len([e for e in ifc.by_type("IfcElement") if not e.is_a("IfcElementAssembly")]) == len(out["geometry"])
