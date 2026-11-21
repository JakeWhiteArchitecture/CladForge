"""The verge: the free side edge of a roof slope in standing seam. Drawn as the eaves: the
plywood runs on, the last tray's own sheet turns round its end into a drip over a support
profile, and its last seam stands 150 to 370 mm from the tray's edge (VMZINC's verge
junction G3)."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import ifcopenshell  # noqa: E402

from test_ridge import _slope, _roof, SEAM  # noqa: E402
from test_pitched import _extract  # noqa: E402
from cladding_checks import check_rules  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_constants import frame_to_world, world_to_frame  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from cladding_verge import VERGE_TRAY, VERGE_CLEAR, VERGE_BELOW, verge_sides  # noqa: E402
from cladding_nesting import chain_pieces  # noqa: E402
from dxf_generator import meshes_to_dxf_string  # noqa: E402
from ifc_generator import meshes_to_ifc, own_elements  # noqa: E402


def _one(elev=None, **kw):
    a = dict(dict(_slope("Roof A"), offset=0), **(elev or {}))
    params = dict({"elevations": [a], "trim": True, "splash": 0}, **dict(SEAM, **kw))
    return params, generate_preview(params)


def _strips(out, what=None):
    return [m for m in out["geometry"] if m["ifc_type"] == "verge_strip" and (what is None or what in m["name"])]


def _drips(out, side=None):
    return [m for m in out["geometry"] if m.get("verge_drip") and (side is None or m["name"].startswith("Roof A Tray C%s" % side))]


def _section(a, m, edge, out_sign):
    """A verge part's corners: (out from the roof's edge, depth off the face)."""
    pts = [world_to_frame(a["frame"], frame_to_world(m["frame"], x, y, 0.0)) for x, y in m["profile"]]
    return [(out_sign * (q[0] - edge), q[2]) for q in pts]


def test_a_roof_slope_gets_a_support_and_the_trays_own_sheet_as_a_drip_up_each_free_side():
    params, out = _one()
    left, right = sorted(_strips(out, "Support"), key=lambda m: m["name"])
    assert (left["name"], right["name"]) == ("Roof A Verge Support L", "Roof A Verge Support R")
    assert left["thickness"] == 3000.0 and right["thickness"] == 3000.0 and left["verge_support"]   # the slope long
    a = params["elevations"][0]
    deck = next(m for m in out["geometry"] if m["ifc_type"] == "deck")
    soffit = deck["depth"]
    trays = {m["name"]: m for m in out["geometry"] if m["ifc_type"] == "plank"}
    drips = _drips(out)
    assert drips and all(m["tray_sheet"] and m["of"] in trays and m["name"].startswith(m["of"] + " Verge Drip") for m in drips)
    for strip, edge, out_sign in ((left, 0.0, -1.0), (right, 8000.0, 1.0)):
        mine = [m for m in drips if abs(min(q[0] for q in trays[m["of"]]["profile"]) - edge) < VERGE_TRAY + 1.0
                or abs(max(q[0] for q in trays[m["of"]]["profile"]) - edge) < VERGE_TRAY + 1.0]
        length = sum(m["thickness"] for m in mine)
        assert length >= strip["thickness"] - 25.0, "a drip to each tray at the edge, the verge long (and on down to the eaves drip)"
        pts = [q for m in mine for q in _section(a, m, edge, out_sign)]
        t = strip["verge_info"]["thickness"]
        assert max(x for x, _d in pts) > VERGE_TRAY + 5.0, "round the plywood's end"
        drop = [x for x, d in pts if d < soffit - 10.0]
        assert drop and VERGE_CLEAR <= max(drop) <= VERGE_CLEAR + 4.0, "the drop 10 clear of the edge's face (its hook turns back behind)"
        assert min(d for _x, d in pts) == pytest.approx(-VERGE_BELOW, abs=4.0), "its foot below the roof's surface"
        prop = _section(a, strip, edge, out_sign)
        assert min(x for x, _d in prop) < 0.0 < max(x for x, _d in prop) <= VERGE_TRAY, "under the plywood, its leg behind the drop"
        assert strip["verge_info"]["sheet"] > 100.0 and strip["verge_info"]["drips"] == len([m for m in mine if not m["name"].endswith(" End")])


def test_the_plywood_and_the_last_tray_run_on_and_the_tray_is_as_wide_in_the_cutting_list():
    _params, out = _one()
    _p0, plain = _one(elev={"verge": "none"})
    for kind in ("plank", "deck"):
        us = [q[0] for m in out["geometry"] if m["ifc_type"] == kind for q in m["profile"]]
        assert (min(us), max(us)) == (-VERGE_TRAY, 8000.0 + VERGE_TRAY), kind
    us0 = [q[0] for m in plain["geometry"] if m["ifc_type"] == "plank" for q in m["profile"]]
    assert (min(us0), max(us0)) == (0.0, 8000.0) and not _strips(plain)
    # the battens stop at the roof's edge
    us = [q[0] for m in out["geometry"] if m["ifc_type"] == "batten" for q in m["profile"]]
    assert min(us) >= 0.0 and max(us) <= 8000.0
    # each tray at the edge is cut as wide as the coil it takes, drip and all
    sheet = _strips(out, "Support")[0]["verge_info"]["sheet"]
    edge = [m for m in out["geometry"] if m.get("cut_extra_w")]
    assert edge and all(m["cut_extra_w"] == sheet for m in edge)
    pieces = {q["name"]: q for q in chain_pieces(out["geometry"], ["Roof A"], "plank")}
    plain_w = {q["name"]: q for q in chain_pieces([dict(m, cut_extra_w=0.0) for m in edge], ["Roof A"], "plank")}
    assert all(pieces[m["name"]]["w"] == pytest.approx(plain_w[m["name"]]["w"] + sheet, abs=0.2) for m in edge)


@pytest.mark.parametrize("want, sides", [("both", "LR"), ("start", "L"), ("end", "R"), ("none", "")])
def test_the_chain_says_which_sides_are_verges(want, sides):
    _params, out = _one(elev={"verge": want})
    assert "".join(sorted(m["name"][-1] for m in _strips(out, "Support"))) == sides
    assert [v["side"] for v in out["info"][0].get("verges", [])] == [c.lower() for c in sides]


def test_start_and_end_are_the_same_ends_of_the_roof_on_both_slopes_of_a_ridge():
    """The second slope of a ridge is turned round, so its left is the first slope's right:
    "start" must still be one end of the roof, not one on each."""
    _link, params, out = _roof(elev={"verge": "start"}, **SEAM)
    a, b = params["elevations"]
    strips = {m["elevation"]: m for m in _strips(out, "Support") if not m["name"].endswith("End")}
    assert strips["Roof A"]["name"].endswith("L") and strips["Roof B"]["name"].endswith("R")
    ends = [frame_to_world(m["frame"], 0.0, 0.0, 0.0) for m in strips.values()]
    from synthetic import U
    along = [sum((p[i] - ends[0][i]) * U[i] for i in range(3)) for p in ends]
    assert abs(along[1]) < 0.01                                                 # both at the one gable
    # at the ridge each verge runs on up to the plane that bisects it, and is cut on it, so
    # the two slopes' verges meet at the apex under the cap's stop end
    from cladding_primitives import ring_at
    k = math.tan(math.radians(31.4))
    for e in (a, b):
        tops = [m for m in out["geometry"] if m["elevation"] == e["name"] and m["name"].endswith("Ridge End")
                and (m.get("verge_support") or m.get("verge_drip"))]
        assert len(tops) == 2, [m["name"] for m in tops]                         # the support and the drip
        for m in tops:
            on = lambda d: [world_to_frame(e["frame"], frame_to_world(m["frame"], x, y, d)) for x, y in ring_at(m, 0, d)]   # noqa: E731
            off = [max(abs(q[1] - k * q[2] - e["ridge"]["v"]) for q in on(d)) for d in (0.0, float(m["thickness"]))]
            assert min(off) < 0.5, (m["name"], off)                              # one end of it lies in the plane
    caps = [m for m in out["geometry"] if m.get("stop_end")]
    assert [m["name"] for m in caps] == ["Roof A Ridge Stop End L"] and caps[0]["of"] == "Roof A Ridge Cap"   # "start": one gable, one stop end


@pytest.mark.parametrize("verge_type", ["g3", "cladding"])
@pytest.mark.parametrize("style", ["square", "v", "flat"])
def test_the_ridge_cap_stop_end_hangs_a_skirt_over_the_apex(style, verge_type):
    """"v" and "flat" hang the stop end down over the two verges' joint at the apex: to a
    point on the centre line, its edges square to the cap's flanges, or a short level edge
    across it 10 mm below the verges' lowest point there. "square" is the cap's section only."""
    from cladding_constants import frame_v
    from cladding_primitives import ring_at
    _link, params, out = _roof(elev={"verge_type": verge_type, "ridge_end": style}, **SEAM)
    geo = out["geometry"]
    (cap,) = [m for m in geo if m["ifc_type"] == "ridge_cap" and not m.get("stop_end")]
    stops = [m for m in geo if m.get("stop_end")]
    assert len(stops) == 2
    f = cap["frame"]
    cu, cv = f["u"], frame_v(f)
    sec = lambda q: [sum((q[i] - f["origin"][i]) * a[i] for i in range(3)) for a in (cu, cv)]   # noqa: E731
    # the verges' lowest point where the two slopes' verges meet, on the centre line
    lows = [sec(frame_to_world(m["frame"], x, y, d))[1] for m in geo if m["name"].endswith("Ridge End")
            for d in (float(m["depth"]), float(m["depth"]) + float(m["thickness"])) for x, y in ring_at(m, 0, d)
            if abs(sec(frame_to_world(m["frame"], x, y, d))[0]) < 0.5]
    verge_low = min(lows)
    cap_low = min(q[1] for q in cap["profile"])
    assert verge_low < cap_low - 30.0                                       # the joint shows below the cap's corners
    for stop in stops:
        ys = [q[1] for q in stop["profile"]]
        bottom = [q for q in stop["profile"] if abs(q[1] - min(ys)) < 0.01]
        if style == "square":
            assert abs(min(ys) - cap_low) < 0.01
        elif style == "v":
            # the point on the centre line, well below the verges, each edge square to its flange
            assert len(bottom) == 1 and abs(bottom[0][0]) < 0.5 and min(ys) < verge_low - 10.0
            apex = max(cap["profile"], key=lambda q: q[1])
            ring = stop["profile"]
            i = ring.index(bottom[0])
            for sign in (1, -1):
                end = max(cap["profile"], key=lambda q: q[0] * sign)
                flange = (end[0] - apex[0], end[1] - apex[1])
                foot = [q for q in (ring[i - 1], ring[(i + 1) % len(ring)]) if q[0] * sign > 0][0]   # the V's edge on that side
                edge = (bottom[0][0] - foot[0], bottom[0][1] - foot[1])
                cos = (flange[0] * edge[0] + flange[1] * edge[1]) / math.hypot(*flange) / math.hypot(*edge)
                assert abs(cos) < 0.01, cos
        else:
            assert abs(min(ys) - (verge_low - 10.0)) < 0.5
            assert len(bottom) == 2 and abs(bottom[0][0] + bottom[1][0]) < 0.5 and abs(bottom[0][0]) > 10.0
    # still one element with its cap in the IFC
    ifc = ifcopenshell.open(meshes_to_ifc(geo, params, out["info"]))
    assert not [e for e in ifc.by_type("IfcElement") if "Stop End" in (e.Name or "")]


def test_only_trays_running_with_the_fall_on_a_roof_get_a_verge():
    roof = dict(_slope("Roof A"), offset=0)
    seam = {"seam": True, "plank_orient": "vertical"}
    assert verge_sides(roof, seam) == ("l", "r")
    assert verge_sides(dict(roof, chain_reversed=True, verge="start"), seam) == ("r",)
    assert verge_sides(roof, {"seam": True, "plank_orient": "horizontal"}) == ()
    assert verge_sides(roof, {"seam": False, "plank_orient": "vertical"}) == ()
    assert verge_sides(dict(_extract(0.0), offset=0), seam) == ()                # a wall
    assert verge_sides(dict(_extract(8.0), offset=0), seam) == ()                # a wall leaning back
    assert verge_sides(dict(_extract(-30.0), offset=0), seam) == ()              # leaning out
    params, out = _one(cladding_type="panel")
    assert not _strips(out) and "verges" not in out["info"][0]


def test_the_setting_out_keeps_the_last_seam_150_to_370_from_the_trays_edge():
    """Whatever the offset, the tray at each verge is neither a sliver nor too wide for the
    detail, and no tray anywhere is wider than the tray width, counting the 40 mm it runs
    out over the strip: a closer under 150 shares with its neighbour, one over 370 is
    halved."""
    for offset in range(0, 530, 10):
        params, out = _one(elev={"offset": float(offset)})
        trays = sorted({(round(min(q[0] for q in m["profile"]), 1), round(max(q[0] for q in m["profile"]), 1))
                        for m in out["geometry"] if m["ifc_type"] == "plank"})
        assert (trays[0][0], trays[-1][1]) == (-VERGE_TRAY, 8000.0 + VERGE_TRAY), offset
        assert max(b - a for a, b in trays) <= 530.0 + 1e-6, offset
        seams = [v["last_seam"] for v in out["info"][0]["verges"]]
        assert all(150.0 - 1e-6 <= s <= 370.0 + 1e-6 for s in seams), (offset, seams)
        (row,) = [c for c in check_rules(params, out["info"]) if c["name"].endswith("Verge")]
        assert row["status"] == "pass", (offset, row["message"])
    # with no verge that end is set out as before: the closer is whatever the offset leaves
    _p, plain = _one(elev={"offset": 0.0, "verge": "none"})
    widths = sorted({round(max(q[0] for q in m["profile"]) - min(q[0] for q in m["profile"]) + (m.get("cut_grow") or (0.0, 0.0))[0], 1)
                     for m in plain["geometry"] if m["ifc_type"] == "plank"})
    assert widths == [25.0, 530.0]                               # seam to seam (each flat stands back from its seams' lines)


def test_the_verge_check_still_warns_when_the_tray_is_narrower_than_the_rule_allows():
    """A tray width the rule cannot be met with (narrow trays: 200 wide cannot leave 150 to
    370 at both verges of every width) is said, not hidden."""
    from cladding_checks import check_rules as rules
    info = [{"elevation": "Roof A", "verges": [{"side": "l", "last_seam": 65.0}, {"side": "r", "last_seam": 415.0}]}]
    (row,) = [c for c in rules(dict(SEAM, elevations=[]), info) if c["name"].endswith("Verge")]
    assert row["status"] == "warn" and "65 mm (left) and 415 mm (right)" in row["message"] and "150 to 370" in row["message"]


def test_the_verge_exports_to_ifc_and_dxf():
    params, out = _one()
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    strips = [e for e in ifc.by_type("IfcCovering") if "Verge Support" in (e.Name or "")]
    assert len(strips) == 2 and all(e.PredefinedType == "ROOFING" and e.ObjectType == "Verge support profile" for e in strips)
    assert not [e for e in ifc.by_type("IfcElement") if "Verge Drip" in (e.Name or "")], "each drip is in its tray's element"
    assert len([e for e in ifc.by_type("IfcElement") if not e.is_a("IfcElementAssembly")]) == len(own_elements(out["geometry"]))
    assert len(out["info"][0]["verge_strips"]) == 2
    assert "ROOF A" in meshes_to_dxf_string(out["geometry"], params, out["info"])


# ── the verge with cladding (VMZ-SN-MA-ZI-05) ─────────────────────────────

def _flashings(out):
    return sorted((m for m in out["geometry"] if "Verge Flashing" in m["name"] and m["ifc_type"] == "verge_strip" and not m.get("drip_end")),
                  key=lambda m: m["name"])


def _section(a, part, edge, out_sign):
    """A verge part's outline as [(out from the roof's edge, depth from the face)]."""
    pts = [world_to_frame(a["frame"], frame_to_world(part["frame"], x, y, 0.0)) for x, y in part["profile"]]
    return [(out_sign * (q[0] - edge), q[2]) for q in pts]


def test_the_verge_with_cladding_is_a_true_seam_at_the_edge_and_a_flashing_down_over_the_wall():
    """The edge is a standing seam, the same closed double lock as between two trays: the
    last tray turns up the undercloak on the roof's side, the flashing is the overcloak on
    the outside and runs straight down from it to cover the head of the wall's cladding."""
    from cladding_verge import VERGE_CLIP_LAP, VERGE_COVER, VERGE_GAP, VERGE_FACE, verge_type, verge_seam_out
    from cladding_constants import SEAM_AIR
    params, out = _one(elev={"verge_type": "cladding"})
    a = params["elevations"][0]
    assert verge_type(a) == "cladding" and verge_type({}) == "g3" and verge_type({"verge_type": "nonsense"}) == "g3"
    assert not _strips_named(out, "Verge Strip") and not _strips_named(out, "Verge Upstand")
    left, right = _flashings(out)
    clips = [m for m in _strips_named(out, "Verge Clip") if not m.get("drip_end")]
    assert (left["name"], right["name"]) == ("Roof A Verge Flashing L", "Roof A Verge Flashing R") and len(clips) == 2
    deck = next(m for m in out["geometry"] if m["ifc_type"] == "deck")
    deck_top, t = deck["depth"] + deck["thickness"], left["verge_info"]["thickness"]
    trays = [m for m in out["geometry"] if m["ifc_type"] == "plank"]
    back, face = min(m["depth"] for m in trays), max(m["depth"] + m["thickness"] for m in trays)
    seams = [m for m in out["geometry"] if m["ifc_type"] == "seam"]
    field = [m for m in seams if not m.get("verge_seam")]
    # a field seam, sheet by sheet: (which sheet, where it stands off its line, its depths)
    one = sorted({m["seam_line"] for m in field})[0]
    shape = lambda ms, line, turn: sorted((m["seam_part"], round(turn * (min(q[0] for q in m["profile"]) - line), 3)             # noqa: E731
                                           if turn > 0 else round(turn * (max(q[0] for q in m["profile"]) - line), 3),
                                           round(max(q[0] for q in m["profile"]) - min(q[0] for q in m["profile"]), 3),
                                           round(m["depth"], 3), round(m["thickness"], 3)) for m in ms)
    want = shape([m for m in field if m["seam_line"] == one], one, 1.0)
    assert len(want) == 8
    for flashing, clip, edge, sign in ((left, clips[0], 0.0, -1.0), (right, clips[1], 8000.0, 1.0)):
        mine = [m for m in seams if m.get("verge_seam") and abs(m["seam_line"] - edge) < 20.0]
        line = edge + sign * (verge_seam_out() + SEAM_AIR / 2.0)
        assert len(mine) == 8 and all(m["seam_line"] == pytest.approx(line) for m in mine)
        # exactly the field seam: the same eight sheets, the same sizes and heights; mirrored on the right, so the
        # head hangs on the roof's side at both verges
        assert shape(mine, line, -sign) == want
        under = [m for m in mine if m["seam_side"] == "+"]
        assert len(under) == 3 and {m["of"] for m in under} <= {m["name"] for m in trays}          # the tray's own sheet
        assert {m["of"] for m in mine if m["seam_side"] == "-"} == {flashing["name"]}               # the flashing's
        up = next(m for m in under if m["seam_part"] == "under_up")
        inner = [sign * (q[0] - edge) for q in up["profile"]]
        assert max(inner) == pytest.approx(verge_seam_out(), abs=0.01)             # the tray's upstand stands on the tray's edge
        assert max(sign * (q[0] - edge) for m in trays for q in m["profile"]) == pytest.approx(verge_seam_out(), abs=0.01)
        assert all(max(q[1] for q in m["profile"]) - min(q[1] for q in m["profile"]) >= 3000.0 for m in mine)   # the slope long, and on to the eaves
        # the flashing: straight down from the foot of its upstand in the seam, in line with it
        sec = _section(a, flashing, edge, sign)
        over = next(m for m in mine if m["seam_part"] == "over_up")
        stem = sorted(sign * (q[0] - edge) for q in over["profile"])
        assert (min(x for x, _d in sec), max(x for x, _d in sec)) == pytest.approx((VERGE_FACE - 2.5 * t, VERGE_FACE + t), abs=0.01)   # its hem inward, its face in line with its upstand
        assert (stem[0], stem[-1]) == pytest.approx((VERGE_FACE, VERGE_FACE + t), abs=0.01)
        assert max(d for _x, d in sec) == pytest.approx(face, abs=0.01) == pytest.approx(over["depth"], abs=0.01)
        # down the face of the edge: past the plywood and the gap under it, to cover the wall's cladding by 60
        foot = min(d for _x, d in sec)
        assert foot == pytest.approx(back - 1.0 - deck["thickness"] - VERGE_GAP - VERGE_COVER, abs=t)
        assert foot < deck["depth"] - VERGE_GAP - VERGE_COVER + 3.0              # below the plywood by the gap and the cover
        assert flashing["verge_info"]["drop"] == deck["thickness"] + VERGE_GAP + VERGE_COVER and flashing["verge_info"]["cover"] == 60.0
        # the continuous strip behind it: up and over the plywood, 40 in on its top under the tray (so it is fixed
        # down through the plywood into the batten at the edge), down the edge's face and into the flashing's hem
        strip = _section(a, clip, edge, sign)
        assert min(x for x, _d in strip) == pytest.approx(-VERGE_CLIP_LAP, abs=0.05) and max(x for x, _d in strip) < VERGE_FACE + t
        on_top = [d for x, d in strip if x < -5.0]
        assert min(on_top) == pytest.approx(deck_top, abs=0.05) and max(on_top) == pytest.approx(deck_top + t, abs=0.05) and max(on_top) < back
        batten = min((m for m in out["geometry"] if m["ifc_type"] == "batten"), key=lambda m: abs(sum(q[0] for q in m["profile"]) / len(m["profile"]) - edge))
        mid = sign * (sum(q[0] for q in batten["profile"]) / len(batten["profile"]) - edge)
        assert min(x for x, _d in strip) < mid < 0.0                              # it reaches over the batten at the roof's edge
        assert min(d for _x, d in strip) >= foot and max(d for _x, d in strip) <= back
        # the slope long: carried down to the back of the eaves drip at the corner (join_drips), its end pieces beyond
        assert abs(flashing["thickness"] - 3000.0) < 10.0 and abs(clip["thickness"] - 3000.0) < 10.0


