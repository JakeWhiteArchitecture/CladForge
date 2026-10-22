"""CladForge — packing a chain's panels onto stock boards (or its planks onto stock
lengths), and the cutting plan.

Every panel of a chain as built — closing cuts, rows of different heights and panels
notched round openings — is a piece cut from a stock board. A notched panel is cut
from one rectangle, so it packs as its bounding rectangle while only its net area
counts as used.

The packing is 2D guillotine bin packing, because a panel saw cuts edge to edge (J.
Jylänki, "A Thousand Ways to Pack the Bin", 2010, section 3). Each piece is inflated
by the kerf, and so is the board, so a saw cut costs a kerf between pieces but not at
the board's edges. Offcuts stay on their board as free rectangles for later pieces.
Several deterministic heuristics are run and the best result kept: fewest boards,
then least waste — sort order × placement rule × split rule.

The cutting plan packs each elevation on its own (chain_plan), so every board is cut
for one elevation, and puts the boards and their pieces in wall order, left to right.
That is the order the cutting run plays them in and the cutting plan DXF draws them.

Planks are cut from stock lengths (the plank's stock length), so their packing is
one-dimensional (pack_lengths): each piece takes its length and a kerf from a length,
any end trim comes off both ends first, and a plank ripped narrower than the face (a
closing course) is still cut from a full-width length. The plan has the same shape as
a panel plan, each length a board stock-length long and a plank wide, so the readout,
the cutting plan and the cutting run read either.

Pure Python: it runs in Pyodide with the rest of the engine.
"""

import bisect
import math

from shapely.geometry import Polygon

from cladding_primitives import ring_at

MAX_PIECES = 400            # packed at most; beyond this the plan says it is capped
EPS = 1e-6
PLAN_NOTE = "Cutting plan for setting-out. Not a quantity take-off for pricing."

# Sort orders, all descending, ties by name so the order never depends on the input's.
SORTS = (("area", lambda p: p["w"] * p["h"]), ("longest side", lambda p: max(p["w"], p["h"])),
         ("height", lambda p: p["h"]), ("width", lambda p: p["w"]),
         ("perimeter", lambda p: p["w"] + p["h"]), ("shortest side", lambda p: min(p["w"], p["h"])))


# Which free rectangle a piece goes in (Jylänki's six): lower scores win.
def _baf(fw, fh, w, h):      # best area fit, then the short side
    return (fw * fh - w * h, min(fw - w, fh - h))


def _bssf(fw, fh, w, h):     # best short side fit, then the long side
    return (min(fw - w, fh - h), max(fw - w, fh - h))


def _blsf(fw, fh, w, h):     # best long side fit, then the short side
    return (max(fw - w, fh - h), min(fw - w, fh - h))


def _worst(rule):
    return lambda fw, fh, w, h: tuple(-x for x in rule(fw, fh, w, h))


PLACEMENTS = (("best area fit", _baf), ("best short side fit", _bssf), ("best long side fit", _blsf),
              ("worst area fit", _worst(_baf)), ("worst short side fit", _worst(_bssf)),
              ("worst long side fit", _worst(_blsf)))


# How what is left of a free rectangle is cut in two (Jylänki's six). Each says whether
# the cut runs horizontally, right across the free rectangle under the piece, from the
# leftover width rw and height rh, the piece w × h and the free rectangle fw × fh.
SPLITS = (("shorter leftover axis", lambda rw, rh, w, h, fw, fh: rw < rh),
          ("longer leftover axis", lambda rw, rh, w, h, fw, fh: rw >= rh),
          ("minimise area", lambda rw, rh, w, h, fw, fh: w * rh > h * rw),
          ("maximise area", lambda rw, rh, w, h, fw, fh: w * rh <= h * rw),
          ("shorter axis", lambda rw, rh, w, h, fw, fh: fw <= fh),
          ("longer axis", lambda rw, rh, w, h, fw, fh: fw > fh))

# The quick check that runs after every rebuild: the original 16 strategies.
QUICK = [(s, pl, sp) for s in SORTS[:4] for pl in PLACEMENTS[:2] for sp in SPLITS[:2]]
# The full search (Optimise boards, and the cutting plan): every sort × placement × split,
# then seeded shuffles of the piece order under the best rules, within a fixed amount of
# work, so the same job always gives the same layout on any machine.
# The quick 16 go first, so the full search can only improve on the quick check.
DEEP = QUICK + [(s, pl, sp) for s in SORTS for pl in PLACEMENTS for sp in SPLITS if (s, pl, sp) not in QUICK]
DEEP_WORK = 1_500_000       # free-rectangle checks the deep search may spend: about 2 s
SHUFFLE_SEED = 20100601


def chain_pieces(geometry, elevations, kind="panel"):
    """[{"name", "w", "h", "area", "elevation", "u", "v"}] for every panel of the given
    elevations. w × h is the piece's bounding rectangle over both its faces (a mitred or
    lapped end runs further on one face than the other); area is the net area of its
    outer face; u, v is its bottom left corner on the elevation, seen from outside."""
    out = []
    for m in geometry:
        if m.get("ifc_type") != kind or m.get("elevation") not in elevations:
            continue
        near = float(m["depth"])
        far = near + float(m["thickness"])
        pts = ring_at(m, 0, near) + ring_at(m, 0, far)
        us, vs = [q[0] for q in pts], [q[1] for q in pts]
        try:
            face = Polygon(ring_at(m, 0, far), [ring_at(m, k + 1, far) for k in range(len(m.get("holes") or []))])
            area = abs(face.area) if face.is_valid else abs(face.buffer(0).area)
        except Exception:   # noqa: BLE001 — a degenerate outline counts its rectangle
            area = (max(us) - min(us)) * (max(vs) - min(vs))
        out.append({"name": m.get("name", "Panel"), "w": round(max(us) - min(us), 1),
                    "h": round(max(vs) - min(vs), 1), "area": area, "elevation": m.get("elevation"),
                    "u": round(min(us), 1), "v": round(min(vs), 1)})
    return out


class _Work:
    """Counts free-rectangle checks, the deterministic measure of search effort."""
    def __init__(self):
        self.n = 0


