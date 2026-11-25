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


def row_height(heights, i, default_h):
    """Height of list row *i*: as listed, held to ROW_MIN–ROW_MAX, or *default_h* past the list."""
    heights = [float(h) for h in (heights or []) if h]
    return min(ROW_MAX, max(ROW_MIN, heights[i])) if i < len(heights) else float(default_h)


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
        if i < len(heights) and heights[i] < ROW_MIN:
            short.append(i)
        h = row_height(heights, i, default_h)
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
    """Structural openings as (u0, u1, v0, v1), at their true height: the interior holes
    big enough to be a window or door rather than a pipe penetration, the notches the
    extractor found (a door reaching the foot of the wall breaks the outline instead of
    leaving a hole), and full-height openings, which split the face in two.

    The chain's top and bottom levels clip the outline before anything is built, and a
    window crossing either would then be a bite rather than a hole: so the openings are
    found on the face as picked and carried through the clip ("openings", set by
    cladding_booleans.clip_elevation). Only those reaching the clad band are returned,
    cut sideways to the clad part of the face, but never in height."""
    lo, hi = clip_bounds(elev)
    if elev.get("openings") is not None:
        v_lo, v_hi = clip_bounds_v(elev)
        return sorted((max(u0, lo), min(u1, hi), v0, v1) for u0, u1, v0, v1 in elev["openings"]
                      if min(u1, hi) - max(u0, lo) >= limit and min(v1, v_hi) - max(v0, v_lo) > 1.0)
    out = []
    for poly in elev.get("polygons", []):
        for hole in poly.get("holes", []):
            us = [q[0] for q in hole]
            vs = [q[1] for q in hole]
            if max(us) - min(us) >= limit and max(vs) - min(vs) >= limit:
                out.append((min(us), max(us), min(vs), max(vs)))
    for u0, u1, v0, v1 in list(elev.get("notches") or []) + _splits(elev.get("polygons", []), limit):
        if min(u1, hi) - max(u0, lo) >= limit and v1 - v0 >= limit:
            out.append((max(u0, lo), min(u1, hi), v0, v1))
    return sorted(set(out))


