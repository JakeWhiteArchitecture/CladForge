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

Planks are cut from stock lengths (the plank's maximum length), so their packing is
one-dimensional (pack_lengths): each piece takes its length and a kerf from a length,
any end trim comes off both ends first, and a plank ripped narrower than the face (a
closing course) is still cut from a full-width length. The plan has the same shape as
a panel plan, each length a board stock-length long and a plank wide, so the readout,
the cutting plan and the cutting run read either.

Pure Python: it runs in Pyodide with the rest of the engine.
"""

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
    boards, fills = [], {}
    for free, ps in bars:
        x, placed = trim, []
        for p in sorted(ps, key=lambda p: (round(p.get("u", 0)), round(p.get("v", 0)), p["name"])):   # in wall order
            placed.append({"name": p["name"], "x": x, "y": 0.0, "w": p["length"], "h": min(p["width"], width),
                           "cut_w": p["length"], "cut_h": p["width"], "rotated": False, "area": p["area"],
                           "u": p.get("u", 0.0), "v": p.get("v", 0.0), "ripped": p["width"] < width - 0.5})
            x += p["length"] + kerf
        used = sum(q["area"] for q in placed)
        end = x - kerf if placed else trim
        offcuts = [(end, 0.0, stock - trim - end, width)] if stock - trim - end > 1.0 else []
        boards.append({"pieces": placed, "waste": 1.0 - used / board_area, "offcuts": offcuts})
        for q in placed:
            fills[q["name"]] = round(used / board_area, 4)
    return {"boards": boards, "n_boards": len(boards), "lower_bound": lower, "net_area": net,
            "waste": 1.0 - net / (len(boards) * board_area) if boards else 0.0, "heuristic": heuristic,
            "oversize": oversize, "capped": capped, "n_pieces": len(usable_pieces), "board": [stock, width],
            "kerf": kerf, "rotate": False, "trim": trim, "deep": bool(deep), "strategies": tried, "emptied": 0,
            "work": work.n, "fills": fills, "kind": "plank",
            "friendly": {"across": [width], "along": friendly_sizes(stock, stock, kerf, trim)["along"]}}


def wall_order(boards):
    """Put each board's pieces in wall order, left to right then bottom up, and the boards
    in the order of their first piece: the order a fitter works along the elevation."""
    at = lambda q: (round(q.get("u", 0.0)), round(q.get("v", 0.0)), q["name"])  # noqa: E731
    for b in boards:
        b["pieces"].sort(key=at)
    boards.sort(key=lambda b: at(b["pieces"][0]) if b["pieces"] else (math.inf, math.inf, ""))
    return boards


def chain_plan(geometry, p, elevations, clad_area, deep=False):
    """The cutting plan for the given elevations' panels (one chain, or the whole job),
    with their clad area (m² net of openings). Each elevation is packed on its own, in the
    order given (a chain's elevations in turn round it), so every board is cut for one
    elevation; boards are numbered through the job in wall order. One row per elevation
    carries its own count and waste. Packing the whole job together could pass an offcut
    from one elevation to another: its board count (quick check) is kept as
    whole_job_boards, so the cost of keeping elevations apart is on show."""
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
    boards, rows, fills, oversize, plans = [], [], {}, [], []
    for e in order:
        if not pieces[e]:
            continue
        # The deep search's work is shared out by piece count, so the job stays bounded.
        share = max(DEEP_WORK // 8, DEEP_WORK * len(pieces[e]) // total)
        pl = packer(pieces[e], deep=deep, work_limit=share)
        plans.append(pl)
        for b in wall_order(pl["boards"]):
            b["elevation"] = e
            b["no"] = len(boards) + 1
            boards.append(b)
        fills.update(pl["fills"])
        oversize += pl["oversize"]
        rows.append({"elevation": e, "n_boards": pl["n_boards"], "waste": pl["waste"], "lower_bound": pl["lower_bound"],
                     "net_area": pl["net_area"], "heuristic": pl["heuristic"], "n_pieces": pl["n_pieces"]})
    whole = packer([q for e in order for q in pieces[e]])
    net = sum(r["net_area"] for r in rows)
    board_area = bw * bh
    trim = max(0.0, float(p.get("board_trim", 0.0) or 0.0))
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
            "per_elevation": True, "whole_job_boards": whole["n_boards"], "clad_area": clad_area}


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
        gx, gy = bw + 700.0, bh + 1100.0
        plank = pl.get("kind") == "plank"
        dxf.add_text("%s  -  %s, lower bound %d, waste %.1f%%, kerf %.0f%s%s%s" % (
            chain, ("%d length(s) of %.0f, %.0f wide" if plank else "%d board(s) of %.0f x %.0f") % (pl["n_boards"], bw, bh),
            pl["lower_bound"], 100.0 * pl["waste"], pl["kerf"],
            (", end trim %.0f" if plank else ", edge trim %.0f") % pl["trim"] if pl.get("trim") else "",
            ", rotation allowed" if pl["rotate"] else "",
            ("  -  %d piece(s) not packed (cap %d)" % (pl["capped"], MAX_PIECES)) if pl["capped"] else ""),
            (0.0, y), 70.0, "NOTES")
        if pl.get("per_elevation"):
            note = "Each elevation packed on its own: every %s is cut for one elevation." % ("length" if plank else "board")
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
                dxf.add_text("%s  -  %d %s, waste %.1f%%" % (row["elevation"], row["n_boards"],
                             "length(s)" if plank else "board(s)", 100.0 * row["waste"]), (0.0, ry + 60.0), 80.0, "NOTES")
                ry -= 250.0
            if plank:                              # lengths stacked down the row, like a cutting list
                for i, b in enumerate(boards):
                    _draw_board(dxf, pl, b, b.get("no", i + 1), 0.0, ry - bh - i * (bh + 260.0))
                ry -= len(boards) * (bh + 260.0) + 500.0
            else:                                  # boards left to right
                for i, b in enumerate(boards):
                    _draw_board(dxf, pl, b, b.get("no", i + 1), i * gx, ry - bh)
                ry -= gy
        y = ry - 600.0
    return dxf.to_string()


def _draw_board(dxf, pl, b, no, ox, oy):
    bw, bh = pl["board"]
    plank = pl.get("kind") == "plank"
    dxf.add_ring([(0, 0), (bw, 0), (bw, bh), (0, bh)], "BOARD", ox, oy)
    t = pl.get("trim") or 0.0
    if t and plank:                        # a length's squared-up ends, cut off first
        for x0, x1 in ((0, t), (bw - t, bw)):
            dxf.add_ring([(x0, 0), (x1, 0), (x1, bh), (x0, bh)], "OFFCUT", ox, oy)
            _hatch(dxf, ox + x0, oy, ox + x1, oy + bh, step=max(t, 20.0))
    elif t:                                # the squared-up edges, cut off first
        dxf.add_ring([(t, t), (bw - t, t), (bw - t, bh - t), (t, bh - t)], "OFFCUT", ox, oy)
        for x0, y0, x1, y1 in ((0, 0, bw, t), (0, bh - t, bw, bh), (0, t, t, bh - t), (bw - t, t, bw, bh - t)):
            _hatch(dxf, ox + x0, oy + y0, ox + x1, oy + y1, step=max(t, 20.0))
    if plank:
        dxf.add_text("Length %d  -  waste %.1f%%" % (no, 100.0 * b["waste"]), (ox - 1500.0, oy + bh / 2.0 - 30.0), 60.0, "NOTES")
    else:
        dxf.add_text("Board %d  -  waste %.1f%%" % (no, 100.0 * b["waste"]), (ox, oy + bh + 60.0), 60.0, "NOTES")
    for q in b["pieces"]:
        dxf.add_ring([(q["x"], q["y"]), (q["x"] + q["w"], q["y"]), (q["x"] + q["w"], q["y"] + q["h"]),
                      (q["x"], q["y"] + q["h"])], "PIECE", ox, oy)
        if plank:
            label = "%s  %.0f%s" % (q["name"], q["cut_w"], ", ripped to %.0f" % q["cut_h"] if q.get("ripped") else "")
            dxf.add_text(label, (ox + q["x"] + 20.0, oy + q["y"] + min(q["h"], bh) / 2.0 - 15.0), min(35.0, 0.4 * bh), "PIECE")
            continue
        label = "%s  %.0f x %.0f%s" % (q["name"], q["cut_w"], q["cut_h"], " (rotated)" if q["rotated"] else "")
        dxf.add_text(label, (ox + q["x"] + 30.0, oy + q["y"] + q["h"] / 2.0), 40.0, "PIECE")
    for x, yy, w, h in b["offcuts"]:
        dxf.add_ring([(x, yy), (x + w, yy), (x + w, yy + h), (x, yy + h)], "OFFCUT", ox, oy)
        _hatch(dxf, ox + x, oy + yy, ox + x + w, oy + yy + h)
