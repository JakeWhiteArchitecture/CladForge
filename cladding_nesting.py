"""CladForge — packing a chain's panels onto stock boards, and the cutting plan.

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


def chain_pieces(geometry, elevations):
    """[{"name", "w", "h", "area"}] for every panel of the given elevations. w × h is the
    piece's bounding rectangle over both its faces (a mitred or lapped end runs further
    on one face than the other); area is the net area of its outer face."""
    out = []
    for m in geometry:
        if m.get("ifc_type") != "panel" or m.get("elevation") not in elevations:
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
                    "h": round(max(vs) - min(vs), 1), "area": area})
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
                        "cut_w": p["w"], "cut_h": p["h"], "rotated": rot, "area": p["area"]})
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
            moving = sorted(({"name": q["name"], "w": q["cut_w"], "h": q["cut_h"], "area": q["area"]}
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


def pack(pieces, board_w, board_h, kerf=3.0, rotate=True, trim=0.0, deep=False):
    """The best packing: fewest boards, then least waste, then the first strategy in
    order. *trim* is cut off every edge of the board first (factory edges), so pieces
    come from the board less 2 × trim each way. The quick check runs 16 strategies; *deep*
    runs every one, then shuffled piece orders, then tries to empty a board. Deterministic:
    the same pieces give the same layout."""
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
        if len(best[0]) <= lower or (deep and tried >= len(QUICK) and work.n > DEEP_WORK):
            break
    if deep and best and len(best[0]) > lower and work.n <= DEEP_WORK:
        # Shuffled orders under the rules that did best, while the work allowance lasts.
        rules = [(pl, sp) for pl in PLACEMENTS for sp in SPLITS]
        score_of = lambda r: min(x[0] for x in ranked if x[1] == r[0][0] and x[2] == r[1][0])  # noqa: E731
        top = sorted(rules, key=score_of)[:3]                     # stable: ties keep the list order
        for k, order in enumerate(_shuffled(usable, 10_000, SHUFFLE_SEED)):
            if work.n > DEEP_WORK or len(best[0]) <= lower:
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


def chain_plan(geometry, p, elevations, clad_area, deep=False):
    """The packing for the given elevations' panels (one chain, or the whole job), with
    their clad area (m² net of openings)."""
    plan = pack(chain_pieces(geometry, set(elevations)), p["board_w"], p["board_h"], p["kerf"], p["rotate"],
                p.get("board_trim", 0.0), deep)
    plan["clad_area"] = clad_area
    return plan


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
    """A DXF of every chain's cutting plan: one rectangle per board, to scale, in a grid,
    each piece in place with its name and cut size, rotated pieces marked, offcuts
    hatched, the waste per board and in total. *plans* is [(chain name, plan)]."""
    from dxf_generator import _DxfWriter
    dxf = _DxfWriter()
    for name, color in (("BOARD", 7), ("PIECE", 5), ("OFFCUT", 8), ("NOTES", 7)):
        dxf.add_layer(name, color)
    per_row, y = 4, 0.0
    total_boards = sum(pl["n_boards"] for _n, pl in plans)
    total_net = sum(pl["net_area"] for _n, pl in plans)
    total_area = sum(pl["n_boards"] * pl["board"][0] * pl["board"][1] for _n, pl in plans)
    dxf.add_text(PLAN_NOTE, (0.0, 600.0), 90.0, "NOTES")
    dxf.add_text("Boards %d  -  total waste %.1f%%" % (total_boards, 100.0 * (1 - total_net / total_area) if total_area else 0.0),
                 (0.0, 420.0), 70.0, "NOTES")
    for chain, pl in plans:
        bw, bh = pl["board"]
        gx, gy = bw + 700.0, bh + 900.0
        dxf.add_text("%s  -  %d board(s) of %.0f x %.0f, lower bound %d, waste %.1f%%, kerf %.0f%s%s%s" % (
            chain, pl["n_boards"], bw, bh, pl["lower_bound"], 100.0 * pl["waste"], pl["kerf"],
            ", edge trim %.0f" % pl["trim"] if pl.get("trim") else "",
            ", rotation allowed" if pl["rotate"] else "",
            ("  -  %d piece(s) not packed (cap %d)" % (pl["capped"], MAX_PIECES)) if pl["capped"] else ""),
            (0.0, y), 70.0, "NOTES")
        dxf.add_text("Best of %d strategies: %s" % (pl.get("strategies", 0), pl.get("heuristic", "")),
                     (0.0, y - 200.0), 45.0, "NOTES")
        if pl["oversize"]:
            dxf.add_text("Larger than the board either way round: " + ", ".join(pl["oversize"]), (0.0, y - 110.0), 50.0, "NOTES")
        top = y - 350.0
        for i, b in enumerate(pl["boards"]):
            ox, oy = (i % per_row) * gx, top - bh - (i // per_row) * gy
            dxf.add_ring([(0, 0), (bw, 0), (bw, bh), (0, bh)], "BOARD", ox, oy)
            t = pl.get("trim") or 0.0
            if t:                                  # the squared-up edges, cut off first
                dxf.add_ring([(t, t), (bw - t, t), (bw - t, bh - t), (t, bh - t)], "OFFCUT", ox, oy)
                for x0, y0, x1, y1 in ((0, 0, bw, t), (0, bh - t, bw, bh), (0, t, t, bh - t), (bw - t, t, bw, bh - t)):
                    _hatch(dxf, ox + x0, oy + y0, ox + x1, oy + y1, step=max(t, 20.0))
            dxf.add_text("Board %d  -  waste %.1f%%" % (i + 1, 100.0 * b["waste"]), (ox, oy + bh + 60.0), 60.0, "NOTES")
            for q in b["pieces"]:
                dxf.add_ring([(q["x"], q["y"]), (q["x"] + q["w"], q["y"]), (q["x"] + q["w"], q["y"] + q["h"]),
                              (q["x"], q["y"] + q["h"])], "PIECE", ox, oy)
                label = "%s  %.0f x %.0f%s" % (q["name"], q["cut_w"], q["cut_h"], " (rotated)" if q["rotated"] else "")
                dxf.add_text(label, (ox + q["x"] + 30.0, oy + q["y"] + q["h"] / 2.0), 40.0, "PIECE")
            for x, yy, w, h in b["offcuts"]:
                dxf.add_ring([(x, yy), (x + w, yy), (x + w, yy + h), (x, yy + h)], "OFFCUT", ox, oy)
                _hatch(dxf, ox + x, oy + yy, ox + x + w, oy + yy + h)
        rows = max(1, int(math.ceil(len(pl["boards"]) / float(per_row))))
        y = top - rows * gy - 600.0
    return dxf.to_string()