def _place(boards, p, kerf, rotate, score, split, useful, work, new_board=None):
    """Put piece *p* in the best free rectangle over all *boards*, or on a new board from
    *new_board* (BW, BH) when none takes it. Returns False when it fits nowhere."""
    w, h = p["w"] + kerf, p["h"] + kerf
    orients = [(w, h, False)] + ([(h, w, True)] if rotate and abs(w - h) > EPS else [])
    best = None
    for bi, b in enumerate(boards):
        work.n += len(b["free"])
        for fi, (fx, fy, fw, fh) in enumerate(b["free"]):
            for ow, oh, rot in orients:
                if ow <= fw + EPS and oh <= fh + EPS:
                    key = (score(fw, fh, ow, oh), bi, fi, rot)
                    if best is None or key < best[0]:
                        best = (key, bi, fi, ow, oh, rot)
    if best is None:
        if new_board is None:
            return False
        BW, BH = new_board
        fits = [(ow, oh, rot) for ow, oh, rot in orients if ow <= BW + EPS and oh <= BH + EPS]
        if not fits:
            return False
        boards.append({"pieces": [], "free": [(0.0, 0.0, BW, BH)], "scrap": []})
        ow, oh, rot = min(fits, key=lambda f: (score(BW, BH, f[0], f[1]), f[2]))
        best = (None, len(boards) - 1, 0, ow, oh, rot)
    _key, bi, fi, ow, oh, rot = best
    b = boards[bi]
    fx, fy, fw, fh = b["free"].pop(fi)
    b["pieces"].append({"name": p["name"], "x": fx, "y": fy, "w": ow - kerf, "h": oh - kerf,
                        "cut_w": p["w"], "cut_h": p["h"], "rotated": rot, "area": p["area"],
                        "u": p.get("u", 0.0), "v": p.get("v", 0.0)})
    # Guillotine split of what is left of the free rectangle: one cut right across it.
    rw, rh = fw - ow, fh - oh
    if split(rw, rh, ow, oh, fw, fh):
        parts = [(fx + ow, fy, rw, oh), (fx, fy + oh, fw, rh)]
    else:
        parts = [(fx + ow, fy, rw, fh), (fx, fy + oh, ow, rh)]
    for q in parts:
        if q[2] > EPS and q[3] > EPS:
            (b["free"] if useful(q) else b["scrap"]).append(q)
    return True


def _useful(pieces, kerf):
    """A free rectangle no piece could ever fit is an offcut, not somewhere to look."""
    short = min((min(p["w"], p["h"]) + kerf for p in pieces), default=0.0)
    long_ = min((max(p["w"], p["h"]) + kerf for p in pieces), default=0.0)
    return lambda q: min(q[2], q[3]) + EPS >= short and max(q[2], q[3]) + EPS >= long_


def _pack(order, BW, BH, kerf, rotate, score, split, useful, work):
    """One run: every piece in *order*, onto as many boards as it takes."""
    boards = []
    for p in order:
        _place(boards, p, kerf, rotate, score, split, useful, work, new_board=(BW, BH))
    return boards


def _eliminate(boards, BW, BH, kerf, rotate, work):
    """Empty a board where possible: take the least-used board and try to fit all of its
    pieces into the other boards' leftover space (offcuts included). Repeats while it
    saves a board. Returns how many boards it emptied."""
    import copy
    saved = 0
    everything = lambda q: True  # noqa: E731 — every leftover is worth a look here
    while len(boards) > 1:
        order = sorted(range(len(boards)), key=lambda i: (sum(q["area"] for q in boards[i]["pieces"]), i))
        done = False
        for i in order[:3]:                                  # the three emptiest are worth trying
            trial = copy.deepcopy([b for k, b in enumerate(boards) if k != i])
            for b in trial:
                b["free"] = b["free"] + b["scrap"]
                b["scrap"] = []
            moving = sorted(({"name": q["name"], "w": q["cut_w"], "h": q["cut_h"], "area": q["area"],
                              "u": q.get("u", 0.0), "v": q.get("v", 0.0)}
                             for q in boards[i]["pieces"]), key=lambda p: (-p["w"] * p["h"], p["name"]))
            if all(_place(trial, p, kerf, rotate, _bssf, SPLITS[0][1], everything, work) for p in moving):
                boards[:] = trial
                saved += 1
                done = True
                break
        if not done:
            break
    return saved


def _shuffled(pieces, n, seed):
    import random
    rng = random.Random(seed)
    for _k in range(n):
        order = sorted(pieces, key=lambda p: p["name"])
        rng.shuffle(order)
        yield order


def pack(pieces, board_w, board_h, kerf=3.0, rotate=True, trim=0.0, deep=False, work_limit=DEEP_WORK):
    """The best packing: fewest boards, then least waste, then the first strategy in
    order. *trim* is cut off every edge of the board first (factory edges), so pieces
    come from the board less 2 × trim each way. The quick check runs 16 strategies; *deep*
    runs every one, then shuffled piece orders, then tries to empty a board, within
    *work_limit* free-rectangle checks. Deterministic: the same pieces give the same layout."""
    trim = max(0.0, float(trim or 0.0))
    uw, uh = board_w - 2 * trim, board_h - 2 * trim            # what is left to cut from
    fits = lambda p: ((p["w"] <= uw + EPS and p["h"] <= uh + EPS) or  # noqa: E731
                      (rotate and p["w"] <= uh + EPS and p["h"] <= uw + EPS))
    oversize = [p["name"] for p in pieces if not fits(p)]
    usable = sorted((p for p in pieces if fits(p)), key=lambda p: p["name"])
    capped = max(0, len(usable) - MAX_PIECES)
    usable = usable[:MAX_PIECES]
    net = sum(p["area"] for p in usable)
    board_area = board_w * board_h
    lower = int(math.ceil(net / (uw * uh) - 1e-9)) if net > 0 and uw > 0 and uh > 0 else 0
    BW, BH = uw + kerf, uh + kerf                  # the kerf goes between pieces only
    useful, work = _useful(usable, kerf), _Work()
    best, tried = None, 0

    def consider(boards, name):
        nonlocal best
        waste = 1.0 - net / (len(boards) * board_area) if boards else 0.0
        if best is None or (len(boards), round(waste, 9)) < (len(best[0]), round(best[1], 9)):
            best = (boards, waste, name)

    ranked = []
    for (sort_name, sort_key), (place_name, score), (split_name, split) in (DEEP if deep else QUICK):
        order = sorted(usable, key=lambda p: (-sort_key(p), p["name"]))
        boards = _pack(order, BW, BH, kerf, rotate, score, split, useful, work)
        tried += 1
        consider(boards, "%s, %s, split on the %s" % (sort_name, place_name, split_name))
        ranked.append(((len(boards), round(1.0 - net / (len(boards) * board_area), 9) if boards else 0.0), place_name, split_name))
        if len(best[0]) <= lower or (deep and tried >= len(QUICK) and work.n > work_limit):
            break
    if deep and best and len(best[0]) > lower and work.n <= work_limit:
        # Shuffled orders under the rules that did best, while the work allowance lasts.
        rules = [(pl, sp) for pl in PLACEMENTS for sp in SPLITS]
        score_of = lambda r: min(x[0] for x in ranked if x[1] == r[0][0] and x[2] == r[1][0])  # noqa: E731
        top = sorted(rules, key=score_of)[:3]                     # stable: ties keep the list order
        for k, order in enumerate(_shuffled(usable, 10_000, SHUFFLE_SEED)):
            if work.n > work_limit or len(best[0]) <= lower:
                break
            (place_name, score), (split_name, split) = top[k % len(top)]
            consider(_pack(order, BW, BH, kerf, rotate, score, split, useful, work),
                     "shuffled order %d, %s, split on the %s" % (k + 1, place_name, split_name))
            tried += 1
    boards, waste, heuristic = best if best else ([], 0.0, "")
    emptied = 0
    if deep and len(boards) > max(lower, 1):
        emptied = _eliminate(boards, BW, BH, kerf, rotate, work)
        if emptied:
            waste = 1.0 - net / (len(boards) * board_area)
            heuristic += ", then %d board(s) emptied into the others' offcuts" % emptied
    fills = {}
    for b in boards:
        used = sum(q["area"] for q in b["pieces"])
        b["waste"] = 1.0 - used / board_area
        for q in b["pieces"]:
            q["x"] += trim
            q["y"] += trim
            fills[q["name"]] = round(used / board_area, 4)
        b["offcuts"] = [(x + trim, y + trim, min(w, uw - x), min(h, uh - y)) for x, y, w, h in b.pop("free") + b.pop("scrap")
                        if min(w, uw - x) > 1.0 and min(h, uh - y) > 1.0]
    return {"boards": boards, "n_boards": len(boards), "lower_bound": lower, "net_area": net,
            "waste": waste, "heuristic": heuristic, "oversize": oversize, "capped": capped,
            "n_pieces": len(usable), "board": [board_w, board_h], "kerf": kerf, "rotate": bool(rotate),
            "trim": trim, "deep": bool(deep), "strategies": tried, "emptied": emptied, "work": work.n,
            "fills": fills, "friendly": friendly_sizes(board_w, board_h, kerf, trim)}