def _strips_named(out, text):
    return sorted((m for m in out["geometry"] if text in m["name"]), key=lambda m: m["name"])


def test_the_last_tray_runs_just_to_its_seam_and_is_a_tray_between_two_seams():
    from cladding_verge import verge_seam_out
    params, out = _one(elev={"verge_type": "cladding"})
    _p, g3 = _one()
    us = lambda o: sorted({round(q[0], 1) for m in o["geometry"] if m["ifc_type"] == "plank" for q in m["profile"]})   # noqa: E731
    assert us(out)[0] == -round(verge_seam_out(), 1) and us(out)[-1] == 8000.0 + round(verge_seam_out(), 1)   # to its seam, 3 mm out: no nose
    assert us(g3)[0] == -VERGE_TRAY and us(g3)[-1] == 8000.0 + VERGE_TRAY
    widths = sorted(max(q[0] for q in m["profile"]) - min(q[0] for q in m["profile"]) for m in out["geometry"] if m["ifc_type"] == "plank")
    assert widths[0] >= 100.0 and widths[-1] <= _parse_width(params) + 0.5   # every tray a tray: none over the seam centres
    info = out["info"][0]["verges"]
    assert [v["type"] for v in info] == ["cladding", "cladding"] and all(v["last_seam"] is None for v in info)


