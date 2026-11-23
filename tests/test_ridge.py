"""The ridge: two roof slopes meeting along a level line at their tops are one chain, set
out from one line so seams carry over the ridge, cut on the plane that bisects it with a
vent slot between the decks, and capped once. The roof used is the synthetic face laid at
31.4° and its mirror image the other side of the ridge."""

import copy
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import ifcopenshell  # noqa: E402

from synthetic import payload, world, U, N, ORIGIN  # noqa: E402
from test_pitched import _turn, _tipped  # noqa: E402
from cladding_checks import check_rules  # noqa: E402
from cladding_constants import frame_to_world  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from cladding_primitives import ring_at  # noqa: E402
from cladding_ridge import RIDGE_GAP, RIDGE_LEG, MEET, _cap_ring  # noqa: E402
from dxf_generator import meshes_to_dxf_string  # noqa: E402
from fabric_extract import extract_elevation, chain_link, ridge_link  # noqa: E402
from ifc_generator import meshes_to_ifc, own_elements  # noqa: E402

TIP = 58.6                                   # the wall tipped back this far lies at 31.4° to level
PITCH = math.radians(90.0 - TIP)
RIDGE = _turn(list(world(0, 3000)), TIP)     # a point on the ridge line, at slope A's u = 0
SEAM = dict(cladding_type="plank", plank_profile="seam", plank_orient="vertical")


def _mirror(p, about=RIDGE):
    t = sum((p[i] - about[i]) * N[i] for i in range(3))
    return [p[i] - 2.0 * t * N[i] for i in range(3)]


def _slope(name, u0=0.0, u1=8000.0, top=3000.0, mirrored=False, about=RIDGE):
    """A plain slope: the rectangle u0..u1 by 0..top of the synthetic face, laid at 31.4°;
    *mirrored*, the slope the other side of the vertical plane through *about*."""
    a, b, c, d = world(u0, 0), world(u1, 0), world(u1, top), world(u0, top)
    pl = payload(name)
    pl.update(context=[], faces=[[list(a), list(b), list(c)], [list(a), list(c), list(d)]])
    pl["options"].update(debug=False, clad_depth=150.0)
    pl = _tipped(pl, TIP)
    if mirrored:
        pl["faces"] = [[_mirror(p, about) for p in t] for t in pl["faces"]]
        t = sum(pl["outward"][i] * N[i] for i in range(3))
        pl["outward"] = [pl["outward"][i] - 2.0 * t * N[i] for i in range(3)]
    return extract_elevation(pl)


def _paired(a, b, link, **extra):
    """The two records as the app sends them: one chain, the second slope lying along the
    first and turned round (app.js placeAtRidge)."""
    part = lambda other, v, u, master: {"with": other["name"], "v": v, "u0": u[0], "u1": u[1], "k": link["k"],  # noqa: E731
                                        "angle": link["angle"], "master": master}
    a = dict(dict(a, offset=0, chain="Roof", chain_start=0.0, chain_reversed=False,
                  ridge=part(b, link["v_a"], link["u_a"], True)), **extra)
    b = dict(dict(b, offset=0, chain="Roof", chain_start=link["u_b0"] - b["width"], chain_reversed=True,
                  ridge=part(a, link["v_b"], link["u_b"], False)), **extra)
    return a, b


def _roof(elev=None, **kw):
    a, b = _slope("Roof A"), _slope("Roof B", mirrored=True)
    link = chain_link(a, b)
    a, b = _paired(a, b, link, **(elev or {}))
    params = dict({"elevations": [a, b], "trim": True, "splash": 0}, **kw)
    return link, params, generate_preview(params)


def _off_ridge(mesh, depths=None):
    """How far every corner of a part stands from the ridge's bisecting plane (the vertical
    plane through the ridge), level, on the part's own side: (nearest, furthest). *depths*:
    only its corners at those depths."""
    side = 1.0 if mesh["elevation"] == "Roof A" else -1.0      # slope A falls away to the +N side of the ridge
    ds = [side * sum((frame_to_world(mesh["frame"], u, v, s)[i] - RIDGE[i]) * N[i] for i in range(3))
          for s in (depths or (mesh["depth"], mesh["depth"] + mesh["thickness"])) for u, v in ring_at(mesh, 0, s)]
    return min(ds), max(ds)


# ── finding the ridge ─────────────────────────────────────────────────────