def friendly_sizes(board_w, board_h, kerf, trim=0.0):
    """Cut sizes that divide the board with nothing over: n pieces and n - 1 saw cuts
    across the usable width, and up its length. Rounded down to the mm."""
    def split(length, most):
        return [int(math.floor((length - 2 * trim - (n - 1) * kerf) / n)) for n in range(1, most + 1)]
    return {"across": split(min(board_w, board_h), 3), "along": split(max(board_w, board_h), 5)}


MAX_PLANKS = 2000           # plank pieces packed at most, per elevation
TOGETHER_REACH = 4000.0     # mm: pieces further apart than this (centre to centre, each way) are not neighbours
TOGETHER_NEAR = 12          # the nearest pieces looked at for each piece
TOGETHER_WORK = 3_000_000   # spread evaluations the keep-together pass may spend
LENGTH_PLACEMENTS = (("first fit", lambda free, need: 0), ("best fit", lambda free, need: free - need),
                     ("worst fit", lambda free, need: need - free))


def plank_pieces(geometry, elevations, orient):
    """[{"name", "length", "width", "area", "elevation", "u", "v"}] for every plank of the
    given elevations: length along the run (u for horizontal planks, v for vertical),
    width across it, both over the plank's faces, area its net outer face."""
    out = []
    for q in chain_pieces(geometry, elevations, "plank"):
        length, width = (q["w"], q["h"]) if orient == "horizontal" else (q["h"], q["w"])
        out.append(dict(q, length=length, width=width))
    return out


def _gap(a, b):
    """Edge to edge distance between two pieces on the wall, 0 where they touch."""
    aw, ah, bw, bh = a.get("w", 0.0), a.get("h", 0.0), b.get("w", 0.0), b.get("h", 0.0)
    dx = max(0.0, a["u"] - (b["u"] + bw), b["u"] - (a["u"] + aw))
    dy = max(0.0, a["v"] - (b["v"] + bh), b["v"] - (a["v"] + ah))
    return math.hypot(dx, dy)


def _spread(ps):
    """How far apart the pieces cut from one length lie on the wall: the shortest chain
    linking them edge to edge (a minimum spanning tree), so pieces that abut add nothing."""
    if len(ps) < 2:
        return 0.0
    rest, total = list(ps[1:]), 0.0
    best = [_gap(ps[0], q) for q in rest]
    while rest:
        k = min(range(len(rest)), key=lambda i: (best[i], rest[i]["name"]))
        total += best[k]
        q = rest.pop(k)
        best.pop(k)
        best = [min(d, _gap(q, r)) for d, r in zip(best, rest)]
    return total


def _neighbours(pieces):
    """{name: the pieces nearest it on the wall, nearest first}, from those within reach."""
    centre = {p["name"]: (p["u"] + p.get("w", 0.0) / 2.0, p["v"] + p.get("h", 0.0) / 2.0) for p in pieces}
    by_x = sorted(pieces, key=lambda p: (centre[p["name"]][0], p["name"]))
    xs = [centre[p["name"]][0] for p in by_x]
    out = {}
    for p in pieces:
        cx, cy = centre[p["name"]]
        lo, hi = bisect.bisect_left(xs, cx - TOGETHER_REACH), bisect.bisect_right(xs, cx + TOGETHER_REACH)
        near = [q for q in by_x[lo:hi] if q is not p and abs(centre[q["name"]][1] - cy) <= TOGETHER_REACH]
        near.sort(key=lambda q: (_gap(p, q), q["name"]))
        out[p["name"]] = near[:TOGETHER_NEAR]
    return out


