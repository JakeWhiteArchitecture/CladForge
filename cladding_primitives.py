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


ROW_MIN, ROW_MAX = 150.0, 3000.0     # mm – a listed panel row; short rows are deliberate tiers


def panel_rows(heights, default_h, gap, start, end, base=None):
    """Panel rows up a face as [(v, h, index, full h)], bottom first, and the indices of
    rows asked for below ROW_MIN.

    *heights* lists the rows in order from *start*; once it runs out the rows carry on
    at *default_h*. Rows stack with *gap* between them, and the top row is the closing
    cut, taking whatever height is left below *end*: a listed row that does not fit is
    cut, and rows listed above the top are dropped.

    *start* is where the list begins — the chain's course datum, so rows line up round
    its corners — and *base* is where this face starts cladding (*start* when omitted).
    Below the datum the rows carry on down at *default_h* with no index; the row that
    crosses the base is cut there, and one wholly below it is dropped. Each row is
    returned as drawn, with its list index (None below the datum) and its full height."""
    base = start if base is None else base
    heights = [float(h) for h in (heights or []) if h]
    seq, short = [], []
    v, i = float(start), 0
    while v < end - 1e-6:
        if i < len(heights):
            if heights[i] < ROW_MIN:
                short.append(i)
            h = min(ROW_MAX, max(ROW_MIN, heights[i]))
        else:
            h = default_h
        seq.append((v, h, i))
        v, i = v + h + gap, i + 1
    v = float(start)
    while v - gap > base + 1e-6:              # under the datum, on a face that starts lower
        v -= default_h + gap
        seq.insert(0, (v, default_h, None))
    out = []
    for v, h, idx in seq:
        lo, hi = max(v, base), min(v + h, end)
        if hi - lo >= 1.0:
            out.append((lo, hi - lo, idx, h))
    return out, short


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


MIN_OPENING = 300.0   # mm – smaller holes are penetrations, not windows


def openings(elev, limit=MIN_OPENING):
    """Structural openings as (u0, u1, v0, v1), from the interior holes big enough to
    be a window or door rather than a pipe penetration, plus the notches the extractor
    found: a door reaching the foot of the wall breaks the outline instead of leaving
    a hole, and it still needs closing and lining."""
    out = []
    for poly in elev.get("polygons", []):
        for hole in poly.get("holes", []):
            us = [q[0] for q in hole]
            vs = [q[1] for q in hole]
            if max(us) - min(us) >= limit and max(vs) - min(vs) >= limit:
                out.append((min(us), max(us), min(vs), max(vs)))
    lo, hi = clip_bounds(elev)
    for u0, u1, v0, v1 in elev.get("notches") or []:
        if min(u1, hi) - max(u0, lo) >= limit and v1 - v0 >= limit:
            out.append((max(u0, lo), min(u1, hi), v0, v1))
    return sorted(out)


def bays_between(stops, max_panel, gap):
    """Panels filling each span between consecutive *stops*, split equally into as few
    bays as stay within *max_panel*. A stop is a fixed edge (an opening jamb or the end
    of the elevation), so no joint gap is taken there; gaps fall between bays."""
    panels, joints = [], []
    for a, b in zip(stops, stops[1:]):
        span = b - a
        if span <= 1.0:
            continue
        n = max(1, int(math.ceil((span + gap) / (max_panel + gap))))
        width = (span - gap * (n - 1)) / n
        for i in range(n):
            start = a + i * (width + gap)
            panels.append((start, start + width, True))
            if i:
                joints.append(start - gap / 2.0)
    return panels, sorted(joints)


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
    if b - a <= max_len + 1e-6:
        return [(a, b)], []          # one whole board reaches: no seam to show
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


MAX_SPLASH_STRETCH = 3.0   # cap the 1/cos blow-up on a near-vertical abutment (~70 deg)


