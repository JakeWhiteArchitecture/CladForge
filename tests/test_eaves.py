"""The eaves: the level bottom edge of a roof slope in standing seam. The trays and their
seams run out over an eaves apron, the layers under them run to the roof's edge, and the
chain can switch it off. No drawing was supplied for it: its sizes are assumptions."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import ifcopenshell  # noqa: E402

from cladding_checks import check_rules  # noqa: E402
from cladding_constants import frame_to_world  # noqa: E402
from cladding_eaves import EAVES_TRAY, EAVES_NOSE, EAVES_LAP, EAVES_DROP, has_eaves  # noqa: E402
from cladding_constants import _parse  # noqa: E402
from cladding_primitives import ring_at  # noqa: E402
from dxf_generator import meshes_to_dxf_string  # noqa: E402
from ifc_generator import meshes_to_ifc  # noqa: E402
from test_hip import _build as _hipped, _pair, SEAM  # noqa: E402
from test_verge import _one  # noqa: E402


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
    assert min(vs) == pytest.approx(-EAVES_NOSE, abs=1.0) and max(vs) == pytest.approx(EAVES_LAP, abs=1.0)
    assert max(ds) <= face["depth"] + 0.01                                   # under the trays
    assert max(ds) - min(ds) == pytest.approx(EAVES_DROP + 10.0, abs=4.0)    # down the drop and its drip
    assert -EAVES_NOSE < -EAVES_TRAY + 10.0                                  # the trays' ends lap the nose


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
