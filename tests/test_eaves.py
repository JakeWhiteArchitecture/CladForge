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
from cladding_constants import SEAM_AIR, frame_to_world, world_to_frame  # noqa: E402
from cladding_eaves import (EAVES_TRAY, EAVES_NOSE, EAVES_LAP, EAVES_DROP, EAVES_CLEAR, EAVES_OVER, EAVES_WALL_LAP, EAVES_HOOK, EAVES_SUPPORT_LEG,  # noqa: E402
                            apron_drop, wall_stop, has_eaves)
from cladding_constants import _parse  # noqa: E402
from cladding_primitives import ring_at  # noqa: E402
from dxf_generator import meshes_to_dxf_string  # noqa: E402
from ifc_generator import meshes_to_ifc, own_elements  # noqa: E402
from test_hip import _build as _hipped, _pair, SEAM  # noqa: E402
from test_verge import _one  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from fabric_extract import extract_elevation, eaves_link, valley_links  # noqa: E402


def _aprons(out):
    """The drips: one length per tray at an eaves, each its tray's own sheet."""
    return [m for m in out["geometry"] if m["ifc_type"] == "eaves_strip" and not m.get("eaves_support")]


def _runs(out):
    """The eaves, as built: one support profile along each."""
    return [m for m in out["geometry"] if m.get("eaves_support") and not m.get("drip_end")]


def _bottoms(out, kind, face=None):
    return sorted({round(min(q[1] for q in m["profile"]), 1) for m in out["geometry"]
                   if m["ifc_type"] == kind and (face is None or m["elevation"] == face) and not m.get("hip_part")})


def test_the_plywood_and_the_trays_run_on_and_the_layers_under_run_to_the_edge():
    _params, out = _one(elev={"verge": "none"})
    _p0, plain = _one(elev={"verge": "none", "eaves": "none"})
    drips = _aprons(out)
    assert not _aprons(plain) and len(_runs(out)) == 1
    assert _bottoms(plain, "plank")[0] == 10.0 and _bottoms(plain, "deck") == [10.0]       # the bottom edge offset
    assert _bottoms(out, "plank")[0] == -EAVES_NOSE and _bottoms(out, "deck") == [-EAVES_NOSE]   # the plywood runs on, the trays with it
    assert _bottoms(out, "seam")[0] == -EAVES_NOSE and _bottoms(out, "batten")[0] == 0.0
    # nothing else moved: the same parts, the same tops
    assert [m["name"] for m in out["geometry"] if m["ifc_type"] != "eaves_strip"] == [m["name"] for m in plain["geometry"]]
    tops = lambda o: [max(q[1] for q in m["profile"]) for m in o["geometry"] if m["ifc_type"] == "plank"]   # noqa: E731
    assert tops(out) == tops(plain)
    info = out["info"][0]["eaves"][0]
    assert info["tray"] == EAVES_TRAY and info["length"] == pytest.approx(out["info"][0]["width"], abs=1.0)
    trays = [m for m in out["geometry"] if m["ifc_type"] == "plank"]
    assert len(drips) == len(trays) and sorted(d["of"] for d in drips) == sorted(m["name"] for m in trays)   # a length of it per tray
    assert all(d["name"] == d["of"] + " Eaves Drip" for d in drips)
    # seam to seam, the eaves long: less the break between one tray's sheet and the next under each seam
    assert sum(d["thickness"] for d in drips) == pytest.approx(info["length"] - (len(trays) - 1) * SEAM_AIR)
    assert _runs(out)[0]["thickness"] == pytest.approx(info["length"])


def _in_section(out, part):
    """A part extruded along the eaves, in section: [(out from the roof's edge on plan, up
    from it)], and the roof's (cos, sin)."""
    face = [m for m in out["geometry"] if m["ifc_type"] == "plank"][0]["frame"]
    pts = [frame_to_world(part["frame"], u, v, s) for s in (0.0, part["thickness"]) for u, v in ring_at(part, 0, s)]
    local = [[sum((q[i] - face["origin"][i]) * axis[i] for i in range(3)) for axis in (face["u"], face.get("v") or [0, 0, 1], face["n"])]
             for q in pts]
    c, s = face["n"][2], (1.0 - face["n"][2] ** 2) ** 0.5
    return [(-v * c + d * s, v * s + d * c) for _u, v, d in local], (c, s)