def _splash_offsets(line, splash):
    """Vertical offset at each vertex that keeps the band *splash* clear of the line
    measured perpendicular to it: level runs give *splash*, a pitch opens it up by
    1/cos(pitch), and a vertex between two slopes takes the steeper of the pair."""
    segs = []
    for (u0, v0), (u1, v1) in zip(line, line[1:]):
        du = abs(u1 - u0)
        stretch = math.hypot(du, v1 - v0) / du if du > 1e-9 else MAX_SPLASH_STRETCH
        segs.append(splash * min(MAX_SPLASH_STRETCH, stretch))
    return [max(segs[max(0, i - 1)], segs[min(i, len(segs) - 1)]) for i in range(len(line))]


def splash_rings(elev, splash):
    """Splash-zone polygons (rings of [u, v]) above each enabled abutment line.

    The band clears the abutment by *splash* measured perpendicular to it, which on a
    level slab is simply that height. A pitched roof needs more vertical room to keep
    the same clearance off its surface, so the band opens up by 1/cos(pitch): 150 mm
    off a 30 degree roof is 173 mm of vertical band."""
    rings = []
    for ab in elev.get("abutments", []):
        if not ab.get("enabled", True) or splash <= 0:
            continue
        raw = ab.get("line") or [[float(ab["u0"]), float(ab["v"])], [float(ab["u1"]), float(ab["v"])]]
        line = [[float(u), float(v)] for u, v in raw]
        if len(line) < 2:
            continue
        top = [[u, v + o] for (u, v), o in zip(line, _splash_offsets(line, splash))]
        rings.append(line + top[::-1])
    return rings


def buildup_depth(p):
    """Depth of the cladding outer face from the wall face (mm)."""
    d = (p["sheathing_t"] if p["sheathing"] else 0.0) + (p["insulation_t"] if p["insulation"] else 0.0)
    d += (p["cb_d"] if p["has_cb"] else 0.0) + p["batten_d"]
    return d + (p["panel_t"] if p["cladding_type"] == "panel" else p["plank_t"])


CORNER_DETAILS = ("mitre", "lap", "butt")


def corner_detail(p, override=None):
    """The corner detail in force: *override* when a corner sets its own, else the job
    setting. The master-lap is a panel detail: one board runs past the corner and the
    other butts behind it, leaving the joint gap exposed. Plank cladding has no master
    board to lap, so it falls back to a mitre."""
    detail = override if override in CORNER_DETAILS else p.get("corner", "mitre")
    if detail == "lap" and p["cladding_type"] != "panel":
        return "mitre"
    return detail if detail in CORNER_DETAILS else "mitre"


def corner_ends(elev, p):
    """((k, ext) left, (k, ext) right, (detail left, detail right)). The end face of an
    element sits at u_end -/+ (ext + k x depth): k shears it onto the corner's bisector
    (a mitre), ext moves it square past the corner (a lap). Ends with no corner get
    (0, 0). Each end takes its own corner's detail (detail_lo / detail_hi, None for the
    job setting); like the master flags they are held in run order, lo first."""
    lo, hi = float(elev.get("corner_lo") or 0.0), float(elev.get("corner_hi") or 0.0)
    master_lo, master_hi = bool(elev.get("master_lo")), bool(elev.get("master_hi"))
    over_lo, over_hi = elev.get("detail_lo"), elev.get("detail_hi")
    if elev.get("chain_reversed"):
        lo, hi, master_lo, master_hi, over_lo, over_hi = hi, lo, master_hi, master_lo, over_hi, over_lo
    depth, board, gap = buildup_depth(p), p["panel_t"], p["panel_gap"]

    def lap(k, master):
        """Master-lap ends. k = tan(beta/2) for a corner turning through beta, signed
        positive outward. At an external corner the master board wraps past and out to
        the far face of the other side's cladding, and the one behind stops a joint gap
        short of the master's back. At a re-entrant corner there is nothing to wrap
        round: the master runs into the corner and the other stops a gap clear of the
        master's whole buildup. Away from a right angle both ends slope with depth."""
        if not k:
            return (0.0, 0.0)
        beta = 2.0 * math.atan(abs(k))
        sin_b = max(1e-6, math.sin(beta))
        cot = math.cos(beta) / sin_b
        cot = 0.0 if abs(cot) < 1e-9 else cot   # a right angle cuts square
        if k > 0:
            return (-cot, depth / sin_b) if master else (-cot, (depth - board) / sin_b - gap)
        return (0.0, 0.0) if master else (cot, -(depth / sin_b + gap))

    def end(k, master, override):
        detail = corner_detail(p, override)
        if detail == "butt":
            return (0.0, 0.0), detail
        return ((k, 0.0) if detail == "mitre" else lap(k, master)), detail

    (left, d_left), (right, d_right) = end(lo, master_lo, over_lo), end(hi, master_hi, over_hi)
    return left, right, (d_left, d_right)


