import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from synthetic import payload, corner_payload, box_tris  # noqa: E402
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
    # the band follows the slope and clears it by 150 measured perpendicular to the
    # roof, so it stands taller than 150 vertically: 150 / cos(31 deg) = 175
    rings = splash_rings(e, 150)
    band = [r for r in rings if len(r) >= 4 and abs(r[0][0] - 5000) < 5][0]
    import math as _math
    pitch = _math.atan2(south[-1][1] - south[0][1], south[-1][0] - south[0][0])
    assert abs(band[-1][1] - band[0][1] - 150 / _math.cos(pitch)) < 1e-6
    assert abs((band[-1][1] - band[0][1]) * _math.cos(pitch) - 150) < 1e-6
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


def test_splash_clearance_is_measured_off_the_roof_surface():
    """150 mm of splash zone means 150 mm from the roof, not 150 mm of vertical band.
    On a pitch the two differ by cos(pitch), which is what leaves boards short."""
    import math
    e = extract_elevation(payload(pitched=True))
    ab = next(a for a in e["abutments"] if a.get("pitched"))
    (u0, v0), (u1, v1) = ab["line"][0], ab["line"][-1]
    nx, ny = -(v1 - v0), (u1 - u0)
    L = math.hypot(nx, ny)
    nx, ny = nx / L, ny / L

    out = generate_preview({"elevations": [dict(e, offset=0)], "cladding_type": "plank",
                            "splash": 150.0, "trim": True})
    gaps = [(u - u0) * nx + (v - v0) * ny
            for m in out["geometry"] if m["ifc_type"] == "plank"
            for u, v in m["profile"] if u0 + 1 < u < u1 - 1]
    above = [g for g in gaps if g > 0]
    assert above, "no boards above the pitched abutment"
    assert min(above) > 149.0, "boards sit %.1fmm off a %.0f degree roof, not 150" % (
        min(above), math.degrees(math.atan2(v1 - v0, u1 - u0)))


def test_a_roof_layer_that_stops_short_of_the_wall_still_counts():
    """The cladding stands off the face by its whole buildup, so a roof finish that
    never touches the wall can still sit where the boards go. A plane section cannot
    see it; the cladding zone can."""
    deck = box_tris(-500.0, 8500.0, 1400.0, 1600.0, -100.0, 2500.0)
    cover = box_tris(-500.0, 8500.0, 1600.0, 1660.0, 60.0, 2500.0)   # 60mm clear of the face
    pay = payload()
    pay["context"] = [{"type": "IfcRoof", "name": "Deck", "tris": [[list(q) for q in t] for t in deck]},
                      {"type": "IfcRoof", "name": "Covering", "tris": [[list(q) for q in t] for t in cover]}]

    def tops(depth):
        e = extract_elevation(dict(pay, options={"penetrations": True, "clad_depth": depth}))
        return {a.get("name"): a["v"] for a in e["abutments"] if a["source"] != "base"}

    assert tops(0.0) == {"Deck": 1600.0}                        # the plane section misses it
    assert tops(150.0) == {"Deck": 1600.0, "Covering": 1660.0}  # the zone does not

    e = extract_elevation(dict(pay, options={"penetrations": True, "clad_depth": 150.0}))
    out = generate_preview({"elevations": [dict(e, offset=0)], "cladding_type": "plank",
                            "splash": 150.0, "trim": True})
    over = [min(q[1] for q in m["profile"]) for m in out["geometry"]
            if m["ifc_type"] == "plank" and min(q[1] for q in m["profile"]) > 1660.0]
    assert over and min(over) >= 1810.0 - 1.0, "boards clear the covering, not the deck"


def test_extraction_gives_up_rather_than_grinding():
    """The engine runs on the page's main thread, so a slow extraction freezes the
    browser with no way out. Past its budget it stops reading context and returns what
    it has, saying so, instead of running on."""
    pay = payload(pitched=True)
    out_of_time = extract_elevation(dict(pay, options={"penetrations": True, "budget_s": 0.0}))
    assert out_of_time["ok"], "a timed-out extraction is still a usable elevation"
    assert out_of_time["width"] > 0 and out_of_time["polygons"]
    assert any("Ran out of time" in w for w in out_of_time["warnings"])
    assert len(out_of_time["abutments"]) == 1          # just the base line, no context read

    full = extract_elevation(dict(pay, options={"penetrations": True}))
    assert not any("Ran out of time" in w for w in full["warnings"])
    assert len(full["abutments"]) > 1
    assert full["width"] == out_of_time["width"]       # the face itself is read either way


