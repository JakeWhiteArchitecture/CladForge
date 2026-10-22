"""Nothing is clad in a sliver under a splash zone: where the roof or slab that sets the
zone leaves a strip of face too thin to clad between itself and the zone."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shapely.geometry import box  # noqa: E402

from cladding_booleans import clip_region  # noqa: E402
from cladding_primitives import ROW_MIN  # noqa: E402


def _face(roof_hole, line, extra_holes=()):
    """8000 x 3000, the roof's section through the face removed as a hole, and its
    abutment line (the splash zone sits on it)."""
    holes = [[[u0, v0], [u1, v0], [u1, v1], [u0, v1]] for u0, v0, u1, v1 in (roof_hole,) + tuple(extra_holes)]
    return {"polygons": [{"exterior": [[0, 0], [8000, 0], [8000, 3000], [0, 3000]], "holes": holes}],
            "abutments": [{"enabled": True, "line": line, "source": "roof"}]}


def test_the_strip_between_a_roof_and_its_splash_zone_is_not_clad():
    """The roof's section stops 80 mm under its abutment line: that 80 mm strip, under
    the 150 mm splash zone, is not clad. The face above the zone and the wall below the
    roof are."""
    elev = _face((4000, 1300, 7000, 1420), [[4000, 1500], [7000, 1500]])
    region = clip_region(elev, 150)
    assert region.intersection(box(4000, 1420, 7000, 1500)).area < 1.0           # the sliver is gone
    assert abs(region.intersection(box(4000, 1650, 7000, 3000)).area - 3000 * 1350) < 1.0   # above the zone
    assert abs(region.intersection(box(4000, 0, 7000, 1300)).area - 3000 * 1300) < 1.0      # below the roof
    assert abs(region.intersection(box(0, 0, 4000, 3000)).area - 4000 * 3000) < 1.0         # beside it, untouched


def test_a_strip_at_least_a_row_high_stays():
    """A deliberate band of cladding under the zone, at least the shortest row high, stays."""
    elev = _face((4000, 1200, 7000, 1300), [[4000, 1500], [7000, 1500]])
    region = clip_region(elev, 150)
    assert abs(region.intersection(box(4000, 1300, 7000, 1500)).area - 3000 * 200) < 1.0
    assert ROW_MIN <= 200


def test_a_wedge_under_a_pitched_zone_loses_its_thin_end():
    """Under a pitched line over a level roof section the strip tapers: the part thinner
    than a row goes, the rest stays."""
    elev = _face((4000, 1300, 7000, 1400), [[4000, 1400], [7000, 1700]])     # 100 rising to 400 over the hole
    region = clip_region(elev, 150)
    thin = region.intersection(box(4000, 1400, 4500, 1500))
    assert thin.area < 1.0                                                     # the first 500: under 150 thick
    assert region.intersection(box(6500, 1400, 7000, 1650)).area > 0.9 * 500 * 250   # the thick end is clad


def test_openings_under_a_zone_keep_their_square_corners():
    """A window under a balcony zone: the opening and the cladding round it are untouched."""
    elev = _face((1000, 2300, 7000, 2400), [[1000, 2400], [7000, 2400]], [(2000, 900, 3200, 2100)])
    region = clip_region(elev, 150)
    assert abs(region.intersection(box(1000, 0, 7000, 2300)).area - (6000 * 2300 - 1200 * 1200)) < 1.0