def _splits(polygons, limit):
    """Openings that run the full height of the face and so split it into pieces: a
    rectangular gap between the pieces, open at the top and the bottom."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    shells = []
    for poly in polygons:
        try:
            pg = Polygon(poly["exterior"])
            shells.append(pg if pg.is_valid else pg.buffer(0))
        except Exception:   # noqa: BLE001
            continue
    if len(shells) < 2:
        return []
    try:
        whole = unary_union(shells)
        umin, vmin, umax, vmax = whole.bounds
        rest = whole.envelope.difference(whole)
    except Exception:   # noqa: BLE001 — GEOS trouble: no split openings
        return []
    out = []
    for piece in getattr(rest, "geoms", [rest]):
        if piece.is_empty or piece.geom_type != "Polygon":
            continue
        u0, v0, u1, v1 = piece.bounds
        if u1 - u0 < limit or not (abs(v0 - vmin) < 1.0 and abs(v1 - vmax) < 1.0 and u0 > umin + 1.0 and u1 < umax - 1.0):
            continue
        if piece.area >= 0.9 * (u1 - u0) * (v1 - v0):
            out.append((round(u0, 2), round(u1, 2), round(v0, 2), round(v1, 2)))
            continue
        # not a rectangle: a door with a window beside it, the gap L-shaped. Its core, what is
        # open the full height, is the door; what is left over, where it is a rectangle, the window.
        core = _full_height_core(piece, v0, v1)
        if core is None or core[1] - core[0] < limit:
            continue
        out.append((round(core[0], 2), round(core[1], 2), round(v0, 2), round(v1, 2)))
        try:
            from shapely.geometry import box
            left = piece.difference(box(core[0], v0 - 1.0, core[1], v1 + 1.0))
        except Exception:   # noqa: BLE001
            continue
        for bit in getattr(left, "geoms", [left]):
            if bit.is_empty or bit.geom_type != "Polygon":
                continue
            a0, b0, a1, b1 = bit.bounds
            if a1 - a0 >= limit and b1 - b0 >= limit and bit.area >= 0.9 * (a1 - a0) * (b1 - b0):
                out.append((round(a0, 2), round(a1, 2), round(b0, 2), round(b1, 2)))
    return out


def _full_height_core(piece, v0, v1):
    """(u0, u1) of what of a gap is open from its bottom to its top: the span every level of
    it shares, or None."""
    from shapely.geometry import LineString
    levels = sorted({round(y, 3) for x, y in piece.exterior.coords if v0 < y < v1} | {v0, v1})
    lo, hi = -float("inf"), float("inf")
    for a, b in zip(levels, levels[1:]):
        y = (a + b) / 2.0
        cut = piece.intersection(LineString([(piece.bounds[0] - 1.0, y), (piece.bounds[2] + 1.0, y)]))
        spans = [g.bounds for g in getattr(cut, "geoms", [cut]) if not g.is_empty]
        if not spans:
            return None
        widest = max(spans, key=lambda s: s[2] - s[0])
        lo, hi = max(lo, widest[0]), min(hi, widest[2])
    return (lo, hi) if hi > lo else None


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


MIN_BAY = 300.0   # mm – a bay set out on posts is never narrower than this where a post allows


FULL_PANEL = 15.0   # mm – a panel this near the most a panel may be is a full one (the joint gaps come off it)
JOINT_SLIP = 10.0   # mm – a joint may stand this far off its batten's centreline
SLIPS = (0.0, -2.5, 2.5, -5.0, 5.0, -7.5, 7.5, -10.0, 10.0)


def _best_bays(a, b, posts, max_panel, gap, min_bay):
    """The panels across one span [a, b] with every joint on a post, chosen among all the
    ways the posts allow: the fewest panels (the fewest vertical joints), then the most
    full ones (a 1200 sheet uncut), then the joints nearest their battens' centres (one moved
    up to JOINT_SLIP off only for a joint less or a full sheet more), then the widest
    narrowest panel (no sliver at a corner or beside an opening), then the longer panels
    first, as before. Posts at 600 with now and
    then two 100 apart: a 700 panel over the pair (an extra fixing) rather than a joint
    more. None where some stretch has no post in reach (bays_on_posts then adds battens)."""
    if b - a <= max_panel + 1e-6:
        return [(a, b)]
    # each joint on a batten's centre, or up to JOINT_SLIP off it (a 1200 sheet over two bays
    # set out at 605 and more, the fixings still well within the batten)
    cand = sorted({(q + d, abs(d)) for q in posts for d in SLIPS if a + gap / 2.0 + 1.0 < q + d < b - gap / 2.0 - 1.0})
    nodes = [(a, 0.0)] + cand + [(b, 0.0)]
    start = lambda i: nodes[i][0] if i == 0 else nodes[i][0] + gap / 2.0           # noqa: E731
    end = lambda j: nodes[j][0] if j == len(nodes) - 1 else nodes[j][0] - gap / 2.0   # noqa: E731
    for floor in (min_bay, 1.0):          # no way within min_bay: any way at all
        best = {0: ((0, 0, 0.0, -float("inf"), ()), None)}
        for j in range(1, len(nodes)):
            for i in range(j):
                if i not in best or nodes[j][0] <= nodes[i][0]:
                    continue
                w = end(j) - start(i)
                if w > max_panel + 1e-6 or w < floor - 1e-6:
                    continue
                (n, full, slip, narrow, order), _p = best[i]
                # a joint off its batten's centre only for a joint less or a full sheet more; then
                # the widest narrowest panel; last, as the old rule did where nothing else
                # chooses: the longer panels first
                score = (n + 1, full - (1 if w >= max_panel - FULL_PANEL else 0), round(slip + nodes[j][1], 3),
                         max(narrow, -w), order + (-round(w, 3),))
                if j not in best or score < best[j][0]:
                    best[j] = (score, i)
        last = len(nodes) - 1
        if last in best:
            out, j = [], last
            while j:
                i = best[j][1]
                out.append((start(i), end(j)))
                j = i
            return out[::-1]
    return None


MIRROR_GAIN = 50.0   # mm – a mirrored set-out is taken where it is this much more symmetrical, in all
MIRROR_SPANS = 2000  # the most set-outs of one span compared for a mirrored pair
MIRROR_TOL = 150.0   # mm – spans this near mirror images of each other about the axis are a pair


def _all_bays(a, b, posts, max_panel, gap, n, floor):
    """Every way of filling [a, b] with *n* panels, each joint on a post (or JOINT_SLIP off
    it), none wider than *max_panel* or narrower than *floor*: lists of (start, end)."""
    cand = sorted({q + d for q in posts for d in SLIPS if a + gap / 2.0 + 1.0 < q + d < b - gap / 2.0 - 1.0})
    out = []

    def walk(start, left, acc):
        if len(out) >= MIRROR_SPANS:
            return
        if left == 1:
            if floor - 1e-6 <= b - start <= max_panel + 1e-6:
                out.append(acc + [(start, b)])
            return
        for j in cand:
            end = j - gap / 2.0
            if end - start > max_panel + 1e-6:
                break
            if end - start >= floor - 1e-6 and b - (j + gap / 2.0) >= floor * (left - 1) - 1e-6:
                walk(j + gap / 2.0, left - 1, acc + [(start, end)])
    walk(a, n, [])
    return out


def _asymmetry(left, right):
    """How far two spans' panels are from mirror images: the left's widths read from the
    axis outward against the right's, and any panel without a partner counts whole."""
    wl = [e - s for s, e in left][::-1]
    wr = [e - s for s, e in right]
    return sum(abs(x - y) for x, y in zip(wl, wr)) + sum(wl[len(wr):]) + sum(wr[len(wl):])