def _three_faces(detail_ab=None, detail_bc=None, job="mitre"):
    """A → B → C, external right angles at both of B's ends. The detail at each corner
    is an override held by both faces that meet there; None means the job setting."""
    a, b = extract_elevation(payload()), extract_elevation(corner_payload())
    c = dict(extract_elevation(payload()), name="Elevation C")
    common = dict(chain="Chain 1", chain_reversed=False, offset=0)
    ra = dict(a, chain_start=0, corner_hi=1.0, master_hi=True, detail_hi=detail_ab, **common)
    rb = dict(b, chain_start=a["width"], corner_lo=1.0, master_lo=False, detail_lo=detail_ab,
              corner_hi=1.0, master_hi=True, detail_hi=detail_bc, **common)
    rc = dict(c, chain_start=a["width"] + b["width"], corner_lo=1.0, master_lo=False,
              detail_lo=detail_bc, **common)
    return {"elevations": [ra, rb, rc], "cladding_type": "panel", "corner": job, "trim": False,
            "reveals": False, "set_out_from_openings": False}


def test_each_corner_takes_its_own_detail():
    """One mitred corner and one lapped corner in the same chain: each end of each face
    is built exactly as it would be if the whole job used that end's detail."""
    mixed = generate_preview(_three_faces(detail_bc="lap"))          # A–B from the job: mitre
    mitre = generate_preview(_three_faces(job="mitre"))
    lap = generate_preview(_three_faces(job="lap"))
    by_name = lambda out: {m["name"]: m.get("corner") or {} for m in out["geometry"]}  # noqa: E731
    got, want_mitre, want_lap = by_name(mixed), by_name(mitre), by_name(lap)
    assert set(got) == set(want_mitre) == set(want_lap)
    left, right = ("k_l", "ext_l", "u_l"), ("k_r", "ext_r", "u_r")
    side = lambda c, keys: {k: c[k] for k in keys if k in c}  # noqa: E731
    for name, corner in got.items():
        elev = name.split(" ")[1]
        if elev == "A":
            assert side(corner, right) == side(want_mitre[name], right), name
        elif elev == "B":
            assert side(corner, left) == side(want_mitre[name], left), name
            assert side(corner, right) == side(want_lap[name], right), name
        else:
            assert side(corner, left) == side(want_lap[name], left), name
    # the lap is a board detail: behind it the layers stay square, while the mitre
    # at B's other end cuts every layer
    battens_b = [c for n, c in got.items() if " B Batten" in n]
    assert any("k_l" in c for c in battens_b) and not any("k_r" in c for c in battens_b)
    info = {i["elevation"]: i["corner"]["details"] for i in mixed["info"]}
    assert info["Elevation B"] == ["mitre", "lap"], info


def test_a_corner_override_falls_back_like_the_job_setting():
    """Master-lap is a panel detail: a lap set on one corner falls back to a mitre in
    plank mode, and a square corner adds nothing to the chain run."""
    params = dict(_three_faces(detail_bc="lap"), cladding_type="plank")
    info = {i["elevation"]: i["corner"]["details"] for i in generate_preview(params)["info"]}
    assert info["Elevation B"] == ["mitre", "mitre"], info
    from cladding_constants import _parse
    from cladding_primitives import buildup_depth, chain_layout
    for override in ("butt", None):
        p = _parse(_three_faces(detail_ab=override))
        run = chain_layout(p["elevations"], buildup_depth(p), p["corner"])["Elevation A"][1]
        p_job = _parse(_three_faces(job=override or "mitre", detail_bc="mitre"))
        assert run == chain_layout(p_job["elevations"], buildup_depth(p_job), p_job["corner"])["Elevation A"][1]
