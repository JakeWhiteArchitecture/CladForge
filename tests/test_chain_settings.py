"""Each chain carries its own cladding settings on its elevation records ("settings"): the
job's params are the defaults, a chain's go over them, and one chain's choices never change
another's. Two separate chains here: one panelled, one in vertical standing seam."""
import contextlib
import copy
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from synthetic import payload  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_preview import generate_preview, check_rules, parse_for, splice_trial  # noqa: E402

PANELS = {"cladding_type": "panel", "panel_w": 1200}
SEAM = {"cladding_type": "plank", "plank_profile": "seam", "plank_orient": "vertical", "seam_centres": 430}


@pytest.fixture(scope="module")
def walls():
    with contextlib.redirect_stdout(io.StringIO()):
        e = extract_elevation(payload())
    a = dict(copy.deepcopy(e), name="A", chain="Chain 1")
    b = dict(copy.deepcopy(e), name="B", chain="Chain 2")
    return a, b


def _build(a, b, sa, sb, **job):
    params = dict({"elevations": [dict(a, settings=sa), dict(b, settings=sb)], "trim": False}, **job)
    with contextlib.redirect_stdout(io.StringIO()):
        return params, generate_preview(params)


def _kinds(out, name):
    return {m["ifc_type"] for m in out["geometry"] if m["elevation"] == name}


def test_each_chain_is_built_with_its_own_settings(walls):
    a, b = walls
    params, out = _build(a, b, PANELS, SEAM)
    assert "panel" in _kinds(out, "A") and "seam" not in _kinds(out, "A")
    assert "plank" in _kinds(out, "B") and "seam" in _kinds(out, "B") and "panel" not in _kinds(out, "B")
    assert {i["elevation"] for i in out["info"]} == {"A", "B"}
    names = [m["name"] for m in out["geometry"]]
    assert len(names) == len(set(names))                            # still unique across the chains
    # the job's own settings are only the defaults: a chain's go over them
    _p, out2 = _build(a, b, PANELS, SEAM, cladding_type="plank", panel_w=600)
    assert [m["profile"] for m in out2["geometry"] if m["elevation"] == "A"] == \
        [m["profile"] for m in out["geometry"] if m["elevation"] == "A"]


def test_changing_one_chain_leaves_the_other_alone(walls):
    a, b = walls
    _p, before = _build(a, b, PANELS, SEAM)
    _p, after = _build(a, b, PANELS, dict(SEAM, seam_centres=530))
    geo = lambda out, n: [(m["name"], m["profile"]) for m in out["geometry"] if m["elevation"] == n]
    assert geo(before, "A") == geo(after, "A")
    assert geo(before, "B") != geo(after, "B")


def test_checks_cutting_plan_and_exports_follow_each_chain(walls, tmp_path):
    import ifcopenshell
    from ifc_generator import meshes_to_ifc
    from dxf_generator import meshes_to_dxf_string
    a, b = walls
    params, out = _build(a, b, PANELS, SEAM)
    checks = check_rules(params, out["info"])
    assert any(c["name"].startswith("Chain 1 · ") for c in checks)
    assert any(c["name"] == "Chain 2 · Deck span" for c in checks)     # standing seam's own check
    assert not any(c["name"] == "Chain 1 · Deck span" for c in checks)
    assert parse_for(params, ["A"])["cladding_type"] == "panel" and parse_for(params, ["B"])["seam"]
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    kinds = {e.ObjectType for e in ifc.by_type("IfcCovering")}
    assert "Standing seam tray" in kinds and "Standing seam" in kinds
    desc = {e.Name: e.Description for e in ifc.by_type("IfcElementAssembly")}
    assert "panel buildup" in desc["A cladding"] and "plank buildup" in desc["B cladding"]
    mats = {m.Name for m in ifc.by_type("IfcMaterial")}
    assert "Standing seam sheet" in mats
    dxf = meshes_to_dxf_string(out["geometry"], params, out["info"])
    assert "CLADDING" in dxf and "Panels: " in dxf and "Planks: " in dxf     # each face's own schedule


def test_a_splice_trial_tries_its_own_chain(walls):
    a, b = walls
    boards = {"cladding_type": "plank", "plank_orient": "horizontal"}
    params = {"elevations": [dict(a, settings=PANELS), dict(b, settings=dict(boards, plank_splice=0))], "trim": False}
    lo = splice_trial(params, ["B"], 1.0)
    params["elevations"][1]["settings"] = dict(boards, plank_splice=100)
    hi = splice_trial(params, ["B"], 1.0)
    assert lo["splice"] == 0 and hi["splice"] == 100 and hi["piece"] < lo["piece"]