def _mirrored(spans, posts, max_panel, gap, min_bay, axis):
    """The set-outs of span pairs mirrored about *axis* (each the other's image, within
    MIRROR_TOL) chosen together, as near mirror images as the posts allow with no more
    panels than each had: where that is MIRROR_GAIN more symmetrical than each chosen alone."""
    for i, (a, b, best) in enumerate(spans):
        if best is None:
            continue
        for j in range(i + 1, len(spans)):
            c, d, other = spans[j]
            if other is None or abs((a + d) - 2.0 * axis) > MIRROR_TOL or abs((b + c) - 2.0 * axis) > MIRROR_TOL:
                continue
            now = _asymmetry(best, other)
            if now < MIRROR_GAIN:
                break
            ls = _all_bays(a, b, posts, max_panel, gap, len(best), min_bay)
            rs = _all_bays(c, d, posts, max_panel, gap, len(other), min_bay)
            pick = min(((_asymmetry(x, y), x, y) for x in ls for y in rs), key=lambda t: t[0], default=None)
            if pick is not None and pick[0] <= now - MIRROR_GAIN:
                spans[i] = (a, b, pick[1])
                spans[j] = (c, d, pick[2])
            break
    return spans


def bays_on_posts(stops, posts, max_panel, gap, min_bay=MIN_BAY, axis=None):
    """Panels filling each span between consecutive *stops* (the ends, opening jambs: fixed
    edges, no joint gap taken there) with every joint on a post, a batten the client's
    model fixes, chosen among all the ways the posts allow (_best_bays: the fewest joints,
    then the most full panels, then no slivers). Where some stretch has no post in reach,
    the furthest post that keeps the panel within *max_panel*, avoiding one that leaves a
    last bay under *min_bay*, and where none is in reach the joint goes at the full panel
    width and is listed in *extra*, needing a batten of its own; where that would leave less
    than *min_bay* to the end, it goes half way, the two panels sharing the stretch rather than
    a sliver at the corner. With an *axis* (a face whose openings stand symmetrically, or one
    opening near its middle), spans that mirror each other about it are set out as mirror
    images where the posts allow (_mirrored). Returns (panels, joints, extra)."""
    panels, joints, extra = [], [], []
    spans = [(a, b, _best_bays(a, b, posts, max_panel, gap, min_bay)) for a, b in zip(stops, stops[1:]) if b - a > 1.0]
    if axis is not None:
        spans = _mirrored(spans, posts, max_panel, gap, min_bay, axis)
    for a, b, best in spans:
        if best is not None:
            for s0, e0 in best:
                panels.append((s0, e0, True))
            joints += [e0 + gap / 2.0 for _s0, e0 in best[:-1]]
            continue
        cur = a
        while b - cur > max_panel + 1e-6:
            reach = cur + max_panel + gap / 2.0
            inside = [q for q in posts if cur + gap / 2.0 + min_bay <= q <= reach + 1e-6 and q <= b - gap / 2.0 - 1.0]
            fair = [q for q in inside if b - (q + gap / 2.0) >= min_bay]
            if fair or inside:
                j = (fair or inside)[-1]
            else:
                j = reach
                if b - (j + gap / 2.0) < min_bay and b - cur <= 2.0 * max_panel + gap:
                    j = (cur + b) / 2.0        # a full sheet would leave a sliver at the end: share the stretch
                extra.append(j)
            panels.append((cur, j - gap / 2.0, True))
            joints.append(j)
            cur = j + gap / 2.0
        panels.append((cur, b, True))
    return panels, sorted(joints), extra


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