def _parse_width(params):
    from cladding_constants import _parse
    return _parse(dict(params, elevations=[]))["plank_w"]


def test_the_check_says_which_verge_and_does_not_hold_it_to_the_g3_seam_rule():
    params, out = _one(elev={"verge_type": "cladding"})
    (row,) = [r for r in check_rules(params, out["info"]) if r["name"].endswith("Verge")]
    assert row["status"] == "pass" and "VMZ-SN-MA-ZI-05" in row["message"] and "by 60 mm" in row["message"]
    _p, g3 = _one()
    (plain,) = [r for r in check_rules(_p, g3["info"]) if r["name"].endswith("Verge")]
    assert "a G3 verge" in plain["message"] and "ZI-05" not in plain["message"]


def test_the_verge_with_cladding_exports():
    params, out = _one(elev={"verge_type": "cladding"})
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    names = sorted(e.Name for e in ifc.by_type("IfcCovering") if "Verge" in (e.Name or ""))
    # the flashing and its strip are elements of their own; every sheet of the edge seam is in its owner's element,
    # the last tray's or the flashing's
    assert names == ["Roof A Verge Clip L", "Roof A Verge Clip R", "Roof A Verge Flashing L", "Roof A Verge Flashing R"]
    folds = [m for m in out["geometry"] if m.get("verge_seam")]
    assert len(folds) == 16 and not [m for m in folds if any(m is x for x in own_elements(out["geometry"]))]
    for e in ifc.by_type("IfcCovering"):
        if "Verge Flashing" in e.Name:
            # the flashing, its five sheets of the seam, and its two end pieces at the eaves corner
            assert sum(len(r.Items) for r in e.Representation.Representations) == 1 + 5 + 2
    assert len(out["info"][0]["verge_strips"]) == 2 and "CORNER_PROFILE" in meshes_to_dxf_string(out["geometry"], params, out["info"])