def test_the_zinc_turns_round_the_end_of_the_plywood_comes_back_under_it_and_drops_into_a_drip():
    """As sketched: the plywood runs on past the drip's line; the trays' sheet goes on
    round its end in a half circle and back under it, then drops plumb over a support
    profile fixed under the plywood, and is hooked back under at the foot."""
    _params, out = _one(elev={"verge": "none"})
    apron = _aprons(out)[0]
    (support,) = [m for m in out["geometry"] if m.get("eaves_support")]
    deck = next(m for m in out["geometry"] if m["ifc_type"] == "deck")
    zinc, (c, s) = _in_section(out, apron)
    prop, _cs = _in_section(out, support)
    top, soffit, t = deck["depth"] + deck["thickness"], deck["depth"], apron["eaves_info"]["thickness"]
    at = lambda x, y: (x * c + y * s, -x * s + y * c)          # noqa: E731 — down the slope from the edge and off the face, as (out, up)
    end, end_low = at(EAVES_NOSE, top), at(EAVES_NOSE, soffit)  # the plywood's end: its top corner and its bottom one
    nose, line, below = apron_drop(soffit, math.degrees(math.acos(c)))
    assert nose == EAVES_NOSE and line == pytest.approx(soffit * s, abs=0.01) and below == EAVES_DROP
    # round the end: it stands out past the plywood's end by about half the plywood's thickness, above and below it
    beyond = [q for q in zinc if q[0] > end[0] + 3.0]
    assert max(q[0] for q in zinc) == pytest.approx(max(end[0], end_low[0]) + (top - soffit) / 2.0 * c + 2.0, abs=6.0)
    assert max(q[1] for q in beyond) - min(q[1] for q in beyond) > 0.5 * (top - soffit) * c, "a half circle, top to bottom of the plywood's end"
    # back under the plywood, to the drop's line
    local = [(h * c - z * s, h * s + z * c) for h, z in zinc]                # down the slope from the edge; off the face
    below_ply = [x for x, y in local if soffit - 5.0 < y < soffit - 1.0]
    assert min(below_ply) < 6.0 and max(below_ply) > EAVES_NOSE - 3.0, "a run of it under the plywood, from its end back to the drop's line"
    # the drop: plumb, on the outside of the support's leg, to EAVES_DROP below the roof's edge and hooked back up behind it
    foot = min(q[1] for q in zinc)
    assert foot == pytest.approx(-EAVES_DROP, abs=4.0)
    low = [q[0] for q in zinc if q[1] < -10.0]
    assert max(low) - min(low) < 6.0 and min(low) == pytest.approx(line, abs=4.0)
    hook = [q for q in zinc if q[1] > foot + EAVES_HOOK - 2.0 and q[1] < foot + EAVES_HOOK + 3.0 and q[0] < line + 1.0]
    assert hook, "hooked back up behind the support's foot"
    # the support: a leg up under the plywood, a leg down behind the zinc's drop
    assert min(q[1] for q in prop) == pytest.approx(-EAVES_DROP, abs=1.0)
    assert min(q[0] for q in prop) < line - 0.7 * EAVES_SUPPORT_LEG * c and max(q[0] for q in prop) > line + 0.5 * EAVES_NOSE * c
    assert apron["eaves_info"]["drop"] == EAVES_DROP and apron["eaves_info"]["over"] is None