def test_two_slopes_back_to_back_meet_at_a_ridge():
    a, b = _slope("Roof A"), _slope("Roof B", mirrored=True)
    link = chain_link(a, b)
    assert link["kind"] == "ridge"
    assert (link["v_a"], link["v_b"]) == (3000.0, 3000.0)                 # along the top of both
    assert link["u_a"] == [0.0, 8000.0] and link["u_b"] == [0.0, 8000.0] and link["u_b0"] == 8000.0
    assert abs(link["k"] - math.tan(PITCH)) < 1e-3 and abs(link["angle"] - 2 * math.degrees(PITCH)) < 0.1
    back = ridge_link(b, a)                                              # either way round
    assert back["u_a"] == link["u_b"] and back["v_a"] == link["v_b"] and back["k"] == link["k"]


def test_a_ridge_is_only_as_long_as_the_two_slopes_share():
    a, short = _slope("Roof A"), _slope("Roof B", u0=5000.0, u1=9500.0, mirrored=True)
    link = chain_link(a, short)
    assert link["u_a"] == [5000.0, 8000.0]                               # a's u where b lies opposite
    assert link["u_b"] == [1500.0, 4500.0]                               # the same stretch in b's own u
    assert chain_link(a, _slope("Roof C", u0=7800.0, u1=9500.0, mirrored=True)) is None    # 200 shared: not a pair


def test_what_is_not_a_ridge_is_not_joined():
    a = _slope("Roof A")
    wall = extract_elevation(dict(payload("Wall"), options={"debug": False}))
    assert chain_link(a, wall) is None and chain_link(wall, a) is None            # a roof meeting a wall
    assert chain_link(a, _slope("Roof A2", u0=8000.0, u1=9000.0)) is None          # the same slope carried on
    low = _slope("Low", top=1500.0)
    assert chain_link(low, _slope("Far", mirrored=True)) is None                  # the line is 1500 above low's top
    foot = _turn(list(world(0, 0)), TIP)
    assert chain_link(a, _slope("Valley", mirrored=True, about=foot)) is None      # falling towards each other
    # a hip: the second slope turned a quarter round, so the two planes meet on a sloping line
    hip = copy.deepcopy(_slope("Hip", mirrored=True))
    hip["frame"]["u"], hip["frame"]["n"] = [-hip["frame"]["u"][1], hip["frame"]["u"][0], 0.0], \
        [-hip["frame"]["n"][1], hip["frame"]["n"][0], hip["frame"]["n"][2]]
    assert chain_link(a, hip) is None


# ── one setting-out over both slopes ──────────────────────────────────────

@pytest.mark.parametrize("offset", [0.0, 137.0])
def test_seams_carry_over_the_ridge(offset):
    _link, params, out = _roof(elev={"offset": offset}, **SEAM)

    def along(name):
        e = next(e for e in params["elevations"] if e["name"] == name)
        lines = {round(m["seam_line"], 1) for m in out["geometry"] if m["elevation"] == name and m.get("seam_part")}
        return sorted(round(sum((frame_to_world(e["frame"], u, 0)[i] - ORIGIN[i]) * U[i] for i in range(3)), 1) for u in lines)
    assert along("Roof A") == along("Roof B") and len(along("Roof A")) > 10


def test_a_shorter_second_slope_is_set_out_from_the_first_slopes_line():
    a, b = _slope("Roof A"), _slope("Roof B", u0=2650.0, u1=8000.0, mirrored=True)
    a, b = _paired(a, b, chain_link(a, b), verge="none")        # a verge moves the seams next to it
    params = dict({"elevations": [a, b], "trim": True, "splash": 0}, **SEAM)
    out = generate_preview(params)

    def along(e):
        lines = {round(m["seam_line"], 1) for m in out["geometry"] if m["elevation"] == e["name"] and m.get("seam_part")}
        return {round(sum((frame_to_world(e["frame"], u, 0)[i] - ORIGIN[i]) * U[i] for i in range(3)), 1) for u in lines}
    assert along(b) and along(b) <= along(a)


# ── the ends at the ridge ─────────────────────────────────────────────────

@pytest.mark.parametrize("kw", [SEAM, dict(cladding_type="panel", sheathing=True, insulation=True),
                                dict(cladding_type="plank", plank_orient="horizontal")])