# ── the plywood oversails by the thickness of the cladding below ──────────

def _gable(roof, back=0.0, name="Gable", **settings):
    """An upright wall under the left edge of *roof*, looking out the way that verge does,
    its face *back* behind the roof's edge (the roof's edge that far out from it)."""
    f = roof["frame"]
    out = [-c for c in f["u"]]                                           # the left verge looks out along -u
    foot, head = frame_to_world(f, 0.0, 0.0, 0.0), frame_to_world(f, 0.0, roof["height"], 0.0)
    run = math.hypot(head[0] - foot[0], head[1] - foot[1])
    along = [(head[0] - foot[0]) / run, (head[1] - foot[1]) / run, 0.0]
    base = [foot[i] - out[i] * back for i in range(3)]
    q = [[base[0], base[1], foot[2] - 2500.0], [base[0] + along[0] * run, base[1] + along[1] * run, foot[2] - 2500.0],
         [base[0] + along[0] * run, base[1] + along[1] * run, foot[2]], [base[0], base[1], foot[2]]]
    wall = extract_elevation({"name": name, "faces": [[q[0], q[1], q[2]], [q[0], q[2], q[3]]], "outward": out, "context": [],
                              "options": {"debug": False, "clad_depth": 150.0}})
    return dict(wall, offset=0, settings=settings)