def test_the_seams_are_folded_back_at_the_eaves_whatever_the_chain_says():
    for said in ({}, {"seam_fold": False}, {"seam_bottom": "flat"}):
        _params, out = _one(elev=dict({"verge": "none"}, **said))
        seams = [m for m in out["geometry"] if m["ifc_type"] == "seam" and m.get("seam_part") and m.get("seam_v")
                 and min(q[1] for q in m["profile"]) == -EAVES_NOSE]
        assert seams
        for m in seams:
            rows = m["vshift"][0]
            ends = [sh for q, sh in zip(m["profile"], rows) if q[1] == -EAVES_NOSE]
            assert ends and all(sh[3] == pytest.approx(1.0) for sh in ends), "45 degrees: a millimetre back for each millimetre out"
        _p, plain = _one(elev=dict({"verge": "none", "eaves": "none"}, **said))
        if said:                # without the eaves the chain's own choice stands
            assert not any(m.get("vshift") and min(q[1] for q in m["profile"]) < 15.0 and any(abs(sh[3]) > 0 for q, sh in zip(m["profile"], m["vshift"][0]) if q[1] < 15.0)
                           for m in plain["geometry"] if m["ifc_type"] == "seam" and m.get("seam_part") and m.get("seam_v"))


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
    assert sorted(m["name"] for m in _runs(out)) == ["Roof A Eaves Support 1", "Roof C Eaves Support 1"]
    assert {m["elevation"] for m in _aprons(out)} == {"Roof A", "Roof C"}
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
    # the drip is the tray's own sheet: in the tray's element with its seams, not an element of its own
    assert not [e for e in ifc.by_type("IfcElement") if "Eaves Drip" in (e.Name or "")]
    trays = [e for e in ifc.by_type("IfcCovering") if e.ObjectType == "Standing seam tray"]
    built = {k: [m for m in out["geometry"] if m["ifc_type"] == k] for k in ("plank", "seam")}
    assert len(trays) == len(built["plank"])
    # each tray with its seams and its drip is one closed solid (cladding_union), no more than its pieces hold
    from solid_checks import one_closed_solid, volume
    from cladding_union import piece_mesh, _volume
    for e in trays:
        solid = volume(one_closed_solid(e))
        pieces = [m for m in out["geometry"] if m["name"] == e.Name or m.get("of") == e.Name
                  or (m["ifc_type"] == "seam" and m["name"].startswith(e.Name + " "))]
        assert len(pieces) > 1
        whole = sum(_volume(*piece_mesh(m)) for m in pieces)
        assert 0.97 * whole < solid <= whole * (1.0 + 1e-4) + 1.0, (e.Name, solid, whole)
    (support,) = [e for e in ifc.by_type("IfcCovering") if "Eaves Support" in e.Name]
    assert support.PredefinedType == "ROOFING" and support.ObjectType == "Eaves support profile"
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
    mono = face("Mono", [[0, 0, 2700], [6000, 0, 2700], [6000, 3000, 2700 - 3000 * t], [0, 3000, 2700 - 3000 * t]], (0, s, c))
    assert eaves_link(wall, mono) is None, "a roof that falls away behind the head of a wall: a monopitch's head, not its eaves"
    assert [k and k["kind"] for k in valley_links(wall, [over, face("Roof", [[0, 0, 2700], [6000, 0, 2700], [6000, 3000, 2700 + 3000 * t],
                                                                              [0, 3000, 2700 + 3000 * t]], (0, -s, c))])] == [None, "eaves"]


