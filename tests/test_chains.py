import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from synthetic import payload, corner_payload  # noqa: E402
from fabric_extract import extract_elevation, chain_link  # noqa: E402
from cladding_primitives import splash_rings  # noqa: E402
from cladding_preview import generate_preview  # noqa: E402


def test_pitched_abutment_follows_the_roof():
    e = extract_elevation(payload(pitched=True))
    roofs = [a for a in e["abutments"] if a["source"] == "IfcRoof" and a["pitched"]]
    assert roofs, e["abutments"]
    lines = sorted((a["line"] for a in roofs), key=lambda l: l[0][0])
    # south slope rises from the eaves (5000, 1500) to the ridge (6500, 2400) ...
    south = lines[0]
    assert abs(south[0][0] - 5000) < 5 and abs(south[0][1] - 1700) < 5      # top of 200 slab
    assert abs(south[-1][0] - 6500) < 5 and abs(south[-1][1] - 2600) < 5
    # ... and the north slope falls back to the eaves at the wall's right end
    north = lines[-1]
    assert abs(north[0][0] - 6500) < 5 and abs(north[0][1] - 2600) < 5
    assert abs(north[-1][0] - 8000) < 5 and abs(north[-1][1] - 1850) < 5   # 2600 - 1500 * 900/1800
    assert any("Pitched" in w for w in e["warnings"])
    # the splash band is a sloped polygon 150 tall, and boards are trimmed above it
    rings = splash_rings(e, 150)
    band = [r for r in rings if len(r) >= 4 and abs(r[0][0] - 5000) < 5][0]
    assert abs(band[-1][1] - band[0][1] - 150) < 1e-6
    out = generate_preview({"elevations": [e], "trim": True})
    for m in out["geometry"]:
        if m["ifc_type"] in ("plank", "batten"):
            for u, v in m["profile"]:
                if 5000 < u < 6500:
                    roof_v = 1700 + (u - 5000) * 900 / 1500
                    # either above the splash band or at/below the roof underside (200 slab)
                    assert v >= roof_v + 150 - 1 or v <= roof_v - 200 + 1, (m["name"], u, v)


def test_level_abutment_is_a_two_point_line():
    e = extract_elevation(payload())
    slab = [a for a in e["abutments"] if a["source"] == "IfcSlab"][0]
    assert not slab["pitched"] and len(slab["line"]) == 2
    assert slab["line"][0][1] == slab["line"][1][1] == 2200


def test_chain_link_at_external_corner():
    a = extract_elevation(payload())
    b = extract_elevation(corner_payload())
    link = chain_link(a, b)
    assert link and link["end_a"] == "right" and link["end_b"] == "left", link
    assert abs(link["corner_u_a"] - 8000) < 1 and abs(link["corner_u_b"]) < 1
    assert abs(abs(link["angle"]) - 90) < 0.5
    assert chain_link(a, a) is None   # coplanar faces never chain


def test_chain_coursing_carries_round_the_corner():
    a = extract_elevation(payload())
    b = extract_elevation(corner_payload())
    run = a["width"] + b["width"]
    ra = dict(a, chain="Chain 1", chain_start=0, chain_reversed=False, offset=0)
    rb = dict(b, chain="Chain 1", chain_start=a["width"], chain_reversed=False, offset=0)
    # a plain grid: openings would otherwise pin the joints to the window jambs
    out = generate_preview({"elevations": [ra, rb], "cladding_type": "panel", "trim": False,
                            "corner": "butt", "set_out_from_openings": False})
    joints = {}
    for m in out["geometry"]:
        if m["ifc_type"] == "panel":
            start = 0 if m["elevation"] == "Elevation A" else a["width"]
            joints.setdefault(m["elevation"], set()).add(round(m["profile"][0][0] + start, 1))
    # panel starts on both faces sit on one 1210 mm grid measured along the whole run
    grid = sorted(joints["Elevation A"] | joints["Elevation B"])
    inner = [g for g in grid if 1 < g < run - 1 and abs(g - a["width"]) > 1]
    diffs = {round((g - inner[0]) % 1210, 1) for g in inner}
    assert diffs <= {0.0, 1210.0}, diffs


def _elev_with_mitre(k_hi=1.0):
    a = extract_elevation(payload())
    return dict(a, chain="Chain 1", chain_start=0, chain_reversed=False, offset=0,
                corner_lo=0.0, corner_hi=k_hi)


def test_mitre_marks_the_end_elements():
    out = generate_preview({"elevations": [_elev_with_mitre()], "cladding_type": "panel", "trim": True,
                            "reveals": False})
    right = [m for m in out["geometry"] if m.get("corner", {}).get("k_r") and m["ifc_type"] != "panel"]
    assert right, "no element marked at the mitred end"
    assert all(abs(m["corner"]["u_r"] - 8000) < 1 for m in right)
    assert not any(m.get("corner", {}).get("k_l") for m in out["geometry"] if m["ifc_type"] != "panel")
    # butt corners leave every element square (reveals are their own mitre)
    out = generate_preview({"elevations": [_elev_with_mitre()], "corner": "butt", "trim": False,
                            "reveals": False})
    assert not any(m.get("corner") for m in out["geometry"])


