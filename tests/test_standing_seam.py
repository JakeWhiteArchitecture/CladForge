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


@pytest.mark.parametrize("centres", [430, 530])
def test_vertical_trays_on_a_deck_on_vertical_battens(wall, centres):
    params, out = _build(wall, plank_orient="vertical", seam_centres=centres)
    p = _parse(params)
    trays, seams = _of(out, "plank"), _of(out, "seam")
    assert {round(_span(m, 0)[1] - _span(m, 0)[0]) for m in trays} <= {float(centres)} | {round(w) for w in range(1, centres)}
    assert max(round(_span(m, 0)[1] - _span(m, 0)[0]) for m in trays) == centres
    assert all("Tray" in m["name"] for m in trays)
    # battens vertical, under one deck and one membrane that follow the outline, window cut out
    assert all(_span(m, 1)[1] - _span(m, 1)[0] > _span(m, 0)[1] - _span(m, 0)[0] for m in _of(out, "batten"))
    (deck,), (membrane,) = _of(out, "deck"), _of(out, "membrane")
    assert len(deck["holes"]) == 2 and deck["thickness"] == 22 and membrane["depth"] == deck["depth"] + 22   # window and pipe
    assert all(abs(m["depth"] - (membrane["depth"] + 1.0)) < 1e-6 for m in trays)
    # every tray but those at the left edge has its seam: a 5 mm stem and a 12 mm head, 25 in all
    stems = [m for m in seams if not m["name"].endswith("head")]
    heads = [m for m in seams if m["name"].endswith("head")]
    assert len(stems) == len(heads) == len([m for m in trays if _span(m, 0)[0] > 1.0])
    assert all(round(_span(m, 0)[1] - _span(m, 0)[0], 3) == 5.0 for m in stems)
    assert all(round(_span(m, 0)[1] - _span(m, 0)[0], 3) == 12.0 for m in heads)
    assert all(abs(s["thickness"] + h["thickness"] - 25.0) < 1e-6 for s, h in zip(stems, heads))
    face = trays[0]["depth"] + trays[0]["thickness"]
    assert abs(buildup_depth(p) - face) < 1e-6


def test_horizontal_trays_seam_along_the_tray_below(wall):
    _params, out = _build(wall, plank_orient="horizontal", seam_centres=430)
    trays, seams = _of(out, "plank"), _of(out, "seam")
    assert max(round(_span(m, 1)[1] - _span(m, 1)[0]) for m in trays) == 430
    stems = [m for m in seams if not m["name"].endswith("head")]
    assert stems and all(round(_span(m, 1)[1] - _span(m, 1)[0], 3) == 5.0 for m in stems)     # running along u
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
    assert ("IfcPlate", "Deck boarding") in kinds and ("IfcCovering", "Breather membrane") in kinds
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
    for part in ("stem", "head"):
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
    head = next(m for m, *_r, jl, jh in _ends(out, "head") if not jh)
    k = 1 if head["seam_v"] else 0
    top = ring_at(head, 0, head["depth"] + head["thickness"])
    assert _span(head, k)[1] - max(q[k] for q in top) == pytest.approx(sh)


@pytest.mark.parametrize("orient", ["vertical", "horizontal"])
def test_flat_seam_ends_stop_short_and_lie_on_the_tray(wall, orient):
    params, out = _build(wall, plank_orient=orient, trim=True, seam_end="flat", seam_t=0.7, seam_h=38)
    tabs = [m for m in _of(out, "seam") if m.get("seam_part") == "end"]
    stems = _ends(out, "stem")
    free = sum((not jl) + (not jh) for _m, _k, _lo, _hi, jl, jh in stems)
    assert tabs and len(tabs) == free
    assert not any(m.get("vshift") for m in _of(out, "seam"))
    trays = {m["name"]: m for m in _of(out, "plank")}
    assert sum(abs(_span(t, 0 if t["seam_v"] else 1)[1] - _span(t, 0 if t["seam_v"] else 1)[0] - 38) < 0.01
               for t in tabs) > len(tabs) / 2      # all but those trimmed at the wall's edge
    for t in tabs:
        k = 1 if t["seam_v"] else 0
        along, across = _span(t, k), _span(t, 1 - k)
        assert along[1] - along[0] == pytest.approx(40.0)
        assert across[1] - across[0] <= 38.0 + 1e-6                  # the seam height, laid flat
        assert t["profile"]
        assert t["thickness"] == pytest.approx(2.8)                   # four thicknesses of sheet
        assert t["depth"] == pytest.approx(t["seam_face"])            # on the tray
        tray = trays[t["name"].split(" Seam")[0]]
        assert t["depth"] == pytest.approx(tray["depth"] + tray["thickness"])
    # each stem stops 40 short of where its tray runs out; where the two meet it runs on
    short = 0
    for m, k, lo, hi, jl, jh in stems:
        t_lo, t_hi = _span(trays[m["name"].split(" Seam")[0]], k)
        assert lo >= t_lo - 0.01 and hi <= t_hi + 0.01
        short += (not jl and abs(lo - 40 - t_lo) < 0.5) + (not jh and abs(hi + 40 - t_hi) < 0.5)
        if jh:
            assert hi == pytest.approx(t_hi)
    assert short > free / 2