@pytest.mark.parametrize("pitch", [20.0, 30.0, 45.0])
@pytest.mark.parametrize("wall", [dict(cladding_type="panel"), dict(SEAM), dict(cladding_type="plank", plank_orient="horizontal")])
def test_the_roof_runs_on_over_the_wall_cladding_and_the_wall_stops_under_it(pitch, wall):
    _link, _params, out = _flush(pitch, wall=wall)
    info = {i["elevation"]: i for i in out["info"]}
    t, c = math.tan(math.radians(pitch)), math.cos(math.radians(pitch))
    seams = [h for h, _z in _section(out, "Wall", "seam")]
    depth = max(seams + [info["Wall"]["total_depth"]])            # the outside of the wall's cladding (its seams' tips), out from the wall
    # the roof's plywood runs on: EAVES_NOSE down the slope past the drip's line, which is EAVES_OVER
    # beyond the outside of the wall's cladding on plan
    deck = _section(out, "Roof", "deck")
    end = min(deck, key=lambda q: q[1])                           # its lowest corner: the underside at its end
    assert end[0] == pytest.approx(depth + EAVES_OVER + EAVES_NOSE * c, abs=0.5)
    soffit = lambda h: end[1] + (end[0] - h) * t                  # noqa: E731 — the height of its underside, h out from the wall
    # the trays run to the plywood's end, and the battens stay at the roof's own edge
    trays = _section(out, "Roof", "plank")
    assert max(h for h, _z in trays) == pytest.approx(max(h for h, _z in deck), abs=3.0)
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
    apron = _section(out, "Roof", "eaves_strip", lambda m: not m.get("eaves_support"))
    foot = min(z for _h, z in apron)
    assert foot == pytest.approx(stop - EAVES_WALL_LAP, abs=4.0)
    drop = [h for h, z in apron if z < stop - 10.0 and h < depth + EAVES_OVER + 10.0]      # the drop, where it laps the wall's cladding
    assert drop and min(drop) > depth + EAVES_OVER - 4.0, "plumb, clear of the wall's cladding, seams and all (its hook turns back behind)"
    # the zinc turns round the plywood's end and comes back under it; the support profile hangs behind the drop
    assert max(h for h, _z in apron) > end[0] + 5.0
    prop = _section(out, "Roof", "eaves_strip", lambda m: m.get("eaves_support"))
    assert min(z for _h, z in prop) == pytest.approx(stop - EAVES_WALL_LAP, abs=1.5)
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
    assert len(_runs(out)) == 2


def test_the_check_tells_both_faces():
    _link, params, out = _flush()
    rows = [r["message"] for r in check_rules(params, out["info"]) if r["name"].endswith("Eaves")]
    assert len(rows) == 2 and any("over Wall" in m and "laps it" in m for m in rows) and any("under the eaves of Roof" in m for m in rows)
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    assert len([e for e in ifc.by_type("IfcElement") if not e.is_a("IfcElementAssembly")]) == len(own_elements(out["geometry"]))


def test_the_drip_is_the_trays_own_sheet_and_the_cutting_list_has_each_tray_that_much_longer():
    """The drip is the same coil as the roof covering: it starts where the trays' flat ends,
    in their own layer, with nothing lying under them; and each tray at the eaves is cut
    longer by the sheet that goes round the plywood's end and down into the drip."""
    from cladding_nesting import plank_pieces
    params, out = _one(elev={"verge": "none"})
    drip = _aprons(out)[0]
    trays = [m for m in out["geometry"] if m["ifc_type"] == "plank"]
    back, t = min(m["depth"] for m in trays), drip["eaves_info"]["thickness"]
    assert drip.get("tray_sheet") and EAVES_LAP == 0.0 and drip["eaves_info"]["lap"] == 0.0
    a = params["elevations"][0]
    pts = [world_to_frame(a["frame"], frame_to_world(drip["frame"], x, y, 0.0)) for x, y in drip["profile"]]
    foot = min(q[1] for m in trays for q in m["profile"])                    # where the trays' flat ends, down the slope
    upper = [q for q in pts if q[2] > back - 3.0]                            # what of it is up at the trays' level
    assert max(q[1] for q in upper) == pytest.approx(foot, abs=0.6), "it starts at the trays' ends: none of it lies up the roof under them"
    start = [q[2] for q in upper if q[1] > foot - 0.6]
    assert min(start) == pytest.approx(back, abs=0.05) and max(start) == pytest.approx(back + t, abs=0.05), "in the trays' own layer"
    sheet = drip["eaves_info"]["sheet"]
    assert 100.0 < sheet < 260.0
    assert trays and all(m.get("cut_extra") == sheet for m in trays)
    for q in plank_pieces(out["geometry"], {"Roof A"}, "vertical"):
        m = next(m for m in trays if m["name"] == q["name"])
        on_roof = max(p[1] for p in m["profile"]) - min(p[1] for p in m["profile"])
        assert q["length"] == pytest.approx(on_roof + sheet, abs=0.2)
    (row,) = [r["message"] for r in check_rules(params, out["info"]) if r["name"].endswith("Eaves")]
    assert "the trays' own sheet" in row and "cut %.0f mm longer" % sheet in row