def keep_together(bars, cap, kerf, budget=TOGETHER_WORK, caps=None, pooled=()):
    """Bring the pieces cut from each stock length together on the wall, so a length's
    pieces go up side by side (two of the same length abutting, the offcut on the best
    fitting piece nearby) rather than scattered over the face. *bars* is [[free, pieces]].
    Moves one piece to another length, or swaps two between lengths, wherever that
    shortens the chains linking each length's pieces (_spread), until nothing does or the
    budget is spent. A length never overfills and none is added: the count can only fall
    (a move that empties a length saves it), so the waste is never worse. Deterministic.
    *caps* gives each bar its own room (an offcut carried on from another elevation has
    less than a new length), and the pieces on the *pooled* bars (those offcuts) are kept
    together as one more group as well, so what is cut from offcuts goes up in one place."""
    groups = [list(ps) for _free, ps in bars]
    if len(groups) < 2:
        return bars
    by_bar = caps is not None or bool(pooled)       # then bar by bar back, empty ones too
    caps = list(caps) if caps else [cap] * len(groups)
    pooled = set(pooled)
    need = lambda p: p["length"] + kerf  # noqa: E731
    used = [sum(need(p) for p in g) for g in groups]
    pool = lambda gs: _spread([q for i in sorted(pooled) for q in gs[i]]) if pooled else 0.0  # noqa: E731
    cost = [_spread(g) for g in groups]
    pool_cost = pool(groups)
    where = {p["name"]: i for i, g in enumerate(groups) for p in g}
    pieces = sorted((p for g in groups for p in g), key=lambda p: p["name"])
    near, work, improved = _neighbours(pieces), 0, True

    def try_bars(p, a, bars_b):
        nonlocal work
        best, rest_a = None, [x for x in groups[a] if x is not p]
        for b in bars_b:
            options = []
            if used[b] + need(p) <= caps[b] + EPS:          # move p over
                options.append((rest_a, groups[b] + [p], None))
            for r in groups[b]:                              # or swap it for one of b's
                if used[a] - need(p) + need(r) <= caps[a] + EPS and used[b] - need(r) + need(p) <= caps[b] + EPS:
                    options.append((rest_a + [r], [x for x in groups[b] if x is not r] + [p], r))
            for ga, gb, r in options:
                work += len(ga) ** 2 + len(gb) ** 2
                delta = _spread(ga) + _spread(gb) - cost[a] - cost[b]
                if (a in pooled) != (b in pooled):           # in or out of the offcuts: their group changes
                    trial = list(groups)
                    trial[a], trial[b] = ga, gb
                    work += sum(len(groups[i]) for i in pooled) ** 2
                    delta += pool(trial) - pool_cost
                key = (delta, b, r["name"] if r else "")
                if delta < -0.5 and (best is None or key < best[0]):
                    best = (key, b, ga, gb, r)
        return best

    while improved and work < budget:
        improved = False
        for p in pieces:
            a = where[p["name"]]
            best = try_bars(p, a, dict.fromkeys(where[q["name"]] for q in near[p["name"]] if where[q["name"]] != a))
            if best is None and len(groups[a]) > 1 and _spread([x for x in groups[a] if x is not p]) < cost[a] - 0.5:
                # Left far from its length's other pieces and nothing nearby has room:
                # still find the closest place it can go, looking at every length.
                best = try_bars(p, a, [b for b in range(len(groups)) if b != a and groups[b]])
            if best:
                _key, b, ga, gb, r = best
                groups[a], groups[b] = ga, gb
                used[a], used[b] = sum(need(x) for x in ga), sum(need(x) for x in gb)
                cost[a], cost[b] = _spread(ga), _spread(gb)
                pool_cost = pool(groups)
                where[p["name"]] = b
                if r is not None:
                    where[r["name"]] = a
                improved = True
            if work >= budget:
                break
    if by_bar:
        return [[c - u, g] for c, u, g in zip(caps, used, groups)]
    return [[cap - u, g] for u, g in zip(used, groups) if g]


def _on_length(ps, start, kerf, width):
    """Plank pieces cut one after another along a length from *start*, in wall order."""
    x, placed = start, []
    for p in sorted(ps, key=lambda p: (round(p.get("u", 0)), round(p.get("v", 0)), p["name"])):
        placed.append({"name": p["name"], "x": x, "y": 0.0, "w": p["length"], "h": min(p["width"], width),
                       "cut_w": p["length"], "cut_h": p["width"], "rotated": False, "area": p["area"],
                       "u": p.get("u", 0.0), "v": p.get("v", 0.0), "ripped": p["width"] < width - 0.5})
        x += p["length"] + kerf
    return placed


def pack_lengths(pieces, stock, width, kerf=3.0, trim=0.0, deep=False, work_limit=DEEP_WORK):
    """Planks onto stock lengths: one-dimensional bin packing, each piece taking its
    length and a kerf, the length less *trim* off each end. Several orders × placement
    rules (first, best and worst fit), fewest lengths then least waste, deterministic;
    *deep* adds seeded shuffles while the work lasts. Returns a plan shaped like pack()'s:
    each length a board *stock* × *width*, each piece at x along it, y = 0, its cut
    length × cut width (a plank ripped to a closing course is narrower than the face)."""
    trim = max(0.0, float(trim or 0.0))
    usable = stock - 2 * trim
    oversize = [p["name"] for p in pieces if p["length"] > usable + EPS]
    usable_pieces = sorted((p for p in pieces if p["length"] <= usable + EPS), key=lambda p: p["name"])
    capped = max(0, len(usable_pieces) - MAX_PLANKS)
    usable_pieces = usable_pieces[:MAX_PLANKS]
    net = sum(p["area"] for p in usable_pieces)
    board_area = stock * width
    lower = int(math.ceil(sum(p["length"] for p in usable_pieces) / usable - 1e-9)) if usable_pieces and usable > 0 else 0
    cap = usable + kerf                      # the kerf goes between pieces only
    work = _Work()

    def run(order, rule):
        bars = []                            # [free length, [pieces]]
        for p in order:
            need = p["length"] + kerf
            work.n += len(bars)
            fit = [(rule(b[0], need), i) for i, b in enumerate(bars) if b[0] + EPS >= need]
            if fit:
                bars[min(fit)[1]][1].append(p)
                bars[min(fit)[1]][0] -= need
            else:
                bars.append([cap - need, [p]])
        return bars

    orders = [("longest first", sorted(usable_pieces, key=lambda p: (-p["length"], p["name"]))),
              ("in wall order", sorted(usable_pieces, key=lambda p: (round(p.get("u", 0)), round(p.get("v", 0)), p["name"])))]
    best, tried = None, 0
    for order_name, order in orders:
        for rule_name, rule in (LENGTH_PLACEMENTS if deep else LENGTH_PLACEMENTS[:2]):
            bars = run(order, rule)
            tried += 1
            key = (len(bars), -max((b[0] for b in bars), default=0.0))   # then the longest offcut kept
            if best is None or key < best[0]:
                best = (key, bars, "%s, %s" % (order_name, rule_name))
    if deep and best and len(best[1]) > lower:
        for k, order in enumerate(_shuffled(usable_pieces, 10_000, SHUFFLE_SEED)):
            if work.n > work_limit or len(best[1]) <= lower:
                break
            rule_name, rule = LENGTH_PLACEMENTS[k % len(LENGTH_PLACEMENTS)]
            bars = run(order, rule)
            tried += 1
            key = (len(bars), -max((b[0] for b in bars), default=0.0))
            if key < best[0]:
                best = (key, bars, "shuffled order %d, %s" % (k + 1, rule_name))
    bars, heuristic = (best[1], best[2]) if best else ([], "")
    if deep and len(bars) > 1:
        # The full search also keeps each length's cuts together on the wall; the quick
        # check only counts, and the count and waste are the same either way.
        n = len(bars)
        bars = keep_together(bars, cap, kerf)
        heuristic += ", then cuts kept together" + (" (%d length(s) saved)" % (n - len(bars)) if len(bars) < n else "")
    boards, fills = [], {}
    for _free, ps in bars:
        placed = _on_length(ps, trim, kerf, width)
        used = sum(q["area"] for q in placed)
        end = placed[-1]["x"] + placed[-1]["w"] if placed else trim
        offcuts = [(end, 0.0, stock - trim - end, width)] if stock - trim - end > 1.0 else []
        boards.append({"pieces": placed, "waste": 1.0 - used / board_area, "offcuts": offcuts})
        for q in placed:
            fills[q["name"]] = round(used / board_area, 4)
    return {"boards": boards, "n_boards": len(boards), "lower_bound": lower, "net_area": net,
            "waste": 1.0 - net / (len(boards) * board_area) if boards else 0.0, "heuristic": heuristic,
            "oversize": oversize, "capped": capped, "n_pieces": len(usable_pieces), "board": [stock, width],
            "kerf": kerf, "rotate": False, "trim": trim, "deep": bool(deep), "strategies": tried, "emptied": 0,
            "work": work.n, "fills": fills, "kind": "plank",
            "spread": round(sum(_spread(b["pieces"]) for b in boards) / 1000.0, 2),   # m, how scattered the cuts are
            "friendly": {"across": [width], "along": friendly_sizes(stock, stock, kerf, trim)["along"]}}