def _clear_cut(cut, cur, want, feasible, avoid, pitch, whole, end=None, max_len=None):
    """The joint to cut at, kept clear of *avoid* (the joints in the board alongside):
    two battens clear if any feasible batten is, else one, choosing as split_run does
    (the furthest within reach, else the nearest beyond), but never one that leaves the
    rest of the run (to *end*) needing more lengths than *cut* would: clearance is not
    worth an extra joint. If none is, None when the rest of the run fits one stock length
    (*whole*): better no joint than one in line with the neighbour's. Else *cut*."""
    pieces = lambda s: math.ceil((end - s - 1e-6) / max_len) if end is not None and max_len else 0  # noqa: E731
    for clear in (2 * pitch - 1.0, pitch - 1.0):
        pool = [s for s in feasible if all(abs(s - x) >= clear for x in avoid) and pieces(s) <= pieces(cut)]
        if pool:
            within = [s for s in pool if s <= cur + want + 1e-6]
            return within[-1] if within else pool[0]
    return None if whole else cut


def split_run(a, b, max_len, stops, stagger, joint_gap=0.0, min_piece=300.0, target=None, avoid=(), pitch=0.0):
    """Split the run [a, b] into board lengths no longer than *max_len* (the stock).

    *target* is the length a piece is set out to (*max_len* when not given): shorter
    means more splices. End joints land on the nearest *stop* (batten centreline) at or
    before it, and no splice leaves a piece shorter than *min_piece*, either side of it:
    when no batten within the target does, the piece may run on towards the stock
    length; a run that fits the stock whole is then left whole. Only a run shorter than
    *min_piece* itself gives a shorter piece. Odd courses start with a half-length
    piece so joints stagger. Where no stop lies within reach the board is cut at the
    target and the joint is reported as unsupported. Returns (segments,
    unsupported_joints) with segments as (start, end) already shortened by half the
    joint gap at each internal joint.

    *avoid* are the joints in the board alongside: every joint keeps two battens (*pitch*
    apart) clear of them where a batten allows, else one, else is left out where the
    rest of the run fits one stock length, so neighbouring boards alternate rather than
    splicing in line. The half-length start alone cannot promise
    that: when the minimum piece is more than half the target it is pushed onto the
    same batten as the neighbour's.
    """
    target = max_len if target is None else max(min(target, max_len), min(min_piece, max_len))
    if b - a <= target + 1e-6:
        return [(a, b)], []          # one whole board reaches: no seam to show
    stops = sorted(s for s in stops if a < s < b)
    segs, unsupported = [], []
    cur = a
    want = max(target / 2.0, min(min_piece, target)) if stagger else target
    while b - cur > 1e-6:
        if b - cur <= want + 1e-6:
            segs.append((cur, b))
            break
        ok = lambda s, reach: cur + min_piece <= s <= cur + reach and b - s >= min_piece  # noqa: E731
        candidates = [s for s in stops if ok(s, want)] or [s for s in stops if ok(s, max_len)]
        if candidates:
            # within the target, the furthest batten; past it, the nearest one beyond
            cut = candidates[-1] if candidates[-1] <= cur + want else candidates[0]
            if avoid and pitch > 0:
                cut = _clear_cut(cut, cur, want, [s for s in stops if ok(s, max_len)], avoid, pitch,
                                 b - cur <= max_len + 1e-6, b, max_len)
                if cut is None:      # no joint clear of the neighbour's: the rest in one length
                    segs.append((cur, b))
                    break
        elif b - cur <= max_len + 1e-6:
            segs.append((cur, b))    # no batten leaves both pieces long enough: one length
            break
        else:
            cut = max(cur + min(min_piece, want), min(cur + want, b - min_piece))
            unsupported.append(cut)
        segs.append((cur, cut))
        cur = cut
        want = target
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