def corner_shift(u, corner, s, tol=0.6):
    """Where a vertex at *u* sits once the corner detail is applied at depth *s*."""
    if not corner:
        return u
    if (corner.get("k_l") or corner.get("ext_l")) and abs(u - corner["u_l"]) < tol:
        return u - (corner.get("ext_l", 0.0) + corner.get("k_l", 0.0) * s)
    if (corner.get("k_r") or corner.get("ext_r")) and abs(u - corner["u_r"]) < tol:
        return u + (corner.get("ext_r", 0.0) + corner.get("k_r", 0.0) * s)
    return u


def corner_ring(ring, corner, s):
    return [(corner_shift(float(u), corner, s), float(v)) for u, v in ring]


def clip_bounds(elev):
    """(lo, hi) in this elevation's u: the part of the face that is actually clad.
    A wall that runs past a corner is cut back to it, so nothing projects through."""
    width = float(elev["width"])
    lo = max(0.0, float(elev.get("clip_lo") or 0.0))
    hi = elev.get("clip_hi")
    hi = min(width, float(hi)) if hi is not None else width
    return (lo, hi) if hi - lo > 1.0 else (0.0, width)


def clip_bounds_v(elev):
    """(v_lo, v_hi) in this elevation's v: where the cladding starts and stops up the
    face. Set by picking a point for the top and one for the bottom; unset means the
    whole face."""
    height = float(elev["height"])
    lo = max(0.0, float(elev.get("clip_v_lo") or 0.0))
    hi = elev.get("clip_v_hi")
    hi = min(height, float(hi)) if hi is not None else height
    return (lo, hi) if hi - lo > 1.0 else (0.0, height)


def chain_layout(elevations, face_depth, detail):
    """Run coordinates measured along the cladding face, so a corner adds the wrap on
    both of its sides. Returns {elevation name: (start, run length)}."""
    groups = {}
    for e in elevations:
        groups.setdefault(e.get("chain") or ("\x00" + str(e.get("name"))), []).append(e)
    out = {}
    for members in groups.values():
        members.sort(key=lambda e: float(e.get("chain_start") or 0.0))
        run, spans = 0.0, []
        for e in members:
            lo, hi = clip_bounds(e)
            width = hi - lo
            spans.append((e.get("name"), run))
            here = e.get("detail_hi") if e.get("detail_hi") in CORNER_DETAILS else detail
            k = float(e.get("corner_hi") or 0.0) if here != "butt" else 0.0
            run += width + 2.0 * k * face_depth
        for name, start in spans:
            out[name] = (start, run)
    return out


def base_level(elev, splash):
    """Vertical setting-out origin: top of the ground splash zone when the
    elevation base is an enabled abutment, otherwise the elevation base."""
    for ab in elev.get("abutments", []):
        if ab.get("source") == "base" and ab.get("enabled", True):
            return float(ab["v"]) + splash
    return 0.0
