"""Panel rows of different heights: stacking, closing cut, noggins, dimensions, scope."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from synthetic import payload, corner_payload  # noqa: E402
from fabric_extract import extract_elevation  # noqa: E402
from cladding_preview import generate_preview, check_rules  # noqa: E402
from cladding_primitives import panel_rows, stacked_positions  # noqa: E402
from dxf_generator import meshes_to_dxf_string  # noqa: E402

GAP = 10.0


@pytest.fixture(scope="module")
def elevation():
    return extract_elevation(payload())      # 8000 x 3000, panels start at 150 over the base splash


def _build(elevation, rows=None, **kw):
    params = dict({"elevations": [dict(elevation, offset=0, panel_rows=rows)], "cladding_type": "panel",
                   "panel_h": 1200, "panel_gap": GAP, "trim": False}, **kw)
    return generate_preview(params), params


def _rows_drawn(out, name="Elevation A"):
    """(bottom, height) of each row, read off the panels in column 1."""
    got = []
    for m in out["geometry"]:
        if m["ifc_type"] == "panel" and m["name"].startswith(name) and m["name"].endswith("-1"):
            vs = [q[1] for q in m["profile"]]
            got.append((round(min(vs), 1), round(max(vs) - min(vs), 1)))
    return sorted(got)


def test_no_list_behaves_as_before(elevation):
    out, _ = _build(elevation)
    starts = stacked_positions(150.0, 3000.0, 1200.0 + GAP)
    assert [v for v, _h in _rows_drawn(out)] == starts
    assert out["info"][0]["rows"] == [1200.0, 1200.0, 3000.0 - starts[-1]]
    assert not [d for d in out["dimensions"] if d.get("kind") == "course"]


def test_mixed_row_heights(elevation):
    out, _ = _build(elevation, [600, 1200, 300])
    # 150 + 600 + 10 = 760, + 1200 + 10 = 1970, + 300 + 10 = 2280; then a 1200 row will
    # not fit under 3000, so the rest is the closing cut.
    assert _rows_drawn(out) == [(150.0, 600.0), (760.0, 1200.0), (1970.0, 300.0), (2280.0, 720.0)]
    info = out["info"][0]
    assert info["n_courses"] == 4 and info["closing_cut_top"] == 720.0
    names = {m["name"] for m in out["geometry"] if m["ifc_type"] == "panel"}
    assert "Elevation A Panel R2-3" in names and not any(" C1-" in n for n in names)
    rows = sorted((d for d in out["dimensions"] if d.get("kind") == "row"), key=lambda d: d["row"])
    assert [(d["row"], d["value"], d["label"]) for d in rows] == [(0, 600.0, "R1 600"), (1, 1200.0, "R2 1200"),
                                                                  (2, 300.0, "R3 300")]
    cut = [d for d in out["dimensions"] if d["label"] == "Cut 720"]
    assert cut and "kind" not in cut[0]          # the closing row is read-only


def test_a_list_shorter_than_the_wall_carries_on_at_panel_h(elevation):
    out, _ = _build(elevation, [500])
    assert _rows_drawn(out) == [(150.0, 500.0), (660.0, 1200.0), (1870.0, 1130.0)]


def test_a_list_taller_than_the_wall_is_cut_at_the_top(elevation):
    out, _ = _build(elevation, [1000, 1000, 1000, 1000])
    assert _rows_drawn(out) == [(150.0, 1000.0), (1160.0, 1000.0), (2170.0, 830.0)]
    assert out["info"][0]["rows"] == [1000.0, 1000.0, 830.0]
    assert len([d for d in out["dimensions"] if d.get("kind") == "row"]) == 2


def test_noggins_at_every_row_joint_with_counter_battens(elevation):
    def seams(cb):
        out, _ = _build(elevation, [600, 1200, 300], counter_batten=cb)
        return sorted({round(sum(q[1] for q in m["profile"]) / 4, 1)
                       for m in out["geometry"] if m["ifc_type"] == "cross_batten"})
    assert seams("yes") == [755.0, 1965.0, 2275.0]        # centred in each 10 mm joint
    assert seams("no") == []                               # the drainage rule still holds


def test_rows_are_clamped_and_flagged(elevation):
    assert [r[:2] for r in panel_rows([100, 5000], 1200, 0, 0, 10000)[0][:2]] == [(0.0, 150.0), (150.0, 3000.0)]
    out, params = _build(elevation, [100])
    assert _rows_drawn(out)[0] == (150.0, 150.0)
    checks = [c for c in check_rules(params, out["info"]) if c["name"] == "Panel rows"]
    assert checks and "row 1" in checks[0]["message"], checks
    # A closing row under 100 at the top is flagged too: 150 + 2780 + 10 leaves 60.
    out, params = _build(elevation, [2780])
    assert out["info"][0]["closing_cut_top"] == 60.0
    assert any("closing row" in c["message"] for c in check_rules(params, out["info"]) if c["name"] == "Panel rows")
    out, params = _build(elevation, [600, 1200])
    assert not [c for c in check_rules(params, out["info"]) if c["name"] == "Panel rows"]


def test_chain_scope_and_elevation_scope():
    a, b = extract_elevation(payload()), extract_elevation(corner_payload())
    def build(rows_a, rows_b):
        ra = dict(a, chain="Chain 1", chain_start=0, chain_reversed=False, offset=0, panel_rows=rows_a)
        rb = dict(b, chain="Chain 1", chain_start=a["width"], chain_reversed=False, offset=0, panel_rows=rows_b)
        out = generate_preview({"elevations": [ra, rb], "cladding_type": "panel", "panel_h": 1200,
                                "panel_gap": GAP, "trim": False})
        return {i["elevation"]: i["rows"] for i in out["info"]}
    chain = build([600, 900], [600, 900])                       # set for the whole chain
    assert chain["Elevation A"][:2] == chain["Elevation B"][:2] == [600.0, 900.0]
    one = build([600, 900], None)                                # this elevation only
    assert one["Elevation A"][:2] == [600.0, 900.0]
    assert one["Elevation B"][:2] == [1200.0, 1200.0]


def test_dxf_dimensions_every_row(elevation):
    out, params = _build(elevation, [600, 1200, 300])
    dxf = meshes_to_dxf_string(out["geometry"], params, out["info"])
    for label in ("R1 600", "R2 1200", "R3 300", "Cut 720"):
        assert label in dxf, label
    assert "Rows bottom up: 600, 1200, 300, 720" in dxf


def test_rows_start_from_the_chain_datum():
    """Rows set for a chain line up round its corners: the list starts at the chain's
    datum, and a face that starts higher cuts the row at its base, read-only there."""
    a, b = extract_elevation(payload()), extract_elevation(corner_payload())
    common = dict(chain="Chain 1", chain_reversed=False, offset=0, panel_rows=[600, 900])
    ra = dict(a, chain_start=0, **common)
    rb = dict(b, chain_start=a["width"], clip_v_lo=437.0, **common)       # its base is higher
    out = generate_preview({"elevations": [ra, rb], "cladding_type": "panel", "panel_h": 1200,
                            "panel_gap": GAP, "trim": False})
    rows = {n: _rows_drawn(out, n) for n in ("Elevation A", "Elevation B")}
    assert rows["Elevation A"][:2] == [(150.0, 600.0), (760.0, 900.0)]
    assert rows["Elevation B"][:2] == [(437.0, 313.0), (760.0, 900.0)]      # row 1 cut at B's base
    assert [v for v, _h in rows["Elevation B"][1:]] == [v for v, _h in rows["Elevation A"][1:]]
    dims_b = [d for d in out["dimensions"] if d["elevation"] == "Elevation B" and d["p1"][0] > 3000]
    assert [(d.get("kind"), d.get("row"), d["label"]) for d in dims_b][:2] == [(None, None, "Cut 313"), ("row", 1, "R2 900")]
    info_b = next(i for i in out["info"] if i["elevation"] == "Elevation B")
    assert info_b["rows"][:2] == [600.0, 900.0]          # by list index, what an edit pads with