def test_mitre_is_cut_on_the_bisector_plane_in_ifc():
    """Read the written coordinates rather than tessellating them: IfcOpenShell's
    shape builder is not dependable enough here to tell a bad export from a bad run."""
    import ifcopenshell
    from synthetic import N, U, ORIGIN
    from ifc_generator import meshes_to_ifc
    params = {"elevations": [_elev_with_mitre()], "cladding_type": "panel", "trim": True}
    out = generate_preview(params)
    ifc = ifcopenshell.open(meshes_to_ifc(out["geometry"], params, out["info"]))
    breps = ifc.by_type("IfcFacetedBrep")
    assert breps, "mitred elements were not written as solids"
    assert not ifc.by_type("IfcBooleanClippingResult"), "mitres must not depend on booleans"
    worst = 0.0
    for brep in breps:
        for face in brep.Outer.CfsFaces:
            for bound in face.Bounds:
                for point in bound.Bound.Polygon:
                    x, y, z = point.Coordinates
                    p = (x - ORIGIN[0], y - ORIGIN[1], z - ORIGIN[2])
                    u = p[0] * U[0] + p[1] * U[1]
                    s = p[0] * N[0] + p[1] * N[1]
                    # k = 1, so no material may sit beyond u = 8000 + depth
                    assert u <= 8000 + s + 1e-6, (u, s)
                    worst = max(worst, u - 8000)
    assert worst > 40, "nothing wrapped past the corner (worst %.1f)" % worst
    # every brep is a closed shell of planar quads
    for brep in breps:
        assert len(brep.Outer.CfsFaces) >= 6
        for face in brep.Outer.CfsFaces:
            assert len(face.Bounds[0].Bound.Polygon) >= 3


def _lap_pair(master_first=True):
    """Elevation A (right end) meeting B (left end) at an external 90 degree corner."""
    a = extract_elevation(payload())
    b = extract_elevation(corner_payload())
    ra = dict(a, chain="Chain 1", chain_start=0, chain_reversed=False, offset=0,
              corner_hi=1.0, master_hi=master_first)
    rb = dict(b, chain="Chain 1", chain_start=a["width"], chain_reversed=False, offset=0,
              corner_lo=1.0, master_lo=not master_first)
    return {"elevations": [ra, rb], "cladding_type": "panel", "corner": "lap", "trim": False,
            "panel_t": 9, "panel_gap": 10, "reveals": False, "set_out_from_openings": False}


def test_master_lap_runs_one_board_past_the_other():
    from cladding_constants import _parse
    from cladding_primitives import buildup_depth
    params = _lap_pair(master_first=True)
    depth = buildup_depth(_parse(params))
    out = generate_preview(params)
    ends = {}
    for m in out["geometry"]:
        corner = m.get("corner")
        if not corner or m["ifc_type"] == "reveal":
            continue
        assert m["ifc_type"] == "panel", "the lap is a board detail: %s" % m["name"]
        if "ext_r" in corner and abs(corner.get("u_r", 0) - m["_W"]) < 1 if "_W" in m else "ext_r" in corner:
            ends.setdefault("A", corner["ext_r"])
        if "ext_l" in corner:
            ends.setdefault("B", corner["ext_l"])
    # the master runs out to the far face of the other side's cladding ...
    assert abs(ends["A"] - depth) < 1e-6, ends
    # ... and the board behind stops a joint gap short of the master board's back
    assert abs(ends["B"] - (depth - 9 - 10)) < 1e-6, ends
    assert all(abs(m["corner"].get("k_r", 0)) < 1e-9 and abs(m["corner"].get("k_l", 0)) < 1e-9
               for m in out["geometry"] if m.get("corner") and m["ifc_type"] != "reveal"), \
        "a lap at a right angle cuts square"
    # swapping the master swaps the two extensions
    swapped = generate_preview(_lap_pair(master_first=False))
    got = {m["elevation"]: m["corner"].get("ext_r", m["corner"].get("ext_l"))
           for m in swapped["geometry"] if m.get("corner") and m["ifc_type"] == "panel"}
    assert abs(got["Elevation B"] - depth) < 1e-6, got


def test_master_lap_is_panel_only():
    params = dict(_lap_pair(), cladding_type="plank")
    out = generate_preview(params)
    assert out["info"][0]["corner"]["detail"] == "mitre", "planks have no master board to lap"


def test_master_lap_at_a_reentrant_corner():
    """Nothing wraps round a re-entrant corner: the master runs into it and the other
    board stops a joint gap clear of the master's whole buildup."""
    from cladding_constants import _parse
    from cladding_primitives import buildup_depth, corner_ends
    params = _lap_pair(master_first=True)
    depth = buildup_depth(_parse(params))
    inner = dict(params["elevations"][0], corner_hi=-1.0, master_hi=True)
    outer = dict(params["elevations"][0], corner_hi=-1.0, master_hi=False)
    p = _parse(params)
    (_l, master_end, _d) = (corner_ends(inner, p)[0], corner_ends(inner, p)[1], None)
    slave_end = corner_ends(outer, p)[1]
    assert master_end == (0.0, 0.0), master_end          # runs into the corner
    assert abs(slave_end[1] + depth + p["panel_gap"]) < 1e-6, slave_end
    assert abs(slave_end[0]) < 1e-9                       # square at a right angle
    out = generate_preview({"elevations": [inner, dict(outer, name="Elevation B")],
                            "cladding_type": "panel", "corner": "lap", "trim": False,
                            "reveals": False, "set_out_from_openings": False})
    cut = [m for m in out["geometry"] if m.get("corner") and m["elevation"] == "Elevation B"
           and m["ifc_type"] == "panel" and "ext_r" in m["corner"]]
    assert cut and all(m["corner"]["ext_r"] < 0 for m in cut), "the board behind is cut back"