# ── the mansard (VMZ-SN-MA-KN-b1): the eaves of a flatter slope over a steeper one ──

def _mansard(lower=70.0, upper=15.0, low=None, up=None):
    """A mansard's lower slope, *lower* degrees, 3 m high along x, and an upper slope of
    *upper* degrees whose bottom edge is the lower's top edge. Each its own chain."""
    quad = lambda q: [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]                                # noqa: E731
    face = lambda name, pts, out: extract_elevation({"name": name, "faces": quad(pts), "outward": list(out), "context": [],   # noqa: E731
                                                     "options": {"debug": False, "clad_depth": 150.0}})
    run, H = 3000.0 / math.tan(math.radians(lower)), 3000.0
    sl, cl, su, cu, tu = (math.sin(math.radians(lower)), math.cos(math.radians(lower)), math.sin(math.radians(upper)),
                          math.cos(math.radians(upper)), math.tan(math.radians(upper)))
    a = face("Lower", [[0, -run, 0], [6000, -run, 0], [6000, 0, H], [0, 0, H]], (0, -sl, cl))
    b = face("Upper", [[0, 0, H], [6000, 0, H], [6000, 3000, H + 3000 * tu], [0, 3000, H + 3000 * tu]], (0, -su, cu))
    link = eaves_link(a, b)
    if link is None:
        return None, None, None
    rec = lambda side, other: dict(link[side], **{"with": other, "k": link["k"], "master": side == "b"})    # noqa: E731
    elevs = [dict(a, offset=0, chain="Chain 1", settings=low or dict(SEAM), valleys=[rec("a", "Upper")], verge="none", eaves="none"),
             dict(b, offset=0, chain="Chain 2", settings=up or dict(SEAM), valleys=[rec("b", "Lower")], verge="none")]
    params = {"elevations": elevs, "trim": True, "splash": 0}
    return link, params, generate_preview(params)


def test_a_flatter_slope_over_a_steeper_one_is_a_mansard_s_eaves():
    for lower in (80.0, 70.0, 45.0, 30.0):
        link, _p, _o = _mansard(lower)
        assert link["kind"] == "eaves" and link["mansard"] is True and link["length"] == 6000.0, lower
        assert link["a"]["roof"] is False and link["b"]["roof"] is True
    assert _mansard(22.0)[0] is None, "less than MANSARD_STEP steeper: a change of pitch, not a mansard"
    link, _p, _o = _flush()
    assert link["mansard"] is False


def test_wall_stop_and_over_wall_lean_back_from_the_upright_case():
    from cladding_eaves import over_wall
    for pitch in (15.0, 30.0):
        t, c = math.tan(math.radians(pitch)), math.cos(math.radians(pitch))
        assert wall_stop(80.0, 60.0, 10.0, pitch) == pytest.approx(min(0.0, 60.0 / c - 80.0 * t) - 10.0)
        run, below = over_wall(80.0, 60.0, 10.0, pitch)
        assert run == pytest.approx((80.0 + EAVES_OVER - 60.0 * math.sin(math.radians(pitch))) / c)
        assert below == pytest.approx(EAVES_WALL_LAP - wall_stop(80.0, 60.0, 10.0, pitch) - run * math.sin(math.radians(pitch)))
    # leaning back, the lower slope's cladding is further out at the drip's foot: the roof runs on further
    assert over_wall(80.0, 60.0, 10.0, 15.0, 70.0)[0] > over_wall(80.0, 60.0, 10.0, 15.0)[0]


