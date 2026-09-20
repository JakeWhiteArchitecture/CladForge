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
    ra = dict(a, chain_start=0, chain_length=run, chain_reversed=False, offset=0)
    rb = dict(b, chain_start=a["width"], chain_length=run, chain_reversed=False, offset=0)
    out = generate_preview({"elevations": [ra, rb], "cladding_type": "panel", "trim": False})
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