def test_the_cladding_stops_the_vent_slot_short_and_the_structure_meets(kw):
    _link, _params, out = _roof(**kw)
    kinds = set()
    for m in out["geometry"]:
        if m["ifc_type"] in ("ridge_cap", "ridge_filler", "verge_strip"):
            continue
        near, _far = _off_ridge(m)
        kinds.add(m["ifc_type"])
        if m["ifc_type"] in MEET:
            assert abs(near) < 0.05, m["name"]                            # the two slopes' boards meet
        else:
            assert near >= RIDGE_GAP / 2.0 - 0.05, (m["name"], near)      # nothing in the vent slot
    assert {"batten"} <= kinds and (kinds & {"deck", "panel", "plank"})
    # the cladding that reaches the top is cut on the plane: both its faces end 15 mm off it
    clad = [m for m in out["geometry"] if m["ifc_type"] in ("plank", "panel") and max(q[1] for q in m["profile"]) > 2950.0]
    assert clad
    for m in clad:
        ends = [_off_ridge(m, (d,))[0] for d in (m["depth"], m["depth"] + m["thickness"])]
        assert all(abs(e - RIDGE_GAP / 2.0) < 0.05 for e in ends), m["name"]


def test_the_trays_stand_30_apart_turned_up_15_and_oversail_a_square_cut_deck():
    _link, _params, out = _roof(**SEAM)
    trays = [m for m in out["geometry"] if m["ifc_type"] == "plank"]
    assert all(abs(_off_ridge(m)[0] - RIDGE_GAP / 2.0) < 0.05 for m in trays)       # 15 each side: 30 between
    ups = [m for m in out["geometry"] if m.get("ridge_part")]
    assert len(ups) == len(trays) and all(m["thickness"] == 15.0 and m["ifc_type"] == "seam" for m in ups)
    assert all(abs(_off_ridge(m)[0] - RIDGE_GAP / 2.0) < 0.6 for m in ups)          # at the tray's end
    decks = [m for m in out["geometry"] if m["ifc_type"] == "deck"]
    assert len(decks) == 2
    tray = trays[0]
    for deck in decks:
        assert not any(sh[3] for sh in deck.get("vshift", [[]])[0])                 # square: no slope with depth
        top = max(q[1] for q in deck["profile"])
        assert 10.0 <= max(q[1] for q in tray["profile"]) - top < 14.0              # the pan oversails it
        assert abs(_off_ridge(deck)[0] - RIDGE_GAP / 2.0) < 0.05                    # its back corner as near as the trays
    battens = [m for m in out["geometry"] if m["ifc_type"] == "batten"]
    assert all(abs(_off_ridge(m)[0] - RIDGE_GAP / 2.0) < 0.05 for m in battens)
    assert not any(sh[3] for m in battens for sh in (m.get("vshift") or [[]])[0])


def test_the_cutting_list_has_the_trays_as_long_as_they_are():
    """The end is moved in the tray's own outline, so its length is the real one: the
    face's 3000 less the splash-free base offset, plus what the ridge adds."""
    _link, _params, out = _roof(**SEAM)
    _l2, _p2, plain = _roof(elev={"clip_v_hi": 2400.0}, **SEAM)
    tray = next(m for m in out["geometry"] if m["ifc_type"] == "plank")
    top = max(q[1] for q in tray["profile"])
    k = math.tan(PITCH)
    want = 3000.0 - 15.0 * math.sqrt(1 + k * k) + k * (tray["depth"] + tray["thickness"] / 2.0)
    assert abs(top - want) < 0.05 and top > 3000.0
    assert max(q[1] for m in plain["geometry"] if m["ifc_type"] == "plank" for q in m["profile"]) <= 2400.0


def test_a_top_level_picked_below_the_ridge_leaves_the_slopes_as_they_were():
    _link, _params, out = _roof(elev={"clip_v_hi": 2400.0}, **SEAM)
    assert not [m for m in out["geometry"] if m["ifc_type"] in ("ridge_cap", "ridge_filler")]
    assert not any(sh[2] or sh[3] for m in out["geometry"] if m["ifc_type"] in ("deck", "batten", "plank")
                   for ring in (m.get("vshift") or []) for sh in ring)
    assert not [m for m in out["geometry"] if m.get("ridge_part")]
    assert all("ridge" not in i for i in out["info"])


# ── the cap and the fillers ───────────────────────────────────────────────