def wall_order(boards):
    """Put each board's pieces in wall order, left to right then bottom up, and the boards
    in the order of their first piece: the order a fitter works along the elevation."""
    at = lambda q: (round(q.get("u", 0.0)), round(q.get("v", 0.0)), q["name"])  # noqa: E731
    for b in boards:
        b["pieces"].sort(key=at)
    boards.sort(key=lambda b: at(b["pieces"][0]) if b["pieces"] else (math.inf, math.inf, ""))
    return boards


OFFCUT_KEEP = 150.0     # mm: an offcut shorter or narrower than this is sawdust, not carried on or shown


def _stock_states(boards, plank, kerf, trim, uw, uh):
    """What is left of each board to cut from: a plank length's used run (pieces and
    kerfs), or a board's free rectangles in packing coordinates (trim off, kerf slack
    back on the far edges, as _place sees them)."""
    out = []
    for b in boards:
        if plank:
            out.append({"board": b, "used": sum(q["w"] + kerf for q in b["pieces"])})
        else:
            free = []
            for x, y, w, h in b["offcuts"]:
                x, y = x - trim, y - trim
                free.append((x, y, w + (kerf if x + w >= uw - 0.5 else 0.0), h + (kerf if y + h >= uh - 0.5 else 0.0)))
            out.append({"board": b, "free": free})
    return out


def _state_offcuts(st, plank, kerf, trim, stock_w, uw, uh, least=1.0):
    """The offcuts left on a board, in board millimetres (x, y, w, h), none under *least*."""
    if plank:
        end = trim + st["used"] - kerf if st["board"]["pieces"] or st["board"].get("later") else trim
        left = stock_w - trim - end
        return [(end, 0.0, left, st["board"]["width"])] if left >= least else []
    return [(x + trim, y + trim, min(w, uw - x), min(h, uh - y)) for x, y, w, h in st["free"]
            if min(w, uw - x) >= least and min(h, uh - y) >= least]


def _seeded(pieces, states, plank, kerf, trim, cap, rotate, width, BW, BH, deep, work):
    """The elevation's pieces packed with the carried offcuts already on the rack, new
    boards opened only when none takes a piece, under each order and rule: the one
    opening fewest new boards wins, then the one cutting most from offcuts, then the one
    whose pieces cut from offcuts lie closest together on the wall (_spread). Returns (the states as they would be, [(state index,
    placed piece)] for the pieces cut from offcuts, the pieces left for new boards)."""
    best = None
    if plank:
        orders = [sorted(pieces, key=lambda p: (-p["length"], p["name"])),
                  sorted(pieces, key=lambda p: (round(p.get("u", 0)), round(p.get("v", 0)), p["name"]))]
        for order in orders:
            for _name, rule in (LENGTH_PLACEMENTS if deep else LENGTH_PLACEMENTS[:2]):
                bars = [[cap - st["used"], [], i] for i, st in enumerate(states)]
                for p in order:
                    need = p["length"] + kerf
                    work.n += len(bars)
                    fit = [(rule(bar[0], need), k) for k, bar in enumerate(bars) if bar[0] + EPS >= need]
                    if fit:
                        bars[min(fit)[1]][1].append(p)
                        bars[min(fit)[1]][0] -= need
                    else:
                        bars.append([cap - need, [p], None])
                reused = [q for bar in bars if bar[2] is not None for q in bar[1]]
                key = (sum(1 for bar in bars if bar[2] is None), -len(reused), _spread(reused))
                if best is None or key < best[0]:
                    best = (key, bars)
        trial, placed, rest = [dict(st) for st in states], [], []
        for _free, ps, i in best[1]:
            if i is None:
                rest += ps
                continue
            for p in sorted(ps, key=lambda p: (round(p.get("u", 0)), round(p.get("v", 0)), p["name"])):
                placed.append((i, {"name": p["name"], "x": trim + trial[i]["used"], "y": 0.0, "w": p["length"],
                                   "h": min(p["width"], width), "cut_w": p["length"], "cut_h": p["width"], "rotated": False,
                                   "area": p["area"], "u": p.get("u", 0.0), "v": p.get("v", 0.0),
                                   "ripped": p["width"] < width - 0.5}))
                trial[i]["used"] += p["length"] + kerf
        return trial, placed, rest
    useful = _useful(pieces, kerf)
    for (_s, sort_key), (_pl, score), (_sp, split) in (DEEP if deep else QUICK):
        boards = [{"pieces": [], "free": list(st["free"]), "scrap": [], "carried": i} for i, st in enumerate(states)]
        for p in sorted(pieces, key=lambda p: (-sort_key(p), p["name"])):
            _place(boards, p, kerf, rotate, score, split, useful, work, new_board=(BW, BH))
        reused = [q for b in boards if "carried" in b for q in b["pieces"]]
        key = (sum(1 for b in boards if "carried" not in b), -len(reused),
               _spread([dict(q, w=q["cut_w"], h=q["cut_h"]) for q in reused]))
        if best is None or key < best[0]:
            best = (key, boards)
    trial, placed, rest = [dict(st) for st in states], [], []
    by_name = {p["name"]: p for p in pieces}
    for b in best[1]:
        if "carried" not in b:
            rest += [by_name[q["name"]] for q in b["pieces"]]
            continue
        trial[b["carried"]]["free"] = b["free"] + b["scrap"]
        placed += [(b["carried"], dict(q, x=q["x"] + trim, y=q["y"] + trim)) for q in b["pieces"]]
    return trial, placed, rest


