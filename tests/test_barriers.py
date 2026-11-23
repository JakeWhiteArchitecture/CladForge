"""Openings closed with solid timber cavity barriers on all four sides. The jamb closers
every opening has are joined by a barrier over the head and one under the sill, and every
upright batten that would join one of those stops short of it (200 mm at least), so the
bays above and below the opening still drain and vent sideways into the bays beside it."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import ifcopenshell  # noqa: E402

from synthetic import payload  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_checks import check_rules  # noqa: E402
from cladding_constants import _parse  # noqa: E402
from cladding_primitives import buildup_depth  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from ifc_generator import meshes_to_ifc  # noqa: E402

WINDOW = (2000.0, 3200.0, 900.0, 2100.0)   # the synthetic wall's structural opening: u0, u1, v0, v1
CW = 50.0
FRAME = (WINDOW[0] - CW, WINDOW[1] + CW, WINDOW[2] - CW, WINDOW[3] + CW)      # the barriers' outside
PANEL = dict(cladding_type="panel")
PLANK_H = dict(cladding_type="plank", plank_orient="horizontal")
PLANK_V = dict(cladding_type="plank", plank_orient="vertical")
SEAM = dict(cladding_type="plank", plank_profile="seam", plank_orient="vertical")


@pytest.fixture(scope="module")
def elevation():
    return extract_elevation(payload())


def _params(elevation, **kw):
    return dict({"elevations": [dict(elevation, offset=0)], "trim": True, "sheathing": True, "insulation": True,
                 "closer_w": CW, "barriers": True}, **kw)


def _box(m):
    us, vs = [q[0] for q in m["profile"]], [q[1] for q in m["profile"]]
    return min(us), max(us), min(vs), max(vs)


def _uprights(out, kinds=("batten", "counter_batten")):
    """The upright timbers, as (u0, u1, v0, v1)."""
    boxes = [_box(m) for m in out["geometry"] if m["ifc_type"] in kinds]
    return [b for b in boxes if b[3] - b[2] > b[1] - b[0]]


@pytest.mark.parametrize("clad", [PANEL, PLANK_H, PLANK_V, SEAM])
def test_the_opening_is_framed_on_all_four_sides(elevation, clad):
    params = _params(elevation, **clad)
    p = _parse(params)
    out = generate_preview(params)
    closers = {m["name"].split()[-1]: m for m in out["geometry"] if m["ifc_type"] == "closer"}
    assert sorted(closers) == ["1H", "1L", "1R", "1S"]
    assert _box(closers["1H"]) == (FRAME[0], FRAME[1], WINDOW[3], FRAME[3])        # over the head, out over the jambs'
    assert _box(closers["1S"]) == (FRAME[0], FRAME[1], FRAME[2], WINDOW[2])        # under the sill
    assert _box(closers["1L"]) == (FRAME[0], WINDOW[0], WINDOW[2], WINDOW[3])      # the jambs', as ever
    # all four fill the cavity alike
    depths = {(round(m["depth"], 3), round(m["thickness"], 3)) for m in closers.values()}
    assert len(depths) == 1
    if not p["seam"]:
        board = p["panel_t"] if p["cladding_type"] == "panel" else p["plank_t"]
        (start, t), = depths
        assert start == pytest.approx(p["sheathing_t"] + p["insulation_t"]) and start + t == pytest.approx(buildup_depth(p) - board)


def test_without_the_option_only_the_jambs_are_closed(elevation):
    out = generate_preview(_params(elevation, barriers=False, **PANEL))
    assert sorted(m["name"].split()[-1] for m in out["geometry"] if m["ifc_type"] == "closer") == ["1L", "1R"]
    assert not any(m.get("keep_clear") for m in out["geometry"]) and "barriers" not in out["info"][0]


@pytest.mark.parametrize("clad", [PANEL, PLANK_H, PLANK_V, SEAM])
@pytest.mark.parametrize("gap", [200.0, 350.0])
def test_uprights_stop_short_of_the_head_and_sill_barriers(elevation, clad, gap):
    out = generate_preview(_params(elevation, barrier_gap=gap, **clad))
    uprights = _uprights(out)
    over = [b for b in uprights if b[0] < FRAME[1] and b[1] > FRAME[0]]            # over or under the opening
    assert len(over) >= 2
    above, below = [b for b in over if b[2] > WINDOW[3]], [b for b in over if b[3] < WINDOW[2]]
    assert len(above) + len(below) == len(over), "none beside the opening, none through it"
    assert above and min(b[2] for b in above) == pytest.approx(FRAME[3] + gap, abs=0.01)
    assert below and max(b[3] for b in below) == pytest.approx(FRAME[2] - gap, abs=0.01)
    # the bays beside the opening run on past it, so there is somewhere for the air to go
    beside = [b for b in uprights if b not in over]
    assert any(b[2] < FRAME[2] and b[3] > FRAME[3] and b[1] <= FRAME[0] for b in beside)
    assert any(b[2] < FRAME[2] and b[3] > FRAME[3] and b[0] >= FRAME[1] for b in beside)
    got = out["info"][0]["barriers"]
    assert got == {"openings": 1, "gap": gap, "cut": got["cut"], "dpm": 8, "supports": got["supports"], "level_gap": got["level_gap"]}
    matrix = clad == PLANK_V                        # level battens on counter-battens
    assert (got["supports"], got["level_gap"]) == ((2, 100.0) if matrix else (0, 0.0))
    assert out["info"][0]["barriers"]["cut"] >= 1


@pytest.mark.parametrize("barriers", [False, True])
def test_an_opening_does_not_upset_the_batten_spacing(elevation, barriers):
    """Panels set out from the openings: a jamb line is backed by the cavity closer beside
    its opening, and over and under the opening it is a panel joint that wants a batten.
    Without one the battens there stood 1100 and 1200 mm apart, a bay and more."""
    out = generate_preview(_params(elevation, barriers=barriers, **PANEL))
    for v in (400.0, 2700.0):                                      # under the sill, over the head
        at = sorted((b[0] + b[1]) / 2.0 for b in _uprights(out, ("batten",)) if b[2] <= v <= b[3] and 900.0 < b[0] < 4000.0)
        assert WINDOW[0] in at and WINDOW[1] in at, at
        assert max(b - a for a, b in zip(at, at[1:])) <= 610.0, at
    # beside the opening the closer is the support: no batten stands in the jamb
    beside = [b for b in _uprights(out, ("batten",)) if b[2] < 1500.0 < b[3]]
    assert not any(b[0] < u < b[1] for b in beside for u in WINDOW[:2])


def test_the_gap_is_never_under_200(elevation):
    out = generate_preview(_params(elevation, barrier_gap=50, **PANEL))
    above = [b for b in _uprights(out) if b[0] < FRAME[1] and b[1] > FRAME[0] and b[2] > WINDOW[3]]
    assert min(b[2] for b in above) == pytest.approx(FRAME[3] + 200.0, abs=0.01)


def test_an_upright_hard_beside_the_frame_counts_as_joining_it(elevation):
    """A batten touching a jamb barrier's side would shut the way out of the strip under the
    sill, so it is cut like the ones over the opening."""
    out = generate_preview(_params(elevation, **PANEL))
    for u0, u1, v0, v1 in _uprights(out):
        if v0 < FRAME[3] + 199.0 and v1 > FRAME[2] - 199.0:               # passes the frame
            assert u1 <= FRAME[0] - 25.0 + 0.01 or u0 >= FRAME[1] + 25.0 - 0.01


@pytest.mark.parametrize("clad", [PANEL, PLANK_H, PLANK_V, SEAM])
def test_all_four_barriers_carry_a_damp_proof_membrane(elevation, clad):
    out = generate_preview(_params(elevation, **clad))
    timbers = {m["name"].split()[-1]: m for m in out["geometry"] if m["ifc_type"] == "closer"}
    part = lambda word: {m["name"].split()[-1]: m for m in out["geometry"] if m["ifc_type"] == "dpm" and m["name"].split()[-2] == word}   # noqa: E731
    dpm, tops, sides = part("DPM"), part("Top"), part("Side")
    assert sorted(dpm) == ["1H", "1L", "1R", "1S"] and sorted(tops) == ["1H", "1S"] and sorted(sides) == ["1L", "1R"]
    # down the window side of each jamb's timber: the face that looks into the opening
    for tag, m in sides.items():
        on = timbers[tag]
        u0, u1, v0, v1 = _box(on)
        left = tag.endswith("L")
        assert _box(m) == ((u1, u1 + 1.0, v0, v1) if left else (u0 - 1.0, u0, v0, v1))
        assert (u1 if left else u0) == (WINDOW[0] if left else WINDOW[1])
        assert m["depth"] == pytest.approx(on["depth"]) and m["depth"] + m["thickness"] == pytest.approx(on["depth"] + on["thickness"] + 1.0)
    # over the top of the head's and the sill's timber, where water would stand: the full
    # depth of the cavity, and turned over the face membrane's edge
    for tag, m in tops.items():
        on = timbers[tag]
        u0, u1, _v0, v1 = _box(on)
        assert _box(m) == (u0, u1, v1, v1 + 1.0)
        assert m["depth"] == pytest.approx(on["depth"]) and m["depth"] + m["thickness"] == pytest.approx(on["depth"] + on["thickness"] + 1.0)
    for tag, m in dpm.items():
        on = timbers[tag]
        assert _box(m) == _box(on), "the whole face of its timber"
        assert m["depth"] == pytest.approx(on["depth"] + on["thickness"]) and m["thickness"] == 1.0      # on its outer face
    # (with panels the jamb timbers had an EPDM gasket: the membrane takes its place)
    gaskets = [_box(m) for m in out["geometry"] if m["ifc_type"] == "gasket"]
    assert not any(g[0] < WINDOW[0] and g[1] > FRAME[0] and g[2] >= WINDOW[2] - 1 and g[3] <= WINDOW[3] + 1 for g in gaskets)
    assert not [m for m in generate_preview(_params(elevation, barriers=False, **clad))["geometry"] if m["ifc_type"] == "dpm"]


def test_gaskets_stop_with_their_battens(elevation):
    out = generate_preview(_params(elevation, **PANEL))
    gaskets = [_box(m) for m in out["geometry"] if m["ifc_type"] == "gasket"]
    over = [g for g in gaskets if g[0] < FRAME[1] and g[1] > FRAME[0] and g[1] - g[0] < 200.0
            and not (g[2] >= WINDOW[2] - 0.01 and g[3] <= WINDOW[3] + 0.01)]       # (not the jamb barriers' own)
    assert over and all(g[2] >= FRAME[3] + 199.99 or g[3] <= FRAME[2] - 199.99 for g in over)


def test_level_battens_are_cut_out_of_the_frame_not_short_of_it(elevation):
    """Vertical boards: the level battens end on the jamb barriers and are cut out of the
    head and sill ones, which are solid timber where they were; it is the uprights behind
    them, the counter-battens, that stop short. On this batten matrix a level batten also
    stands 100 mm clear of the head and sill barriers: the one the grid puts on top of the
    head barrier is cut out between the jamb closers, its ends on the uprights beside them."""
    out = generate_preview(_params(elevation, **PLANK_V))
    level = [b for b in (_box(m) for m in out["geometry"] if m["ifc_type"] == "batten") if b[1] - b[0] > b[3] - b[2]]
    inside = lambda b: b[0] < FRAME[1] - 0.5 and b[1] > FRAME[0] + 0.5 and b[2] < FRAME[3] - 0.5 and b[3] > FRAME[2] + 0.5   # noqa: E731
    assert level and not any(inside(b) for b in level)
    assert any(abs(b[1] - FRAME[0]) < 0.01 for b in level) and any(abs(b[0] - FRAME[1]) < 0.01 for b in level)
    # none runs across within 100 mm of the head barrier: the one there ends at the jamb closers' outsides
    assert not any(b[0] < FRAME[1] and b[1] > FRAME[0] and FRAME[3] - 0.5 <= b[2] < FRAME[3] + 100.0 for b in level)
    near = [b for b in level if FRAME[3] - 0.5 <= b[2] < FRAME[3] + 100.0]
    assert near and {round(b[1]) for b in near if b[1] <= FRAME[0] + 0.5} == {FRAME[0]} \
        and {round(b[0]) for b in near if b[0] >= FRAME[1] - 0.5} == {FRAME[1]}


def _joints(out):
    """Every end joint between two planks of a course: (u, v0, v1)."""
    planks = [_box(m) for m in out["geometry"] if m["ifc_type"] == "plank"]
    starts = {(round(b[0], 1), b[2]) for b in planks}
    return [(b[1] + 4.0, b[2], b[3]) for b in planks if (round(b[1] + 8.0, 1), b[2]) in starts]


def test_no_plank_end_joint_where_the_batten_was_cut_away(elevation):
    out = generate_preview(_params(elevation, plank_len=2400, **PLANK_H))
    assert out["info"][0]["unsupported_joints"] == 0
    battens = _uprights(out, ("batten",))
    joints = [q for q in _joints(out) if q[0] < 4400.0]            # (past that the model has an abutment of its own)
    assert len(joints) > 10
    for u, v0, v1 in joints:
        assert any(b[0] <= u <= b[1] and b[2] <= v0 + 0.01 and b[3] >= v1 - 0.01 for b in battens), (u, v0, v1)
    # boards too short to span the battens that were cut away are told so, not left on thin air
    short = generate_preview(_params(elevation, plank_len=1800, **PLANK_H))
    assert short["info"][0]["unsupported_joints"] > 0
    assert generate_preview(_params(elevation, plank_len=1800, barriers=False, **PLANK_H))["info"][0]["unsupported_joints"] == 0


def test_a_door_has_no_sill_barrier(elevation):
    """An opening whose sill is not within the cladding (a door, a window to the splash
    line) is closed on three sides; nothing is cut under it."""
    low = dict(elevation, clip_v_lo=WINDOW[2] + 100.0)         # the cladding starts above the sill
    out = generate_preview(dict(_params(elevation, **PANEL), elevations=[dict(low, offset=0)]))
    assert sorted(m["name"].split()[-1] for m in out["geometry"] if m["ifc_type"] == "closer") == ["1H", "1L", "1R"]


def test_one_opening_can_have_its_own_say(elevation):
    """The chain's setting is the default; an opening set on its own (the 2D view) keeps
    its choice either way."""
    key = "%.0f,%.0f" % (WINDOW[0], WINDOW[2])
    tags = lambda out: sorted(m["name"].split()[-1] for m in out["geometry"] if m["ifc_type"] == "closer")     # noqa: E731
    on = dict(elevation, offset=0, opening_details={key: {"barriers": True}})
    out = generate_preview(dict(_params(elevation, barriers=False, **PANEL), elevations=[on]))
    assert tags(out) == ["1H", "1L", "1R", "1S"] and out["info"][0]["opening_details"][0]["barriers"] is True
    off = dict(elevation, offset=0, opening_details={key: {"barriers": False}})
    out = generate_preview(dict(_params(elevation, barriers=True, **PANEL), elevations=[off]))
    assert tags(out) == ["1L", "1R"] and out["info"][0]["opening_details"][0]["barriers"] is False
    assert "barriers" not in out["info"][0]


def test_the_check_says_what_was_done(elevation):
    params = _params(elevation, **PANEL)
    out = generate_preview(params)
    (row,) = [r for r in check_rules(params, out["info"]) if r["name"] == "Cavity barriers"]
    assert row["status"] == "pass" and "all four sides" in row["message"] and "200 mm" in row["message"]
    assert not [r for r in check_rules(_params(elevation, barriers=False, **PANEL), generate_preview(
        _params(elevation, barriers=False, **PANEL))["info"]) if r["name"] == "Cavity barriers"]


def test_barriers_export(elevation):
    params = _params(elevation, **PANEL)
    out = generate_preview(params)
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    names = sorted(e.Name.split()[-1] for e in ifc.by_type("IfcMember") if "Cavity" in e.Name)
    assert names == ["1H", "1L", "1R", "1S"]
    membranes = [e for e in ifc.by_type("IfcCovering") if "Barrier DPM" in e.Name]
    assert len(membranes) == 8 and {e.PredefinedType for e in membranes} == {"MEMBRANE"}
    assert len([e for e in ifc.by_type("IfcElement") if not e.is_a("IfcElementAssembly")]) == len(out["geometry"])


def test_on_a_batten_matrix_level_battens_stand_off_and_the_jambs_carry_their_ends(elevation):
    """Level battens on upright counter-battens: none within 100 mm of the head or sill barrier
    (one nearer is cut out from jamb closer to jamb closer), and an upright counter-batten tight
    to each jamb closer carries the level battens' ends there, from below the sill's gap to above
    the head's."""
    from cladding_geometry import BARRIER_LEVEL_GAP
    params = _params(elevation, counter_batten="yes", **PLANK_V)
    out = generate_preview(params)
    level = [_box(m) for m in out["geometry"] if m["ifc_type"] == "batten"]
    level = [b for b in level if b[1] - b[0] > b[3] - b[2]]
    over = [b for b in level if b[0] < FRAME[1] and b[1] > FRAME[0]]          # running across the opening's width
    for u0, u1, v0, v1 in over:
        assert v0 >= FRAME[3] + BARRIER_LEVEL_GAP - 0.5 or v1 <= FRAME[2] - BARRIER_LEVEL_GAP + 0.5
    supports = {m["name"][-2:]: _box(m) for m in out["geometry"] if m.get("barrier_support")}
    assert sorted(supports) == ["1L", "1R"]
    assert supports["1L"][1] == FRAME[0] and supports["1R"][0] == FRAME[1]     # tight to the closers
    for u0, u1, v0, v1 in level:                                               # every level end at a jamb lands on one
        for end in (u0, u1):
            if abs(end - FRAME[0]) < 0.5:
                assert supports["1L"][2] <= v0 and v1 <= supports["1L"][3]
            if abs(end - FRAME[1]) < 0.5:
                assert supports["1R"][2] <= v0 and v1 <= supports["1R"][3]
    assert out["info"][0]["barriers"]["supports"] == 2