@pytest.mark.parametrize("lower", [80.0, 70.0, 45.0])
@pytest.mark.parametrize("low", [dict(SEAM), dict(cladding_type="panel")])
def test_the_upper_slope_runs_on_over_the_lower_and_the_lower_stops_under_it(lower, low):
    _link, params, out = _mansard(lower, low=low)
    info = {i["elevation"]: i for i in out["info"]}
    tu = math.tan(math.radians(15.0))
    deck = _section(out, "Upper", "deck")
    end = min(deck, key=lambda q: q[1])                           # the plywood's underside at its end
    soffit = lambda h: end[1] + (end[0] - h) * tu                 # noqa: E731
    boards = "panel" if low["cladding_type"] == "panel" else "plank"
    lower_pts = [q for kind in ("batten", boards, "seam", "deck", "gasket") for q in _section(out, "Lower", kind)]
    # nothing of the lower slope reaches the upper's plywood: at least its top offset clear (and never past the
    # line the two meet on, where the upper's own layers end)
    assert min(soffit(h) - z for h, z in lower_pts) >= 10.0 - 1.0
    (under,) = info["Lower"]["eaves_under"]
    assert under["with"] == "Upper" and under["mansard"] is True and under["gap"] == 10.0 and under["stop"] <= -10.0 + 0.01
    # the drip: plumb, clear of the lower slope's cladding at its foot, lapping its top by EAVES_WALL_LAP
    apron = _section(out, "Upper", "eaves_strip", lambda m: not m.get("eaves_support"))
    foot = min(z for _h, z in apron)
    top = max(z for _h, z in _section(out, "Lower", boards))      # the top of its sheets or boards, at their outside
    assert foot == pytest.approx(top - EAVES_WALL_LAP, abs=2.5)
    drop = min(h for h, z in apron if z < top - 10.0)
    outside = max(h for h, z in lower_pts if z >= foot - 1.0)     # the lower slope's cladding, as far out as it comes above the foot
    assert drop >= outside + EAVES_OVER - 4.0
    (eaves,) = info["Upper"]["eaves"]
    assert eaves["over"] == "Lower" and eaves["mansard"] is True and eaves["run"] > 0.0
    rows = [r for r in check_rules(params, out["info"]) if r["name"].endswith("Eaves")]
    text = " ".join(r["message"] for r in rows)
    assert "a mansard: the steeper slope below, VMZ-SN-MA-KN-b1" in text and "this is its steeper lower slope" in text


def test_at_a_hip_the_two_slopes_drips_are_mitred_on_the_hips_line_and_the_cap_foot_is_closed():
    """Where an eaves runs into a hip, each slope's drip runs past its own corner and is cut
    on the plumb plane through the hip's line on plan, so the two drips meet; the hip cap's
    foot is closed by a stop end under each leg."""
    from cladding_primitives import ring_at
    _link, params, out = _hipped(elev={"eaves": "on"}, **SEAM)
    by = {e["name"]: e for e in params["elevations"]}
    ends = [m for m in out["geometry"] if m["name"].endswith("Hip End")]
    assert {m["elevation"] for m in ends} == {"Roof A", "Roof C"}
    for m in ends:
        h = next(v for v in by[m["elevation"]]["valleys"] if v["kind"] == "hip")
        (u1, v1), (u2, v2) = h["p1"], h["p2"]
        f = by[m["elevation"]]["frame"]
        a, b = frame_to_world(f, u1, v1, 0.0), frame_to_world(f, u2, v2, 0.0)
        d = (b[0] - a[0], b[1] - a[1])
        size = math.hypot(*d)
        # one end face of the piece lies on the plumb plane through the hip's line on plan
        off = []
        for depth in (0.0, float(m["thickness"])):
            pts = [frame_to_world(m["frame"], x, y, depth) for x, y in ring_at(m, 0, depth)]
            off.append(max(abs((q[0] - a[0]) * d[1] - (q[1] - a[1]) * d[0]) / size for q in pts))
        assert min(off) < 0.5, (m["name"], off)
        assert m["of"]                                                   # one element with its tray (or support)
    stops = sorted(m["name"] for m in out["geometry"] if m.get("stop_end"))
    assert stops == ["Roof A Hip Stop End Roof C", "Roof C Hip Stop End Roof A"]