def test_one_cap_over_the_ridge_resting_on_the_seams():
    link, params, out = _roof(**SEAM)
    (cap,) = [m for m in out["geometry"] if m["ifc_type"] == "ridge_cap" and not m.get("stop_end")]
    # the master's: the ridge long, and on out over each verge's drip, closed at each end (cladding_verge.close_cap_ends)
    over = (float(cap["thickness"]) - 8000.0) / 2.0
    assert cap["elevation"] == "Roof A" and 55.0 < over < 70.0
    info = cap["ridge_info"]
    assert info["between"] == ["Roof A", "Roof B"] and info["leg"] == RIDGE_LEG and info["gap"] == RIDGE_GAP
    xs, ys = [q[0] for q in cap["profile"]], [q[1] for q in cap["profile"]]
    assert abs(min(xs) + max(xs)) < 0.01                                           # equal pitches: a symmetrical cap
    assert abs(max(xs) - RIDGE_LEG * math.cos(PITCH)) < 1.5                        # each leg 185 down the slope
    face = out["info"][0]["total_depth"]
    rest = face + params.get("seam_h", 25.0) + 2.0                                 # on the seams, 2 clear
    assert abs(max(ys) - (rest / math.cos(PITCH) + 0.3 / math.cos(PITCH))) < 1.0   # the apex, over the ridge line
    # its ends are the ridge's ends, carried out over the verges, and it stands over the ridge line
    ends = [frame_to_world(cap["frame"], 0.0, 0.0, d) for d in (0.0, cap["thickness"])]
    along = sorted(round(sum((p[i] - RIDGE[i]) * U[i] for i in range(3)), 1) for p in ends)
    assert along == [round(-over, 1), round(8000.0 + over, 1)]
    stops = [m for m in out["geometry"] if m.get("stop_end")]
    assert sorted(m["name"] for m in stops) == ["Roof A Ridge Stop End L", "Roof A Ridge Stop End R"]
    assert all(m["of"] == cap["name"] and m["tray_sheet"] for m in stops)
    assert all(abs(sum((p[i] - RIDGE[i]) * N[i] for i in range(3))) < 0.05 and abs(p[2] - RIDGE[2]) < 0.05 for p in ends)
    # nothing built on either slope comes through the cap: every part under it is below its underside
    a = next(e for e in params["elevations"] if e["name"] == "Roof A")
    for m in out["geometry"]:
        if m["elevation"] == "Roof A" and m["ifc_type"] in ("seam", "plank", "deck", "ridge_filler"):
            assert m["depth"] + m["thickness"] <= rest + 0.01, m["name"]            # the fillers touch it
    assert a["ridge"]["master"] and out["info"][0]["ridge"]["master"] and not out["info"][1]["ridge"]["master"]


def test_fillers_sit_between_the_seams_under_the_cap_on_both_slopes():
    _link, _params, out = _roof(**SEAM)
    for name in ("Roof A", "Roof B"):
        fillers = [m for m in out["geometry"] if m["ifc_type"] == "ridge_filler" and m["elevation"] == name]
        lines = sorted({round(m["seam_line"], 1) for m in out["geometry"] if m["elevation"] == name and m.get("seam_part")})
        stops = [0.0] + lines + [8000.0]
        assert len(fillers) == len([1 for a, b in zip(stops, stops[1:]) if b - a > 60.0]) >= len(lines) - 1
        strip = next(i["ridge_strip"] for i in out["info"] if i["elevation"] == name)
        edge = min(q[1] for q in strip)                                           # the cap's edge on this slope
        for m in fillers:
            us, vs = [q[0] for q in m["profile"]], [q[1] for q in m["profile"]]
            assert not any(min(us) - 5.0 < line < max(us) + 5.0 for line in lines), m["name"]
            assert (round(min(vs) - edge, 1), round(max(vs) - min(vs), 1)) == (90.0, 50.0)   # 80 + 10 in, 50 wide
            assert m["thickness"] == 27.0                                         # from the tray up to the cap
            ups = [u for u in out["geometry"] if u.get("ridge_part") and u["elevation"] == name]
            assert min(q[1] for u in ups for q in u["profile"]) - max(vs) >= 10.0  # 10 clear of the turned-up ends


def test_boards_and_panels_get_the_cap_but_no_fillers_and_a_warning():
    for kw, word in ((dict(cladding_type="panel"), "panels"), (dict(cladding_type="plank", plank_orient="horizontal"), "boards")):
        _link, params, out = _roof(**kw)
        assert len([m for m in out["geometry"] if m["ifc_type"] == "ridge_cap"]) == 1
        assert not [m for m in out["geometry"] if m["ifc_type"] == "ridge_filler"]
        (row,) = [c for c in check_rules(params, out["info"]) if c["name"].endswith("Ridge")]
        assert row["status"] == "warn" and word in row["message"]
    _link, params, out = _roof(**SEAM)
    (row,) = [c for c in check_rules(params, out["info"]) if c["name"].endswith("Ridge")]
    assert row["status"] == "pass" and "8.00 m" in row["message"] and "Roof A and Roof B" in row["message"]
    assert out["info"][0]["ridge"]["clip_room"] == 80.0