@pytest.mark.parametrize("wall_kw, thick", [(dict(cladding_type="panel"), 47.0),
                                            (dict(cladding_type="plank", plank_profile="seam", plank_orient="vertical"), 83.6)])
def test_the_plywood_oversails_the_edge_by_the_thickness_of_the_cladding_on_the_wall_below(wall_kw, thick):
    """As a sill board runs out over the cladding under a window: the roof's plywood, its last
    tray, the seam at its edge and the flashing all stand out past the roof's edge by the
    thickness of the wall's cladding (to the tips of its seams, where it is standing seam)."""
    from cladding_constants import _parse
    from cladding_primitives import buildup_depth
    from cladding_verge import VERGE_FACE, join_verges, verge_seam_out
    roof = dict(dict(_slope("Roof A"), offset=0), verge_type="cladding", settings=dict(SEAM))
    wall = _gable(roof, **wall_kw)
    pw = _parse(dict(wall_kw, elevations=[]))
    assert buildup_depth(pw) + (pw["seam_h"] if pw.get("seam") else 0.0) == pytest.approx(thick)
    params = {"elevations": [roof, wall], "trim": True, "splash": 0}
    out = generate_preview(params)
    info = next(i for i in out["info"] if i["elevation"] == "Roof A")
    left, right = sorted(info["verges"], key=lambda v: v["side"])
    assert (left["over"], left["over_from"]) == (pytest.approx(thick, abs=0.1), "Gable")
    assert (right["over"], right["over_from"]) == (0.0, None)             # nothing clad under the other edge: the plywood stops at it
    mine = [m for m in out["geometry"] if m["elevation"] == "Roof A"]
    deck = next(m for m in mine if m["ifc_type"] == "deck")
    assert min(q[0] for q in deck["profile"]) == pytest.approx(-thick, abs=0.1) and max(q[0] for q in deck["profile"]) == 8000.0
    assert min(q[0] for m in mine if m["ifc_type"] == "batten" for q in m["profile"]) >= 0.0          # the battens stay on the roof
    trays = [m for m in mine if m["ifc_type"] == "plank"]
    assert min(q[0] for m in trays for q in m["profile"]) == pytest.approx(-thick - verge_seam_out(), abs=0.1)
    assert max(max(q[0] for q in m["profile"]) - min(q[0] for q in m["profile"]) for m in trays) <= 530.5   # set out to where it ends
    flashing = next(m for m in mine if m["name"] == "Roof A Verge Flashing L")
    sec = _section(roof, flashing, 0.0, -1.0)
    assert max(x for x, _d in sec) == pytest.approx(thick + VERGE_FACE + 0.6, abs=0.1)                # in front of the wall's cladding
    (row,) = [r for r in check_rules(params, out["info"]) if r["name"].endswith("Verge") and "Roof A" in r["message"]]
    assert "oversails the roof's edge by %.0f mm on the left, the cladding on Gable below" % thick in row["message"]
    assert "no clad wall was found under the right edge" in row["message"]