@pytest.mark.parametrize("lean", [1.0, 2.7, 10.0])
def test_a_wall_leaning_out_under_an_eaves_still_has_the_eaves(lean):
    """A canopy's wall leans out (its head further out than its foot): the roof that comes
    down to its head is still an eaves, with its drip; not a mansard's knuckle."""
    t, s, c = math.tan(math.radians(30.0)), 0.5, math.cos(math.radians(30.0))
    out = math.tan(math.radians(lean)) * 2700.0         # how far out its head stands
    quad = lambda q: [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]                                # noqa: E731
    face = lambda name, pts, n: extract_elevation({"name": name, "faces": quad(pts), "outward": list(n), "context": [],   # noqa: E731
                                                   "options": {"debug": False, "clad_depth": 150.0}})
    a = math.radians(lean)
    w = face("Wall", [[0, 0, 0], [6000, 0, 0], [6000, -out, 2700], [0, -out, 2700]], (0, -math.cos(a), -math.sin(a)))
    r = face("Roof", [[0, -out, 2700], [6000, -out, 2700], [6000, 3000, 2700 + (3000 + out) * t], [0, 3000, 2700 + (3000 + out) * t]], (0, -s, c))
    link = eaves_link(w, r)
    assert link and link["kind"] == "eaves" and not link["mansard"] and link["length"] == pytest.approx(6000.0, abs=1.0)
    rec = lambda side, other: dict(link[side], **{"with": other, "k": link["k"], "master": side == "b"})    # noqa: E731
    elevs = [dict(w, offset=0, chain="Chain 1", settings=dict(cladding_type="panel"), valleys=[rec("a", "Roof")]),
             dict(r, offset=0, chain="Chain 2", settings=dict(SEAM), valleys=[rec("b", "Wall")], verge="none")]
    built = generate_preview({"elevations": elevs, "trim": True, "splash": 0})
    assert _aprons(built), "the drip at the eaves"


def test_a_gently_raking_eaves_still_gets_its_drip():
    """A roof's eaves that follows a wall's raked head (a canopy's leaning wall in a real
    model, about 3.5 degrees across the face): an eaves all the same, each tray turned
    into its drip where it ends, the drip running up the line with the edge."""
    t, s, c = math.tan(math.radians(25.0)), math.sin(math.radians(25.0)), math.cos(math.radians(25.0))
    rise = 360.0                                       # the eaves line on plan, 360 mm up the slope over 6 m
    pts = [[0, 0, 0], [6000, rise, rise * t], [6000, 4000, 4000 * t], [0, 4000, 4000 * t]]
    roof = extract_elevation({"name": "Roof", "faces": [[pts[0], pts[1], pts[2]], [pts[0], pts[2], pts[3]]],
                              "outward": [0, -s, c], "context": [], "options": {"debug": False, "clad_depth": 150.0}})
    out = generate_preview({"elevations": [dict(roof, offset=0, chain="Roof", settings=dict(SEAM), verge="none")],
                            "trim": True, "splash": 0, **SEAM})
    feet = [m for m in out["geometry"] if m["ifc_type"] == "plank" and min(q[1] for q in m["profile"]) < 600.0]   # the trays at the eaves
    drips = _aprons(out)
    assert feet and len(drips) >= len(feet) - 1, "a drip at every bottom tray's foot"
    lows = sorted((min(frame_to_world(m["frame"], u, v, float(m["depth"]))[2] for u, v in ring_at(m, 0, float(m["depth"]))), m["name"])
                  for m in drips)
    assert lows[-1][0] - lows[0][0] > 100.0, "the drip rises with the edge"