def _together_with_offcuts(states, placed, new, by_name, cap, kerf, trim, width):
    """The keep-together pass over an elevation's pieces cut from offcuts (*states* as
    they were before it) and from new lengths at once: each offcut's pieces close together, all those cut from offcuts in
    one place on the wall (a pooled group), each new length's together, never a new
    length more. Returns (states, placed, new boards) as rearranged."""
    carried = [[] for _ in states]
    for i, q in placed:
        carried[i].append(by_name[q["name"]])
    bars = [[0.0, ps] for ps in carried] + [[0.0, [by_name[q["name"]] for q in b["pieces"]]] for b in new]
    caps = [cap - st["used"] for st in states] + [cap] * len(new)       # *states* as they were before
    out = keep_together(bars, cap, kerf, caps=caps, pooled=range(len(states)))
    trial, placed = [], []
    for i, (st, (_free, ps)) in enumerate(zip(states, out)):
        trial.append(dict(st, used=st["used"] + sum(q["length"] + kerf for q in ps)))
        placed += [(i, q) for q in _on_length(ps, trim + st["used"], kerf, width)]
    return trial, placed, [{"pieces": _on_length(ps, trim, kerf, width)} for _free, ps in out[len(states):] if ps]


def chain_plan(geometry, p, elevations, clad_area, deep=False):
    """The cutting plan for the given elevations' panels (one chain, or the whole job),
    with their clad area (m² net of openings). Each elevation is packed on its own, in the
    order given (a chain's elevations in turn round it), so every board is cut for one
    elevation; boards are numbered through the job in wall order. One row per elevation
    carries its own count and waste. Packing the whole job together could pass an offcut
    from one elevation to another: its board count (quick check) is kept as
    whole_job_boards, so the cost of keeping elevations apart is on show.

    With *share_offcuts*, an elevation may cut pieces from the offcuts of the elevations
    before it, wherever that saves buying a board (_cut_from_offcuts): such a piece is
    drawn on the board it comes from (its "later" pieces). Either way each elevation
    ends with a rack: the offcuts carried on (with sharing, from every board so far; else
    its own), each marked with the elevation that goes on to cut from it."""
    plank = p.get("cladding_type") == "plank"
    order = list(dict.fromkeys(elevations))
    if plank:
        # stock lengths: the plank's maximum length, a plank wide
        bw, bh = p["plank_len"], p["plank_w"]
        pieces = {e: plank_pieces(geometry, {e}, p["plank_orient"]) for e in order}
        packer = lambda ps, deep=False, work_limit=DEEP_WORK: pack_lengths(  # noqa: E731
            ps, bw, bh, p["kerf"], p.get("board_trim", 0.0), deep, work_limit)
    else:
        bw, bh = p["board_w"], p["board_h"]
        pieces = {e: chain_pieces(geometry, {e}) for e in order}
        packer = lambda ps, deep=False, work_limit=DEEP_WORK: pack(  # noqa: E731
            ps, bw, bh, p["kerf"], p["rotate"], p.get("board_trim", 0.0), deep, work_limit)
    total = sum(len(v) for v in pieces.values()) or 1
    sharing = bool(p.get("share_offcuts"))
    trim = max(0.0, float(p.get("board_trim", 0.0) or 0.0))
    kerf = p["kerf"]
    uw, uh = bw - 2 * trim, bh - 2 * trim
    cap = uw + kerf                                   # a plank length: the kerf between pieces only
    rotate = bool(p["rotate"]) and not plank
    keep = lambda st: _state_offcuts(st, plank, kerf, trim, bw, uw, uh, OFFCUT_KEEP)  # noqa: E731
    boards, rows, fills, oversize, plans, states, racks, reuse = [], [], {}, [], [], [], [], []
    work = _Work()
    for e in order:
        if not pieces[e]:
            continue
        # The deep search's work is shared out by piece count, so the job stays bounded.
        share = max(DEEP_WORK // 8, DEEP_WORK * len(pieces[e]) // total)
        pl = packer(pieces[e], deep=deep, work_limit=share)
        plans.append(pl)
        new, took = pl["boards"], []
        if sharing and states and pl["boards"]:
            # Cut what it can from the offcuts carried on, the rest from new boards, and
            # keep that only if it buys fewer boards than packing the elevation alone.
            fit = [q for q in pieces[e] if q["name"] in pl["fills"]]
            trial, placed, rest = _seeded(fit, states, plank, kerf, trim, cap, rotate, bh, uw + kerf, uh + kerf, deep, work)
            # Not worth packing what is left unless even its lower bound would buy fewer.
            bound = math.ceil(sum(q["length"] if plank else q["area"] for q in rest) / (uw if plank else uw * uh) - 1e-9)
            again = (packer(rest, deep=deep, work_limit=share) if rest else {"boards": [], "n_boards": 0}) \
                if placed and bound < pl["n_boards"] else {"n_boards": math.inf}
            if placed and again["n_boards"] < pl["n_boards"]:
                if plank and deep:
                    trial, placed, again["boards"] = _together_with_offcuts(
                        states, placed, again["boards"], {q["name"]: q for q in pieces[e]}, cap, kerf, trim, bh)
                states, new, took = trial, [b for b in again["boards"] if b["pieces"]], placed
        for b in new:
            b["width"] = bh
        if took:
            # Cut from an earlier board's offcut: drawn on that board, grouped by the
            # offcut on the last rack it came from, for the run and the plan.
            last = racks[-1]["offcuts"] if racks else []
            groups = {}
            for i, q in took:
                b = states[i]["board"]
                q = dict(q, elevation=e, source=b["no"])
                b.setdefault("later", []).append(q)
                at = next((o for o in last if o["no"] == b["no"] and o["rect"][0] - 0.5 <= q["x"] <= o["rect"][0] + o["rect"][2]
                           and o["rect"][1] - 0.5 <= q["y"] <= o["rect"][1] + o["rect"][3]), None)
                if at:
                    at["used_by"] = e
                key = at["id"] if at else "%s-%d-%.0f-%.0f" % (e, b["no"], q["x"], q["y"])
                g = groups.setdefault(key, {"elevation": e, "id": key, "no": b["no"], "k": b["no"] - 1,
                                            "rect": list(at["rect"]) if at else [q["x"], q["y"], q["w"], q["h"]], "pieces": []})
                g["pieces"].append(q)
            for g in groups.values():
                g["pieces"].sort(key=lambda q: (round(q.get("u", 0.0)), round(q.get("v", 0.0)), q["name"]))
            reuse += sorted(groups.values(), key=lambda g: (g["no"], g["rect"][0], g["rect"][1]))
        first = len(boards)
        for b in wall_order(new):
            b["elevation"] = e
            b["no"] = len(boards) + 1
            boards.append(b)
        own = _stock_states(boards[first:], plank, kerf, trim, uw, uh)
        states = states + own if sharing else own
        racks.append({"elevation": e, "offcuts": [
            {"id": "%d-%d" % (len(racks), n), "no": st["board"]["no"], "k": st["board"]["no"] - 1, "rect": list(r), "used_by": None}
            for n, (st, r) in enumerate((st, r) for st in states for r in keep(st))]})
        oversize += pl["oversize"]
        rows.append({"elevation": e, "n_boards": len(boards) - first, "waste": 0.0, "lower_bound": pl["lower_bound"],
                     "net_area": pl["net_area"], "heuristic": pl["heuristic"], "n_pieces": pl["n_pieces"],
                     "from_offcuts": len(took), "saved": pl["n_boards"] - (len(boards) - first)})
    # What every board ends up carrying (its own pieces and those cut later from its
    # offcuts): its offcuts, waste and the fill each piece's panel is tinted by.
    board_area = bw * bh
    if not plank:
        live = {id(st["board"]): st for st in states}
    for b in boards:
        everything = b["pieces"] + b.get("later", [])
        if plank:
            used_run = sum(q["w"] + kerf for q in everything)
            b["offcuts"] = _state_offcuts({"board": b, "used": used_run}, plank, kerf, trim, bw, uw, uh)
        elif id(b) in live:
            b["offcuts"] = _state_offcuts(live[id(b)], plank, kerf, trim, bw, uw, uh)
        used = sum(q["area"] for q in everything)
        b["waste"] = 1.0 - used / board_area
        for q in everything:
            fills[q["name"]] = round(used / board_area, 4)
    for r in rows:
        mine = [b for b in boards if b["elevation"] == r["elevation"]]
        r["waste"] = 1.0 - sum(q["area"] for b in mine for q in b["pieces"] + b.get("later", [])) / (len(mine) * board_area) if mine else 0.0
    whole = packer([q for e in order for q in pieces[e]])
    net = sum(r["net_area"] for r in rows)
    return {"boards": boards, "rows": rows, "n_boards": len(boards), "net_area": net,
            "lower_bound": sum(r["lower_bound"] for r in rows),
            "waste": 1.0 - net / (len(boards) * board_area) if boards else 0.0,
            "heuristic": "each elevation on its own; " + "; ".join("%s: %s" % (r["elevation"], r["heuristic"]) for r in rows),
            "oversize": oversize, "capped": sum(pl["capped"] for pl in plans), "n_pieces": sum(r["n_pieces"] for r in rows),
            "board": [bw, bh], "kerf": p["kerf"], "rotate": bool(p["rotate"]) and not plank, "trim": trim,
            "deep": bool(deep), "strategies": sum(pl["strategies"] for pl in plans),
            "emptied": sum(pl["emptied"] for pl in plans), "work": sum(pl["work"] for pl in plans) + whole["work"],
            "fills": fills, "kind": "plank" if plank else "panel",
            "friendly": whole["friendly"] if plank else friendly_sizes(bw, bh, p["kerf"], trim),
            "per_elevation": True, "whole_job_boards": whole["n_boards"], "clad_area": clad_area,
            "share_offcuts": sharing, "racks": racks, "reuse": reuse,
            "from_offcuts": sum(r["from_offcuts"] for r in rows), "saved": sum(r["saved"] for r in rows)}


# ── the cutting plan drawing ──────────────────────────────────────────────

def _hatch(dxf, x0, y0, x1, y1, step=60.0, layer="OFFCUT"):
    """45 degree lines across a rectangle: R12 has no hatch entity."""
    c = (y0 - x1)
    while c < y1 - x0:
        a, b = max(x0, y0 - c), min(x1, y1 - c)
        if b - a > 0.5:
            dxf.add_line((a, a + c), (b, b + c), layer)
        c += step


def plan_dxf(plans):
    """A DXF of every chain's cutting plan: one rectangle per board, to scale, each piece
    in place with its name and cut size, rotated pieces marked, offcuts hatched, the
    waste per board and in total. A plan packed per elevation (chain_plan) draws one row
    per elevation, its boards left to right in wall order and numbered through the job,
    the same order the cutting run plays; a plain packing draws its boards in one row.
    A plank plan draws its stock lengths standing up, side by side along the row, each
    labelled up its side, its pieces from the bottom in the order they are cut.
    *plans* is [(chain name, plan)]."""
    from dxf_generator import _DxfWriter
    dxf = _DxfWriter()
    for name, color in (("BOARD", 7), ("PIECE", 5), ("OFFCUT", 8), ("NOTES", 7)):
        dxf.add_layer(name, color)
    y = 0.0
    total_boards = sum(pl["n_boards"] for _n, pl in plans)
    total_net = sum(pl["net_area"] for _n, pl in plans)
    total_area = sum(pl["n_boards"] * pl["board"][0] * pl["board"][1] for _n, pl in plans)
    dxf.add_text(PLAN_NOTE, (0.0, 600.0), 90.0, "NOTES")
    dxf.add_text("Boards %d  -  total waste %.1f%%" % (total_boards, 100.0 * (1 - total_net / total_area) if total_area else 0.0),
                 (0.0, 420.0), 70.0, "NOTES")
    for chain, pl in plans:
        bw, bh = pl["board"]
        plank = pl.get("kind") == "plank"
        dw, dh = (bh, bw) if plank else (bw, bh)            # as drawn: a plank's length stands up
        gx, gy = dw + (400.0 if plank else 700.0), dh + 1100.0
        dxf.add_text("%s  -  %s, lower bound %d, waste %.1f%%, kerf %.0f%s%s%s" % (
            chain, ("%d length(s) of %.0f, %.0f wide" if plank else "%d board(s) of %.0f x %.0f") % (pl["n_boards"], bw, bh),
            pl["lower_bound"], 100.0 * pl["waste"], pl["kerf"],
            (", end trim %.0f" if plank else ", edge trim %.0f") % pl["trim"] if pl.get("trim") else "",
            ", rotation allowed" if pl["rotate"] else "",
            ("  -  %d piece(s) not packed (cap %d)" % (pl["capped"], MAX_PIECES)) if pl["capped"] else ""),
            (0.0, y), 70.0, "NOTES")
        if pl.get("per_elevation"):
            note = ("Offcuts passed on: an elevation cuts what it can from earlier elevations' offcuts, drawn on the %s they come from."
                    if pl.get("share_offcuts") else
                    "Each elevation packed on its own: every %s is cut for one elevation.") % ("length" if plank else "board")
            extra = pl["n_boards"] - pl.get("whole_job_boards", pl["n_boards"])
            if extra > 0:
                note += "  Packing the whole job together would need about %d fewer." % extra
            dxf.add_text(note, (0.0, y - 200.0), 45.0, "NOTES")
        else:
            dxf.add_text("Best of %d strategies: %s" % (pl.get("strategies", 0), pl.get("heuristic", "")),
                         (0.0, y - 200.0), 45.0, "NOTES")
        if pl["oversize"]:
            dxf.add_text("Larger than the board either way round: " + ", ".join(pl["oversize"]), (0.0, y - 110.0), 50.0, "NOTES")
        if pl.get("rows"):
            rows = [(r, [b for b in pl["boards"] if b.get("elevation") == r["elevation"]]) for r in pl["rows"]]
        else:
            rows = [(None, pl["boards"])]
        ry = y - 450.0                             # the top of the next row
        for row, boards in rows:
            if row:
                dxf.add_text("%s  -  %d %s, waste %.1f%%%s" % (row["elevation"], row["n_boards"],
                             "length(s)" if plank else "board(s)", 100.0 * row["waste"],
                             ", %d piece(s) cut from earlier offcuts" % row["from_offcuts"] if row.get("from_offcuts") else ""),
                             (0.0, ry + 60.0), 80.0, "NOTES")
                ry -= 250.0
            for i, b in enumerate(boards):         # left to right along the row
                _draw_board(dxf, pl, b, b.get("no", i + 1), i * gx, ry - dh)
            ry -= gy
        y = ry - 600.0
    return dxf.to_string()


def _draw_board(dxf, pl, b, no, ox, oy):
    bw, bh = pl["board"]
    plank = pl.get("kind") == "plank"
    if plank:
        return _draw_length(dxf, pl, b, no, ox, oy)
    dxf.add_ring([(0, 0), (bw, 0), (bw, bh), (0, bh)], "BOARD", ox, oy)
    t = pl.get("trim") or 0.0
    if t:                                # the squared-up edges, cut off first
        dxf.add_ring([(t, t), (bw - t, t), (bw - t, bh - t), (t, bh - t)], "OFFCUT", ox, oy)
        for x0, y0, x1, y1 in ((0, 0, bw, t), (0, bh - t, bw, bh), (0, t, t, bh - t), (bw - t, t, bw, bh - t)):
            _hatch(dxf, ox + x0, oy + y0, ox + x1, oy + y1, step=max(t, 20.0))
    dxf.add_text("Board %d  -  waste %.1f%%" % (no, 100.0 * b["waste"]), (ox, oy + bh + 60.0), 60.0, "NOTES")
    for q in b["pieces"] + b.get("later", []):          # later: cut from its offcut for a later elevation
        dxf.add_ring([(q["x"], q["y"]), (q["x"] + q["w"], q["y"]), (q["x"] + q["w"], q["y"] + q["h"]),
                      (q["x"], q["y"] + q["h"])], "PIECE", ox, oy)
        label = "%s  %.0f x %.0f%s" % (q["name"], q["cut_w"], q["cut_h"], " (rotated)" if q["rotated"] else "")
        dxf.add_text(label, (ox + q["x"] + 30.0, oy + q["y"] + q["h"] / 2.0), 40.0, "PIECE")
    for x, yy, w, h in b["offcuts"]:
        dxf.add_ring([(x, yy), (x + w, yy), (x + w, yy + h), (x, yy + h)], "OFFCUT", ox, oy)
        _hatch(dxf, ox + x, oy + yy, ox + x + w, oy + yy + h)


def _draw_length(dxf, pl, b, no, ox, oy):
    """A plank's stock length standing up, a plank wide and its length tall, the pieces
    from the bottom: a piece at x along the length is drawn at height x, across it at
    y. Labels run up the page, the length's beside it on the left."""
    L, w = pl["board"]
    dxf.add_ring([(0, 0), (w, 0), (w, L), (0, L)], "BOARD", ox, oy)
    t = pl.get("trim") or 0.0
    if t:                                  # the squared-up ends, cut off first
        for y0, y1 in ((0, t), (L - t, L)):
            dxf.add_ring([(0, y0), (w, y0), (w, y1), (0, y1)], "OFFCUT", ox, oy)
            _hatch(dxf, ox, oy + y0, ox + w, oy + y1, step=max(t, 20.0))
    dxf.add_text("Length %d  -  waste %.1f%%" % (no, 100.0 * b["waste"]), (ox - 40.0, oy), 50.0, "NOTES", rotation=90.0)
    th = min(35.0, 0.3 * w)
    for q in b["pieces"] + b.get("later", []):          # later: cut from its offcut for a later elevation
        x0, y0, x1, y1 = q["y"], q["x"], q["y"] + q["h"], q["x"] + q["w"]
        dxf.add_ring([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], "PIECE", ox, oy)
        label = "%s  %.0f%s" % (q["name"], q["cut_w"], ", ripped to %.0f" % q["cut_h"] if q.get("ripped") else "")
        dxf.add_text(label, (ox + (x0 + x1) / 2.0 + th / 2.0, oy + y0 + 20.0), th, "PIECE", rotation=90.0)
    for x, y, lw, lh in b["offcuts"]:
        x0, y0, x1, y1 = y, x, y + lh, x + lw
        dxf.add_ring([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], "OFFCUT", ox, oy)
        _hatch(dxf, ox + x0, oy + y0, ox + x1, oy + y1)