JUNCTION_ALONG = 0.8    # share of an abutment line lying on a valley or hip before it is that junction, not an abutment
JUNCTION_TOL = 60.0     # mm – how near the valley or hip line it must lie


def _along_junction(line, elev):
    """Does the abutment *line* run along one of the face's valleys or hips? The roof
    slab across a valley is found as an abutment too, but the valley's own detail
    stands the cladding back there: a splash zone on top, opened up for the slope and
    widest where the line turns at its foot, would cut the trays back again, and not
    parallel to the valley."""
    total = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(line, line[1:]))
    if total < 1.0:
        return False
    for r in elev.get("valleys") or []:
        if (r.get("kind") or "valley") not in ("valley", "hip"):
            continue
        try:
            (x1, y1), (x2, y2) = [float(c) for c in r["p1"]], [float(c) for c in r["p2"]]
        except (KeyError, TypeError, ValueError):
            continue
        length = math.hypot(x2 - x1, y2 - y1)
        if length < 1.0:
            continue
        dx, dy = (x2 - x1) / length, (y2 - y1) / length

        def near(q):
            along = (q[0] - x1) * dx + (q[1] - y1) * dy
            return abs((q[0] - x1) * dy - (q[1] - y1) * dx) <= JUNCTION_TOL and \
                -JUNCTION_TOL <= along <= length + JUNCTION_TOL
        on = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(line, line[1:]) if near(a) and near(b))
        if on >= JUNCTION_ALONG * total:
            return True
    return False


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
        if len(line) < 2 or _along_junction(line, elev):
            continue
        top = [[u, v + o] for (u, v), o in zip(line, _splash_offsets(line, splash))]
        rings.append(line + top[::-1])
    return rings


def buildup_depth(p):
    """Depth of the cladding outer face from the wall face (mm)."""
    d = (p["sheathing_t"] if p["sheathing"] else 0.0) + (p["insulation_t"] if p["insulation"] else 0.0)
    d += (p["cb_d"] if p["has_cb"] else 0.0) + p["batten_d"]
    if p.get("seam"):                      # a standing seam's deck, and the metal 2 off it
        d += p["deck_t"] + 2.0
    return d + (p["panel_t"] if p["cladding_type"] == "panel" else p["plank_t"])


CORNER_DETAILS = ("mitre", "lap", "profile", "butt")     # Mitred, Master, Profile, Square