def test_an_open_eaves_over_a_wall_runs_on_until_its_drip_clears_the_wall_s_cladding():
    """A roof that oversails a clad wall by less than the wall's cladding stands out (a real
    model's canopy: 31 mm over seams 84 mm out) had its drip behind the seams. Its eaves runs
    on until the drop is EAVES_OVER clear of the cladding's outside; one that oversails far
    enough is left as it is."""
    from cladding_eaves import EAVES_OVER
    t, s, c = math.tan(math.radians(30.0)), 0.5, math.cos(math.radians(30.0))
    quad = lambda q: [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]                                # noqa: E731
    face = lambda name, pts, n: extract_elevation({"name": name, "faces": quad(pts), "outward": list(n), "context": [],   # noqa: E731
                                                   "options": {"debug": False, "clad_depth": 150.0}})
    def build(over):
        w = face("Wall", [[0, 0, 0], [6000, 0, 0], [6000, 0, 2700], [0, 0, 2700]], (0, -1, 0))
        r = face("Roof", [[0, -over, 2700 - over * t], [6000, -over, 2700 - over * t], [6000, 3000, 2700 + 3000 * t],
                          [0, 3000, 2700 + 3000 * t]], (0, -s, c))
        elevs = [dict(w, offset=0, chain="Chain 1", settings=dict(SEAM)),
                 dict(r, offset=0, chain="Chain 2", settings=dict(SEAM), verge="none")]
        return generate_preview({"elevations": elevs, "trim": True, "splash": 0})
    near = build(30.0)
    (e,) = [x for i in near["info"] for x in i.get("eaves", [])]
    assert e["over_wall"] == "Wall" and e["run"] > 0.0
    wall_out = max(-frame_to_world(m["frame"], u, v, d)[1] for m in near["geometry"] if m["elevation"] == "Wall"
                   for d in (float(m["depth"]), float(m["depth"]) + float(m["thickness"])) for u, v in ring_at(m, 0, d))
    drop_in = min(-frame_to_world(m["frame"], u, v, d)[1] for m in _aprons(near)
                  for d in (float(m["depth"]), float(m["depth"]) + float(m["thickness"])) for u, v in ring_at(m, 0, d)
                  if frame_to_world(m["frame"], u, v, d)[2] < 2700.0 - 40.0)
    assert drop_in >= wall_out + EAVES_OVER - 15.0, (drop_in, wall_out)
    far = build(400.0)
    (e,) = [x for i in far["info"] for x in i.get("eaves", [])]
    assert not e.get("over_wall")


def test_a_raking_eaves_meets_its_verges():
    """The job's canopy: an eaves raking about 1 in 6 across the face, a verge with cladding
    (ZI-05) at each end. The corners were left open, the eaves drip short of the flashing
    and the flashing short of the drip, because a drip that far off the face's u was not
    taken for an eaves. Each end now closes: the drip runs on to the flashing and the
    flashing comes down over its end."""
    t, s, c = math.tan(math.radians(25.0)), math.sin(math.radians(25.0)), math.cos(math.radians(25.0))
    rise = 900.0
    pts = [[0, 0, 0], [6000, rise, rise * t], [6000, 4000, 4000 * t], [0, 4000, 4000 * t]]
    roof = extract_elevation({"name": "Roof", "faces": [[pts[0], pts[1], pts[2]], [pts[0], pts[2], pts[3]]],
                              "outward": [0, -s, c], "context": [], "options": {"debug": False, "clad_depth": 150.0}})
    out = generate_preview({"elevations": [dict(roof, offset=0, chain="Roof", settings=dict(SEAM), verge="both", verge_type="cladding")],
                            "trim": True, "splash": 0, **SEAM})
    names = {m["name"] for m in out["geometry"]}
    assert {"Roof Verge Flashing L Eaves End", "Roof Verge Flashing R Eaves End"} <= names
    xs = [frame_to_world(m["frame"], u, v, d)[0] for m in out["geometry"] if "Eaves Drip" in m["name"]
          for d in (float(m["depth"]), float(m["depth"]) + float(m["thickness"])) for u, v in ring_at(m, 0, d)]
    assert min(xs) < -2.0 and max(xs) > 6002.0, "the drip runs on under the flashing at both ends"
