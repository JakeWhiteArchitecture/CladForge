"""Saved states: chains, elevations, their surfaces and settings to XML and back, exactly."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from synthetic import payload, corner_payload  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402
import cladding_state as cs  # noqa: E402


@pytest.fixture(scope="module")
def surfaces():
    return extract_elevation(payload()), extract_elevation(corner_payload())


def _snapshot(a, b):
    """What app.js snapshotState() hands over: plain data, as it arrives through JSON."""
    member = lambda s, start, **kw: dict({  # noqa: E731
        "name": s["name"], "storey": "Ground Floor", "start": start, "rev": False, "link": None, "offset": 12.5,
        "cornerLo": 0, "cornerHi": 1.0, "masterLo": False, "masterHi": True, "detailLo": None, "detailHi": "lap",
        "clipLo": 0, "clipHi": None, "cover": None, "panelRows": [600, 1200.5], "panelJoints": [[0, 4, 3200, 4392.5, 1355]],
        "openingDetails": {"2000,900": {"jamb": "lap", "jamb_master": "face"}}, "manual": [2750.0],
        "disabled": {"slab:Balcony:3200": True},
        "picks": [{"element": "South wall", "faces": 2, "normal": [0.5, -0.866025, 0], "point": [1, 2, 3]}],
        "surface": s}, **kw)
    return json.loads(json.dumps({
        "app": "CladForge", "saved": "2026-09-28T21:00:00.000Z",
        "model": {"file": "house.ifc", "offset": [10250, -3400, 0], "context": {"project": "Kiln Lane", "site": ""}},
        "seq": 1, "active": "Elevation A",
        "settings": {"panel_w": 1200, "panel_h": 2400, "kerf": 3, "board_w": 1250, "board_h": 2500, "rotate": True,
                     "live-trim": True, "cladding-type": "panel", "plank-orient": "horizontal", "counter_batten": "auto"},
        "chains": [{"name": "Chain 1", "built": True, "topZ": None, "bottomZ": 250.0,
                    "edges": {"top": 10, "side": 0, "bottom": 10, "gap": 10, "vent": "front", "air": 10},
                    "datumFrom": "Elevation A",
                    "members": [member(a, 0), member(dict(b, name="Elevation B"), a["width"], cornerLo=1.0, cornerHi=0)]}]}))


def test_round_trip_is_exact(surfaces):
    snap = _snapshot(*surfaces)
    xml = cs.to_xml(snap)
    back = cs.from_xml(xml)
    assert back == snap
    # types survive: an int stays an int, a float a float, true/None/"" as they were
    m = back["chains"][0]["members"][0]
    assert m["panelRows"] == [600, 1200.5] and type(m["panelRows"][0]) is int and type(m["panelRows"][1]) is float
    assert back["settings"]["rotate"] is True and m["link"] is None and back["model"]["context"]["site"] == ""
    # an opening key is not an XML name, so it rides as an attribute
    assert m["openingDetails"] == {"2000,900": {"jamb": "lap", "jamb_master": "face"}}
    assert '<entry key="2000,900" t="object">' in xml


def test_the_file_is_readable(surfaces):
    xml = cs.to_xml(_snapshot(*surfaces))
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<CladForgeState format="cladforge-state" version="1">')
    for tag in ("<chain t=\"object\">", "<elevation t=\"object\">", "<surface t=\"object\">", "<polygon t=\"object\">",
                "<abutment t=\"object\">", "<origin t=\"nums\">"):
        assert tag in xml, tag
    assert '<exterior t="points">4500.0,2200.0 8000.0,2200.0' in xml
    assert "not a quantity take-off for pricing" in xml


def test_a_loaded_surface_builds_the_same_cladding(surfaces):
    """The surface in the file is the surface: building from it gives exactly the cladding
    the original gave, with no IFC and no extraction."""
    snap = _snapshot(*surfaces)
    back = cs.from_xml(cs.to_xml(snap))
    def build(state):
        m = state["chains"][0]["members"][0]
        rec = dict(m["surface"], offset=m["offset"], panel_rows=m["panelRows"], panel_joints=m["panelJoints"],
                   opening_details=m["openingDetails"])
        return generate_preview({"elevations": [rec], "cladding_type": "panel", "trim": True})
    assert json.dumps(build(back), sort_keys=True) == json.dumps(build(snap), sort_keys=True)


@pytest.mark.parametrize("text, why", [
    ("not xml at all", "not an XML file"),
    ('<svg xmlns="http://www.w3.org/2000/svg"/>', "not a CladForge state"),
    ('<CladForgeState format="cladforge-state" version="9"/>', "newer CladForge"),
    ('<CladForgeState format="cladforge-state" version="1"><seq t="num">x</seq></CladForgeState>', "damaged"),
    ('<CladForgeState format="cladforge-state" version="1"><seq t="blob">1</seq></CladForgeState>', "unknown value type"),
])
def test_anything_else_is_refused_with_a_reason(text, why):
    res = cs.load(text)
    assert res["ok"] is False and why in res["error"], res


def test_load_for_the_browser(surfaces):
    res = cs.load(cs.to_xml(_snapshot(*surfaces)))
    assert res["ok"] and res["state"]["chains"][0]["members"][1]["name"] == "Elevation B"