def test_what_the_modelled_roof_already_oversails_comes_off_and_the_chain_can_set_it():
    from cladding_verge import join_verges
    roof = dict(dict(_slope("Roof A"), offset=0), verge_type="cladding", settings=dict(SEAM))
    for back, want in ((0.0, 47.0), (30.0, 17.0), (100.0, 0.0), (-40.0, 87.0)):
        params = {"elevations": [dict(roof), _gable(roof, back, cladding_type="panel")]}
        join_verges(params)
        assert params["elevations"][0]["verge_over"] == {"l": pytest.approx(want, abs=0.1)}, back
    far = {"elevations": [dict(roof), _gable(roof, 600.0, cladding_type="panel")]}        # too far behind the edge to be the wall under it
    join_verges(far)
    assert "verge_over" not in far["elevations"][0]
    fixed = {"elevations": [dict(roof, verge_oversail=75), _gable(roof, cladding_type="panel")]}
    join_verges(fixed)
    assert fixed["elevations"][0]["verge_over"] == {"l": 75.0, "r": 75.0} and fixed["elevations"][0]["verge_over_from"] == {"l": "set", "r": "set"}
    g3 = {"elevations": [dict(roof, verge_type="g3"), _gable(roof, cladding_type="panel")]}  # the G3 strip is not moved
    join_verges(g3)
    assert "verge_over" not in g3["elevations"][0]


