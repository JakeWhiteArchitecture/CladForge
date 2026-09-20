"""
CladForge — setting-out primitives.

Pure 1D coursing maths shared by the plank and panel layouts. Everything works
along one axis (mm) and knows nothing about which axis it is, so the same
routines set out vertical battens across an elevation and horizontal battens
up it. All functions return sorted lists.
"""

import math


def centred_positions(length, pitch, offset, item):
    """Starts of items of size *item* at *pitch* centres, arrayed both ways from a
    central item whose centre sits at length/2 + offset. Partial items at either
    end are included (their starts may be negative) so callers can trim them."""
    if pitch <= 0 or length <= 0:
        return []
    c = length / 2.0 + offset - item / 2.0
    k_lo = int(math.floor(-(c + item) / pitch)) - 1
    k_hi = int(math.ceil((length - c) / pitch)) + 1
    out = []
    for k in range(k_lo, k_hi + 1):
        s = c + k * pitch
        if s + item > 0.0 and s < length:
            out.append(s)
    return sorted(out)


def stacked_positions(start, end, pitch):
    """Starts from *start* stepping by *pitch* while still inside (start, end)."""
    out = []
    if pitch <= 0:
        return out
    s = start
    while s < end - 1e-6:
        out.append(s)
        s += pitch
    return out


def batten_positions(length, width, centres, offset, edges=True):
    """Batten centrelines across *length*: arrayed at *centres* about the middle
    (shifted by *offset*), plus an edge batten at each end. Battens closer than
    one batten width to another are dropped."""
    pos = [s + width / 2.0 for s in centred_positions(length, centres, offset, width)]
    pos = [x for x in pos if width / 2.0 <= x <= length - width / 2.0]
    if edges and length >= width:
        pos += [width / 2.0, length - width / 2.0]
    return dedupe(pos, width)


def dedupe(values, min_gap):
    """Sorted values with any value closer than *min_gap* to its predecessor removed."""
    out = []
    for x in sorted(values):
        if not out or x - out[-1] >= min_gap - 1e-6:
            out.append(x)
    return out


def dedupe_priority(groups, min_gap):
    """Merge lists of positions in priority order: a position is kept unless one
    already kept (from an earlier group or earlier in its own group) lies within
    *min_gap*. Panel joints therefore always beat edge and intermediate battens."""
    kept = []
    for group in groups:
        for x in sorted(group):
            if all(abs(x - k) >= min_gap - 1e-6 for k in kept):
                kept.append(x)
    return sorted(kept)


def subdivide(a, b, max_span):
    """Interior points splitting [a, b] into equal spans no longer than *max_span*."""
    if max_span <= 0 or b - a <= max_span + 1e-6:
        return []
    n = int(math.ceil((b - a) / max_span))
    return [a + (b - a) * i / n for i in range(1, n)]


def panel_bays(length, panel, gap, offset):
    """Panels across *length* with a panel centred at length/2 + offset.
    Returns (panels, joints): panels as (start, end, full) clipped to [0, length],
    joints as the centrelines of the gaps between neighbouring panels."""
    bay = panel + gap
    starts = centred_positions(length, bay, offset, panel)
    panels, joints = [], []
    for s in starts:
        e = s + panel
        cs, ce = max(0.0, s), min(length, e)
        if ce - cs > 1e-6:
            panels.append((cs, ce, abs(cs - s) < 1e-6 and abs(ce - e) < 1e-6))
        j = e + gap / 2.0
        if 0.0 < j < length:
            joints.append(j)
    return panels, sorted(joints)


def split_run(a, b, max_len, stops, stagger, joint_gap=0.0, min_piece=300.0):
    """Split the run [a, b] into board lengths no longer than *max_len*.

    End joints land on the nearest *stop* (batten centreline) at or before the
    maximum length. Odd courses start with a half-length piece so joints stagger.
    Where no stop lies within reach the board is cut at max_len and the joint is
    reported as unsupported. Returns (segments, unsupported_joints) with segments
    as (start, end) already shortened by half the joint gap at each internal joint.
    """
    stops = sorted(s for s in stops if a < s < b)
    segs, unsupported = [], []
    cur = a
    target = max_len / 2.0 if stagger else max_len
    while b - cur > 1e-6:
        if b - cur <= target + 1e-6:
            segs.append((cur, b))
            break
        limit = cur + target
        candidates = [s for s in stops if cur + min_piece <= s <= limit]
        if candidates:
            cut = candidates[-1]
        else:
            cut = limit
            unsupported.append(cut)
        segs.append((cur, cut))
        cur = cut
        target = max_len
    if joint_gap > 0 and len(segs) > 1:
        g = joint_gap / 2.0
        segs = [(s + (g if i > 0 else 0.0), e - (g if i < len(segs) - 1 else 0.0))
                for i, (s, e) in enumerate(segs)]
    return segs, unsupported


def region_bands(elev, splash):
    """Splash-zone bands (u0, u1, v0, v1) from an elevation's enabled abutments."""
    bands = []
    for ab in elev.get("abutments", []):
        if not ab.get("enabled", True) or splash <= 0:
            continue
        bands.append((float(ab["u0"]), float(ab["u1"]), float(ab["v"]), float(ab["v"]) + splash))
    return bands


def base_level(elev, splash):
    """Vertical setting-out origin: top of the ground splash zone when the
    elevation base is an enabled abutment, otherwise the elevation base."""
    for ab in elev.get("abutments", []):
        if ab.get("source") == "base" and ab.get("enabled", True):
            return float(ab["v"]) + splash
    return 0.0