def test_a_cap_over_unequal_pitches_lies_on_both():
    """30° one side, 50° the other: each leg runs parallel to its own slope, its underside
    the rest distance off it, and the two meet at one apex."""
    def face(deg, flip):
        c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        return ((-c, -s), (-s, c)) if flip else ((c, -s), (s, c))
    (da, na), (db, nb) = face(30.0, False), face(50.0, True)
    ring, up_a, up_b = _cap_ring(da, na, 80.0, db, nb, 80.0, 0.6)
    assert up_a > 0 and up_b > 0
    for d, n in ((da, na), (db, nb)):
        along = [q[0] * d[0] + q[1] * d[1] for q in ring]
        off = [q[0] * n[0] + q[1] * n[1] for q in ring]
        edge = [o for a, o in zip(along, off) if a > max(along) - 1.0]            # the leg's far edge and its hem
        assert max(edge) - 80.0 < 0.61 and min(edge) > 80.0 - 2.0                 # a sheet and a hem deep, on the rest
    apex = max(ring, key=lambda q: q[1])
    assert abs((apex[0] * na[0] + apex[1] * na[1]) - (apex[0] * nb[0] + apex[1] * nb[1])) < 0.5


# ── exports ───────────────────────────────────────────────────────────────

def test_the_ridge_exports_to_ifc_and_dxf():
    _link, params, out = _roof(**SEAM)
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    (cap,) = [e for e in ifc.by_type("IfcCovering") if e.Name == "Roof A Ridge Cap"]
    assert cap.PredefinedType == "ROOFING" and cap.ObjectType == "Ridge cap"
    fillers = [e for e in ifc.by_type("IfcMember") if "Ridge Filler" in (e.Name or "")]
    assert len(fillers) == len([m for m in out["geometry"] if m["ifc_type"] == "ridge_filler"]) and fillers
    products = [e for e in ifc.by_type("IfcElement") if not e.is_a("IfcElementAssembly")]
    assert len(products) == len(own_elements(out["geometry"]))
    dxf = meshes_to_dxf_string(out["geometry"], params, out["info"])
    assert "ROOF / ROOF A" in dxf and "ROOF / ROOF B" in dxf


def test_a_slope_modelled_running_on_past_its_ridge_still_meets_the_other_and_is_cut_back():
    """A real model's slope ran on half a metre past its ridge line (two roofs drawn
    overlapping). It still meets the other slope at the ridge, so the two share a chain and
    the cap; and nothing of it is built past the ridge line, battens and all."""
    a, b = _slope("Roof A", top=3495.0), _slope("Roof B", mirrored=True)
    link = chain_link(a, b)
    assert link and link["kind"] == "ridge"
    a, b = _paired(a, b, link)
    out = generate_preview({"elevations": [a, b], "trim": True, "splash": 0})
    caps = [m for m in out["geometry"] if m.get("ridge_info") or "Ridge Cap" in (m.get("name") or "")]
    assert caps, "the ridge cap"
    worst = min(_off_ridge(m)[0] for m in out["geometry"] if m.get("elevation") == "Roof A" and not m.get("ridge_info")
                and m.get("ifc_type") in ("plank", "batten", "deck", "counter_batten", "seam"))
    assert worst > -1.0, "nothing of the slope past the ridge line"


def test_a_slope_modelled_short_of_its_ridge_is_carried_up_to_it():
    """The other side of a ridge between two roofs drawn apart: one slope stops 250 mm short
    of the line. Its cladding is carried up to the ridge as the other's is, not left short
    with the model's deck showing under the cap."""
    a, b = _slope("Roof A", top=2750.0), _slope("Roof B", mirrored=True)
    link = chain_link(a, b)
    assert link and link["kind"] == "ridge"
    a, b = _paired(a, b, link)
    out = generate_preview({"elevations": [a, b], "trim": True, "splash": 0})
    trays = [m for m in out["geometry"] if m.get("elevation") == "Roof A" and m.get("ifc_type") == "plank"]
    other = [m for m in out["geometry"] if m.get("elevation") == "Roof B" and m.get("ifc_type") == "plank"]
    near = lambda ms: min(_off_ridge(m)[0] for m in ms)          # noqa: E731
    assert near(trays) == pytest.approx(near(other), abs=2.0), "both slopes' trays stop the same distance off the ridge"