def test_at_the_eaves_the_verge_drip_comes_down_to_the_back_of_the_eaves_drip_and_the_bullnoses_mitre():
    params, out = _one()
    a = params["elevations"][0]
    f = a["frame"]
    geo = out["geometry"]
    for side, edge, letter in ((-1.0, 0.0, "l"), (1.0, 8000.0, "r")):
        ends = [m for m in geo if m.get("drip_end") and m.get("verge_drip")
                and (world_to_frame(f, m["frame"]["origin"])[0] - edge) * side > -60.0]
        eaves_ends = [m for m in geo if m.get("drip_end") and m.get("eaves_info")
                      and (world_to_frame(f, m["frame"]["origin"])[0] - edge) * side > -60.0]
        assert sorted(m["name"].split(" Verge Drip ")[1] for m in ends) == ["Bullnose End", "Drop End"], letter
        assert sorted(m["name"].split(" Eaves Drip ")[1] for m in eaves_ends) == ["Bullnose End", "Drop End"], letter
        # every end piece is its tray's own sheet, sheared to end in its plane
        assert all(m["tray_sheet"] and m.get("of") and m.get("vshift") for m in ends + eaves_ends)
        bull_v = next(m for m in ends if "Bullnose" in m["name"])
        bull_e = next(m for m in eaves_ends if "Bullnose" in m["name"])
        # the two bullnoses end in one plane, square to the roof, between the eaves and the verge
        for m in (bull_v, bull_e):
            assert abs(sum(x * y for x, y in zip(m["frame"]["n"], f["n"]))) < 1e-6, "square to the roof"
        n1, n2 = bull_v["frame"]["n"], bull_e["frame"]["n"]
        assert abs(abs(sum(x * y for x, y in zip(n1, n2))) - 1.0) < 1e-6, "one plane"
        # level, up the slope; and along the eaves
        hv = [f["v"][0], f["v"][1], 0.0]
        hl = (hv[0] ** 2 + hv[1] ** 2) ** 0.5
        hv = [c / hl for c in hv]
        level = lambda m: [sum(p[i] * hv[i] for i in range(3)) for p in _points(m)]          # noqa: E731
        along = lambda m: [(world_to_frame(f, p)[0] - edge) * side for p in _points(m)]      # noqa: E731
        drop_v = next(m for m in ends if "Drop" in m["name"])
        drop_e = next(m for m in eaves_ends if "Drop" in m["name"])
        # the verge drop stops on the back of the eaves drop: no gap, and it does not pass through it
        face = min(level(drop_e))
        assert 0.0 < min(level(drop_v)) - face <= 1.5, "down to the back of the eaves drip (zinc is under 1.5 mm)"
        # the eaves drop runs out to the verge drop's outside face, covering its end
        assert max(along(drop_e)) == pytest.approx(max(along(drop_v)), abs=1.0), "the eaves drop covers its end"


