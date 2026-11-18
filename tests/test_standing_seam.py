"""Standing seam: a plank profile. Trays a seam-centre wide (430 or 530) on a breather
membrane over a continuous deck on vertical battens, run and joined like planks, each seam
modelled as its closed upstands (a stem) and double fold (a head to one side)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contextlib  # noqa: E402
import io  # noqa: E402

import pytest  # noqa: E402

from synthetic import payload  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
from cladding_constants import _parse  # noqa: E402
from cladding_primitives import buildup_depth  # noqa: E402
from cladding_nesting import chain_plan  # noqa: E402


@pytest.fixture(scope="module")
def wall():
    with contextlib.redirect_stdout(io.StringIO()):
        return extract_elevation(payload())          # 8000 x 3000, a window at 2000-3200 x 900-2100


def _build(wall, **kw):
    params = dict({"elevations": [wall], "cladding_type": "plank", "plank_profile": "seam", "trim": False}, **kw)
    with contextlib.redirect_stdout(io.StringIO()):
        return params, generate_preview(params)


def _of(out, kind):
    return [m for m in out["geometry"] if m["ifc_type"] == kind]


def _span(m, k):
    return min(q[k] for q in m["profile"]), max(q[k] for q in m["profile"])


def test_only_the_standard_centres_and_seam_heights():
    assert _parse({"plank_profile": "seam", "seam_centres": 470})["plank_w"] == 430
    assert _parse({"plank_profile": "seam", "seam_centres": 500})["plank_w"] == 530
    p = _parse({"plank_profile": "seam", "seam_h": 30, "seam_t": 0.7, "seam_deck": "ply"})
    assert p["seam_h"] == 25 and p["plank_t"] == 0.7 and p["plank_gap"] == 0 and p["deck_t"] == 18
    assert p["cover"] == 530 and p["battens"] == "vertical" and not p["has_cb"]
    # a board profile is untouched
    assert not _parse({"plank_orient": "vertical"})["seam"] and _parse({"plank_orient": "vertical"})["battens"] == "horizontal"


PARTS = ("over_up", "over_cap", "over_outer", "over_turn", "over_lip", "under_up", "under_bend", "under_flange")


def _parts(out, part):
    return [m for m in _of(out, "seam") if m.get("seam_part") == part]


@pytest.mark.parametrize("centres", [430, 530])
def test_vertical_trays_on_a_deck_on_vertical_battens(wall, centres):
    params, out = _build(wall, plank_orient="vertical", seam_centres=centres, set_out_from_openings=False)
    p = _parse(params)
    trays = _of(out, "plank")
    assert {round(_span(m, 0)[1] - _span(m, 0)[0]) for m in trays} <= {float(centres)} | {round(w) for w in range(1, centres)}
    assert max(round(_span(m, 0)[1] - _span(m, 0)[0]) for m in trays) == centres
    assert all("Tray" in m["name"] for m in trays)
    assert all(m["opacity"] == 1.0 for m in trays)                       # metal: not see-through
    assert all(m["opacity"] == 1.0 for m in _of(out, "reveal"))
    # battens vertical, under one deck that follows the outline, window cut out; no membrane
    assert all(_span(m, 1)[1] - _span(m, 1)[0] > _span(m, 0)[1] - _span(m, 0)[0] for m in _of(out, "batten"))
    (deck,) = _of(out, "deck")
    assert not _of(out, "membrane")
    assert len(deck["holes"]) == 2 and deck["thickness"] == 22         # window and pipe
    assert all(abs(m["depth"] - (deck["depth"] + 22)) < 1e-6 for m in trays)
    # every tray but those at the left edge has its seam, as three solids
    stems = _parts(out, "under_up")
    assert {len(_parts(out, k)) for k in PARTS} == {len(stems)} and len(stems) >= len([m for m in trays if _span(m, 0)[0] > 1.0])
    # a seam only where two trays meet: none at the face's left edge, none beside the window
    assert all(_span(m, 0)[0] > 1.0 for m in stems)
    assert not any(_span(m, 1)[0] < 1500 < _span(m, 1)[1] and 2000 <= m["seam_line"] <= 3200 for m in stems)
    face = trays[0]["depth"] + trays[0]["thickness"]
    assert abs(buildup_depth(p) - face) < 1e-6


def _boxes_clear(piece):
    """No two sheets of a seam overlap (as boxes across the seam and out from the tray)."""
    bx = [(_span(m, 0), (m["depth"], m["depth"] + m["thickness"])) for m in piece.values()]
    for i, ((a0, a1), (b0, b1)) in enumerate(bx):
        for (c0, c1), (d0, d1) in bx[i + 1:]:
            if min(a1, c1) - max(a0, c0) > 1e-6 and min(b1, d1) - max(b0, d0) > 1e-6:
                return False
    return True


def test_the_seam_is_a_closed_double_lock(wall):
    """Both trays turn up into the seam, rolled rather than doubled back: the tray on the left
    the overcloak, outermost all the way (up, over the top, down the outside, round the foot
    of the head, back up inside); this tray the undercloak, up, over inside it and down
    between. Each a sheet thick, with air between them that makes up the overall size: the
    upstands 3 apart (the clip's room), the head 12 across and 17 deep, 25 high overall."""
    t = 0.6
    _params, out = _build(wall, plank_orient="vertical", set_out_from_openings=False)
    by = {k: _parts(out, k) for k in PARTS}
    assert len({len(v) for v in by.values()}) == 1
    for i, up in enumerate(by["under_up"]):
        line, face = up["seam_line"], up["seam_face"]
        piece = {k: by[k][i] for k in PARTS}
        assert {m["seam_line"] for m in piece.values()} == {line}
        assert {_span(m, 1) for m in piece.values()} == {_span(up, 1)}          # one length
        assert {m["seam_side"] for k, m in piece.items() if k.startswith("over")} == {"-"}
        assert {m["seam_side"] for k, m in piece.items() if k.startswith("under")} == {"+"}
        assert all(m["thickness"] == pytest.approx(t) or k in ("over_up", "under_up", "over_outer", "over_lip",
                   "under_flange") for k, m in piece.items())
        x = lambda k: tuple(round(v - line, 3) for v in _span(piece[k], 0))
        z = lambda k: (round(piece[k]["depth"] - face, 3), round(piece[k]["depth"] + piece[k]["thickness"] - face, 3))
        assert x("under_up")[0] - x("over_up")[1] == pytest.approx(3.0)              # the clip's room
        assert x("over_cap")[1] - x("over_cap")[0] == pytest.approx(12.0)            # the head, outside to outside
        assert z("over_cap") == pytest.approx((25 - t, 25.0)) and z("over_turn")[0] == pytest.approx(25 - 17)
        # across the head from the stem out: undercloak, air, overcloak lip, air, undercloak flange, air, overcloak outer
        order = [x(k) for k in ("over_up", "under_up", "over_lip", "under_flange", "over_outer")]
        gaps = [b[0] - a[1] for a, b in zip(order, order[1:])]
        assert all(g > 1.0 for g in gaps)
        assert gaps[1] == pytest.approx(gaps[2]) == pytest.approx(gaps[3])        # the folds evenly spaced
        # the undercloak's bend inside the cap, with the clip's room between; its end inside the turn
        assert z("over_cap")[0] - z("under_bend")[1] == pytest.approx(3.0)
        assert z("under_flange")[0] > z("over_turn")[1]
        assert x("over_turn")[0] <= x("over_lip")[0] and x("over_turn")[1] == pytest.approx(x("over_outer")[1])
        assert _boxes_clear(piece)
        assert min(m["depth"] for m in piece.values()) == pytest.approx(face)
    _params, out = _build(wall, plank_orient="vertical", seam_h=38, seam_t=0.8, set_out_from_openings=False)
    cap = _parts(out, "over_cap")[0]
    assert cap["depth"] + cap["thickness"] == pytest.approx(cap["seam_face"] + 38.0)
    assert _span(cap, 0)[1] - _span(cap, 0)[0] == pytest.approx(12.0)


def test_seams_on_the_reveal_corners_run_on_past_the_opening(wall):
    """Set out from the openings, each jamb (2000, 3200) is a seam line, carried up past the
    window head and down past its sill, and each span is split into equal trays within the
    seam centres: no closing cut."""
    _params, out = _build(wall, plank_orient="vertical", seam_centres=430)
    assert out["info"][0]["set_out_from_openings"]
    lines = {round(m["seam_line"], 1) for m in _parts(out, "over_up")}
    assert {2000.0, 3200.0} <= lines
    widths = sorted({round(_span(m, 0)[1] - _span(m, 0)[0], 1) for m in _of(out, "plank")})
    assert max(widths) <= 430 and widths == [400.0]           # 2000 = 5 x 400, 1200 = 3 x 400, 4800 = 12 x 400
    for jamb in (2000.0, 3200.0):
        spans = [_span(m, 1) for m in _parts(out, "over_up") if abs(m["seam_line"] - jamb) < 0.1]
        assert any(hi <= 900 + 1 for _lo, hi in spans) and any(lo >= 2100 - 1 for lo, _hi in spans)   # below and above
        assert not any(lo < 1500 < hi for lo, hi in spans)      # none beside the window: the tray turns into the reveal
    # free set-out instead: the seams at the regular centres
    _params, out = _build(wall, plank_orient="vertical", seam_centres=430, set_out_from_openings=False)
    assert not out["info"][0]["set_out_from_openings"]
    assert 2000.0 not in {round(m["seam_line"], 1) for m in _parts(out, "over_up")}


def test_horizontal_trays_seam_along_the_tray_below(wall):
    _params, out = _build(wall, plank_orient="horizontal", seam_centres=430)
    trays = _of(out, "plank")
    assert max(round(_span(m, 1)[1] - _span(m, 1)[0]) for m in trays) == 430
    stems = _parts(out, "over_up")
    assert stems and all(round(_span(m, 1)[1] - _span(m, 1)[0], 3) == 0.6 for m in stems)     # running along u
    # no seam along the bottom edge of the bottom course
    base = min(_span(m, 1)[0] for m in trays)
    assert all(_span(m, 1)[0] > base for m in stems)


def test_trays_pack_onto_their_longest_length_and_cross_joints_stagger(wall):
    params, out = _build(wall, plank_orient="vertical", seam_centres=430, plank_len=2000)
    plan = chain_plan(out["geometry"], _parse(params), ["Elevation A"], 1.0)
    assert plan["kind"] == "plank" and plan["board"] == [2000.0, 430.0]
    trays = _of(out, "plank")
    assert all(_span(m, 1)[1] - _span(m, 1)[0] <= 2000 + 1e-6 for m in trays)
    cols = {}
    for m in trays:
        cols.setdefault(round(_span(m, 0)[0]), []).append(_span(m, 1))
    # a joint is where two trays meet, not a gap where the roof crosses the run
    joints = [sorted(round((a[1] + b[0]) / 2) for a, b in zip(sorted(c), sorted(c)[1:]) if b[0] - a[1] < 5)
              for _u, c in sorted(cols.items())]
    assert any(joints) and all(not set(a) & set(b) for a, b in zip(joints, joints[1:]))


@pytest.mark.parametrize("end", ["folded", "flat"])
def test_trimmed_and_exported(wall, tmp_path, end):
    import ifcopenshell
    from ifc_generator import meshes_to_ifc
    from dxf_generator import meshes_to_dxf_string
    params, out = _build(wall, plank_orient="vertical", trim=True, seam_end=end)
    assert _of(out, "seam") and _of(out, "plank")
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    kinds = {(e.is_a(), e.ObjectType) for e in ifc.by_type("IfcElement")}
    assert ("IfcCovering", "Standing seam tray") in kinds and ("IfcCovering", "Standing seam") in kinds
    assert ("IfcPlate", "Deck boarding") in kinds and ("IfcCovering", "Breather membrane") not in kinds
    assert "CLADDING" in meshes_to_dxf_string(out["geometry"], params, out["info"])
    seams = [e for e in ifc.by_type("IfcCovering") if e.ObjectType == "Standing seam"]
    assert len(seams) == len(_of(out, "seam"))
    if end == "folded":     # the sloped ends go out as closed breps
        assert any(r.RepresentationType == "Brep" for e in seams for r in e.Representation.Representations)


def test_the_deck_is_what_spans_the_battens():
    from cladding_checks import check_rules
    for deck, centres, status in (("boards", 600, "pass"), ("ply", 600, "fail"), ("ply", 500, "pass")):
        checks = {c["name"]: c for c in check_rules({"cladding_type": "plank", "plank_profile": "seam", "seam_deck": deck,
                                                       "batten_centres": centres}, infos=[])}
        assert "Batten centres" not in checks and checks["Deck span"]["status"] == status


def _ends(out, part):
    """Each seam piece of *part*, with whether each of its ends meets another piece."""
    pieces = [m for m in _of(out, "seam") if m.get("seam_part") == part]
    out_ = []
    for m in pieces:
        k = 1 if m["seam_v"] else 0
        lo, hi = _span(m, k)
        mates = [_span(o, k) for o in pieces if o is not m and abs(o["seam_line"] - m["seam_line"]) < 0.1]
        out_.append((m, k, lo, hi, any(abs(h - lo) < 1 for _l, h in mates), any(abs(l - hi) < 1 for l, _h in mates)))
    return out_


@pytest.mark.parametrize("orient", ["vertical", "horizontal"])
def test_seams_fold_down_where_they_run_out_not_at_cross_joints(wall, orient):
    from cladding_primitives import ring_at
    params, out = _build(wall, plank_orient=orient, trim=True, plank_len=1500)    # short trays: cross joints
    assert _parse(params)["seam_end"] == "folded"
    sh, folded, joined = 25.0, 0, 0
    for part in PARTS:
        for m, k, lo, hi, joined_lo, joined_hi in _ends(out, part):
            face = m["seam_face"]
            for s in (m["depth"], m["depth"] + m["thickness"]):
                ring = ring_at(m, 0, s)
                back = s - face                 # 45°: pulled back as far as it stands up
                assert min(q[k] for q in ring) == pytest.approx(lo if joined_lo else lo + back, abs=0.01)
                assert max(q[k] for q in ring) == pytest.approx(hi if joined_hi else hi - back, abs=0.01)
            folded += (not joined_lo) + (not joined_hi)
            joined += joined_lo + joined_hi
    assert folded and joined          # both kinds of end are there
    # the head's top is drawn back the full seam height
    head = next(m for m, *_r, jl, jh in _ends(out, "over_cap") if not jh)
    k = 1 if head["seam_v"] else 0
    top = ring_at(head, 0, head["depth"] + head["thickness"])
    assert _span(head, k)[1] - max(q[k] for q in top) == pytest.approx(sh)


@pytest.mark.parametrize("orient", ["vertical", "horizontal"])
def test_flat_seam_ends_run_square_to_the_end_of_the_tray(wall, orient):
    params, out = _build(wall, plank_orient=orient, trim=True, seam_end="flat")
    assert not any(m.get("vshift") for m in _of(out, "seam"))
    trays = {m["name"]: m for m in _of(out, "plank")}
    flush = 0
    for part in PARTS:
        for m, k, lo, hi, _jl, _jh in _ends(out, part):
            t_lo, t_hi = _span(trays[m["name"].split(" Seam")[0]], k)
            assert t_lo - 0.01 <= lo < hi <= t_hi + 0.01
            flush += abs(lo - t_lo) < 0.01 or abs(hi - t_hi) < 0.01
    assert flush > len(_of(out, "seam")) / 2      # square, flush with the tray (not where a pipe cuts it)