def corner_detail(p, override=None, k=None):
    """The corner detail in force: *override* when a corner sets its own, else the job
    setting. Master ("lap") is a panel detail: one board runs past the corner and the
    other stops a joint gap clear of it. Plank cladding has no master board to lap, so
    it falls back to a mitre. Profile is for right-angled external corners: the aluminium
    profile with panels, the square timber trim with planks (cladding_corners); at a
    re-entrant corner (k <= 0) or any other angle it falls back to a mitre too."""
    detail = override if override in CORNER_DETAILS else p.get("corner", "mitre")
    detail = detail if detail in CORNER_DETAILS else "mitre"
    panel = p["cladding_type"] == "panel"
    square = k is None or (k > 0 and abs(k - 1.0) < 0.05)
    if p.get("seam"):
        # Standing seam: "lap" is a seam stood on the corner (the master face carries it),
        # "profile" a corner flashing strip; both at right-angled external corners only.
        return detail if detail in ("mitre", "butt") or square else "mitre"
    if detail == "lap" and not panel:
        return "mitre"
    if detail == "profile" and not square:
        return "mitre"
    return detail


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
        detail = corner_detail(p, override, k if k else None)
        if detail == "butt" and k < 0:
            # Square at a re-entrant corner cannot be both stopping on the wall's corner
            # line: the two buildups would share the corner. One runs in and the other
            # stops against it, as with Master; under standing seam they meet on the
            # bisector, as its other corners do.
            return ((k, 0.0) if p.get("seam") else lap(k, master)), detail
        if detail == "butt":
            return (0.0, 0.0), detail
        # A profiled corner cuts the layers behind as a mitre, so the battens run on under
        # the profile's flanges; the boards stop short of it (cladding_geometry). So does a
        # standing seam's corner seam: the deck and battens mitre, the trays are set after.
        if detail in ("mitre", "profile") or p.get("seam"):
            return (k, 0.0), detail
        return lap(k, master), detail

    (left, d_left), (right, d_right) = end(lo, master_lo, over_lo), end(hi, master_hi, over_hi)
    return left, right, (d_left, d_right)


def end_master(elev, side):
    """Does this elevation master the corner at its *side* ("l" or "r")? The flags are
    held in run order, lo first, so a reversed face reads them the other way round."""
    lo_side = "r" if elev.get("chain_reversed") else "l"
    return bool(elev.get("master_lo") if side == lo_side else elev.get("master_hi"))


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


def ring_at(mesh, k, s):
    """Ring *k* of a prism (0 the profile, then its holes) where it is *s* deep: the
    chain-corner shear found by position, then the shift each vertex carries where the
    board is mitred to a window or door lining ("vshift", one per ring vertex:
    u += a + b·s, v += c + d·s). Shared by the IFC and DXF writers."""
    rings = [mesh["profile"]] + list(mesh.get("holes") or [])
    corner = mesh.get("corner")
    pts = corner_ring(rings[k], corner, s) if corner else [(float(u), float(v)) for u, v in rings[k]]
    shifts = mesh.get("vshift") or []
    if k < len(shifts) and shifts[k]:
        pts = [(u + a + b * s, v + c + d * s) for (u, v), (a, b, c, d) in zip(pts, shifts[k])]
    return pts


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
        run, spans, placed = 0.0, [], {}
        for e in members:
            lo, hi = clip_bounds(e)
            width = hi - lo
            # The second slope of a ridge lies along the first, not after it: it takes the
            # first's place in the run (moved by however the app set one against the other,
            # so their seams line through over the ridge) and adds nothing to its length.
            first = placed.get((e.get("ridge") or {}).get("with"))
            if first is not None:
                spans.append((e.get("name"), first[1] + float(e.get("chain_start") or 0.0) - first[0]))
                continue
            placed[e.get("name")] = (float(e.get("chain_start") or 0.0), run)
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