def _points(m):
    from cladding_primitives import ring_at
    d = float(m["depth"])
    return [frame_to_world(m["frame"], u, v, s) for s in (0.0, d) for u, v in ring_at(m, 0, s)]


def test_the_setting_out_slides_so_each_verge_has_one_tray_in_range_not_two_narrow_ones():
    from cladding_geometry import seam_columns
    both = ((150.0, 370.0), (150.0, 370.0))
    # centred, 556 mm would be left at each verge: the run slides to 340 | 14 x 430 | 340, not 278 + 278
    w = [round(b - a) for a, b in seam_columns(6600.0, 430.0, 0.0, [], (0.0, 6600.0), (50.0, 50.0), both)]
    assert w == [340] + [430] * 14 + [340]
    # one verge: that end's tray is as wide as the detail allows
    w = [round(b - a) for a, b in seam_columns(6600.0, 430.0, 0.0, [], (0.0, 6600.0), (50.0, 0.0), ((150.0, 370.0), (0.0, 430.0)))]
    assert w[0] == 370 and w[1] == 430
    # whatever the width, every end tray at a verge is one tray in range
    for width in range(3000, 9000, 37):
        w = [b - a for a, b in seam_columns(float(width), 430.0, 0.0, [], (0.0, float(width)), (50.0, 50.0), both)]
        assert 149.5 <= w[0] <= 370.5 and 149.5 <= w[-1] <= 370.5, width
        assert all(abs(x - 430.0) < 0.5 for x in w[1:-1]) or len(w) <= 3, width


def test_verge_last_tray_full_lets_the_last_seam_stand_further_in_and_the_check_warns():
    found = False
    for offset in range(0, 430, 25):
        params, out = _one(elev={"verge_last": "full", "offset": float(offset)})
        infos = out["info"][0]["verges"]
        assert infos and all(v["wide"] for v in infos)
        seams = [v["last_seam"] for v in infos if v.get("last_seam") is not None]
        (row,) = [c for c in check_rules(params, out["info"]) if c["name"].endswith("Verge")]
        if any(x > 370.5 for x in seams):
            found = True
            assert row["status"] == "warn" and "supplier" in row["message"], row["message"]
            assert max(seams) <= 530.0 + 0.5               # never more than a whole tray (the default 530)
        else:
            assert row["status"] == "pass", row["message"]
    assert found, "some offset puts the last seam past 370 with a full tray allowed"


def test_with_cladding_the_eaves_corner_is_closed_as_the_g3_one():
    """The verge with cladding at the eaves: the flashing's part over the edge mitres to the
    eaves bullnose, its drop comes down to the back of the eaves drip's drop, the clip is cut
    with it, and the eaves drip runs out to the flashing's outside."""
    params, out = _one(elev={"verge_type": "cladding"})
    geo = out["geometry"]
    names = sorted(m["name"] for m in geo if m.get("drip_end") and m.get("ifc_type") == "verge_strip")
    assert names == ["Roof A Verge Clip L Drop End", "Roof A Verge Clip R Drop End",
                     "Roof A Verge Flashing L Bullnose End", "Roof A Verge Flashing L Drop End",
                     "Roof A Verge Flashing R Bullnose End", "Roof A Verge Flashing R Drop End"]
    assert all(m["of"] in (m["name"].rsplit(" ", 2)[0],) for m in geo if m.get("drip_end") and m.get("ifc_type") == "verge_strip")
    eaves_ends = [m for m in geo if m.get("drip_end") and " Eaves Drip " in m["name"]]
    assert len(eaves_ends) == 4                                          # bullnose and drop, at each verge