def test_without_counter_battens_nothing_changes(elevation):
    out = generate_preview(_params(elevation, **PLANK_H))
    assert not [m for m in out["geometry"] if m.get("barrier_support")]
    assert out["info"][0]["barriers"]["level_gap"] == 0.0


def test_a_batten_grazed_by_the_boards_edge_offset_at_a_notch_stays_whole(elevation):
    """The synthetic wall's outline has a notch (a slab through it, 1400 to 1600 high, from the
    corner to 1500). The boards stand their edge offset (10 mm) off it, and the level battens
    either side of it, which only touch that offset, were shaved 10 mm along their edge for
    1.5 m: a step in the batten. They are left whole; a batten is only ever made whole within
    the face's own outline, never into an opening."""
    from cladding_booleans import _no_slivers
    from shapely.geometry import Polygon, box
    out = generate_preview(_params(elevation, counter_batten="yes", **PLANK_V))
    for m in out["geometry"]:
        if m["ifc_type"] == "batten":
            assert len(m["profile"]) == 4, (m["name"], m["profile"])
    # into an opening: not made whole, the shave stays
    shaved = box(0, 1350, 1500, 1390).union(box(1500, 1350, 3000, 1400))
    face = Polygon([(0, 0), (3000, 0), (3000, 3000), (0, 3000)], [[(200, 1395), (1400, 1395), (1400, 2000), (200, 2000)]])
    kept = _no_slivers(shaved, [[0, 1350], [3000, 1350], [3000, 1400], [0, 1400]], face)
    assert kept.bounds[3] == 1400 and abs(kept.area - shaved.area) < 1.0
    # a sliver (10 of 50 mm) is cut out
    sliver = box(0, 1350, 1500, 1360).union(box(1500, 1350, 3000, 1400))
    assert _no_slivers(sliver, [[0, 1350], [3000, 1350], [3000, 1400], [0, 1400]], None).bounds[0] == 1500


def test_a_batten_s_strips_are_joined_on_the_grid():
    """The strips a batten is cut into share their edges, and where a cut's corner is a hair
    off another's (here 0.004 mm) a plain union can throw, which kills the engine in the
    browser. They are joined on the 0.01 mm grid: one clean batten, whole where it was shaved."""
    from cladding_booleans import _no_slivers
    from shapely.geometry import Polygon
    profile = [[0, 1350], [3000, 1350], [3000, 1400], [0, 1400]]
    shaved = Polygon([(0, 1350), (3000, 1350), (3000, 1400), (1500.004, 1400), (1500.004, 1390.001),
                      (1500, 1390), (0, 1390)])
    kept = _no_slivers(shaved, profile, None)
    assert kept.is_valid and kept.geom_type == "Polygon"
    assert kept.bounds == (0.0, 1350.0, 3000.0, 1400.0) and abs(kept.area - 3000 * 50) < 1.0
