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
         ("height", lambda p: p["h"]), ("width", lambda p: p["w"]))


def _baf(fw, fh, w, h):      # best area fit, then the short side
    return (fw * fh - w * h, min(fw - w, fh - h))


def _bssf(fw, fh, w, h):     # best short side fit, then the long side
    return (min(fw - w, fh - h), max(fw - w, fh - h))


PLACEMENTS = (("best area fit", _baf), ("best short side fit", _bssf))
SPLITS = ("shorter leftover axis", "longer leftover axis")


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


def _pack(pieces, board_w, board_h, kerf, rotate, sort_key, score, split):
    """One heuristic run. Returns the boards: {"pieces": [...], "free": [(x, y, w, h)]}."""
    BW, BH = board_w + kerf, board_h + kerf        # the kerf goes between pieces only
    boards = []
    order = sorted(pieces, key=lambda p: (-sort_key(p), p["name"]))
    # A free rectangle no piece could ever fit is an offcut, not somewhere to look.
    short = min((min(p["w"], p["h"]) + kerf for p in pieces), default=0.0)
    long_ = min((max(p["w"], p["h"]) + kerf for p in pieces), default=0.0)
    useful = lambda q: min(q[2], q[3]) + EPS >= short and max(q[2], q[3]) + EPS >= long_  # noqa: E731
    for p in order:
        w, h = p["w"] + kerf, p["h"] + kerf
        orients = [(w, h, False)] + ([(h, w, True)] if rotate and abs(w - h) > EPS else [])
        best = None
        for bi, b in enumerate(boards):
            for fi, (fx, fy, fw, fh) in enumerate(b["free"]):
                for ow, oh, rot in orients:
                    if ow <= fw + EPS and oh <= fh + EPS:
                        key = (score(fw, fh, ow, oh), bi, fi, rot)
                        if best is None or key < best[0]:
                            best = (key, bi, fi, ow, oh, rot)
        if best is None:
            fits = [(ow, oh, rot) for ow, oh, rot in orients if ow <= BW + EPS and oh <= BH + EPS]
            if not fits:
                continue                                   # oversize: reported by the caller
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
        horizontal = (rw < rh) if split == SPLITS[0] else (rw >= rh)
        if horizontal:
            parts = [(fx + ow, fy, rw, oh), (fx, fy + oh, fw, rh)]
        else:
            parts = [(fx + ow, fy, rw, fh), (fx, fy + oh, ow, rh)]
        for q in parts:
            if q[2] > EPS and q[3] > EPS:
                (b["free"] if useful(q) else b["scrap"]).append(q)
    return boards


def pack(pieces, board_w, board_h, kerf=3.0, rotate=True):
    """The best packing over every heuristic: fewest boards, then least waste, then the
    first heuristic in order. Deterministic: the same pieces give the same layout."""
    fits = lambda p: ((p["w"] <= board_w + EPS and p["h"] <= board_h + EPS) or  # noqa: E731
                      (rotate and p["w"] <= board_h + EPS and p["h"] <= board_w + EPS))
    oversize = [p["name"] for p in pieces if not fits(p)]
    usable = sorted((p for p in pieces if fits(p)), key=lambda p: p["name"])
    capped = max(0, len(usable) - MAX_PIECES)
    usable = usable[:MAX_PIECES]
    net = sum(p["area"] for p in usable)
    board_area = board_w * board_h
    lower = int(math.ceil(net / board_area - 1e-9)) if net > 0 else 0
    best = None
    for sort_name, sort_key in SORTS:
        for place_name, score in PLACEMENTS:
            for split in SPLITS:
                boards = _pack(usable, board_w, board_h, kerf, rotate, sort_key, score, split)
                waste = 1.0 - net / (len(boards) * board_area) if boards else 0.0
                if best is None or (len(boards), round(waste, 9)) < (len(best[0]), round(best[1], 9)):
                    best = (boards, waste, "%s, %s, split on the %s" % (sort_name, place_name, split))
                if best and len(best[0]) <= lower:
                    break
            if best and len(best[0]) <= lower:
                break
        if best and len(best[0]) <= lower:
            break
    boards, waste, heuristic = best if best else ([], 0.0, "")
    for b in boards:
        used = sum(q["area"] for q in b["pieces"])
        b["waste"] = 1.0 - used / board_area
        b["offcuts"] = [(x, y, min(w, board_w - x), min(h, board_h - y)) for x, y, w, h in b.pop("free") + b.pop("scrap")
                        if min(w, board_w - x) > 1.0 and min(h, board_h - y) > 1.0]
    return {"boards": boards, "n_boards": len(boards), "lower_bound": lower, "net_area": net,
            "waste": waste, "heuristic": heuristic, "oversize": oversize, "capped": capped,
            "n_pieces": len(usable), "board": [board_w, board_h], "kerf": kerf, "rotate": bool(rotate)}


def chain_plan(geometry, p, elevations, clad_area):
    """The packing for one chain's panels, with its clad area (m² net of openings)."""
    plan = pack(chain_pieces(geometry, set(elevations)), p["board_w"], p["board_h"], p["kerf"], p["rotate"])
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
        dxf.add_text("%s  -  %d board(s) of %.0f x %.0f, lower bound %d, waste %.1f%%, kerf %.0f%s%s" % (
            chain, pl["n_boards"], bw, bh, pl["lower_bound"], 100.0 * pl["waste"], pl["kerf"],
            ", rotation allowed" if pl["rotate"] else "",
            ("  -  %d piece(s) not packed (cap %d)" % (pl["capped"], MAX_PIECES)) if pl["capped"] else ""),
            (0.0, y), 70.0, "NOTES")
        if pl["oversize"]:
            dxf.add_text("Larger than the board either way round: " + ", ".join(pl["oversize"]), (0.0, y - 110.0), 50.0, "NOTES")
        top = y - 250.0
        for i, b in enumerate(pl["boards"]):
            ox, oy = (i % per_row) * gx, top - bh - (i // per_row) * gy
            dxf.add_ring([(0, 0), (bw, 0), (bw, bh), (0, bh)], "BOARD", ox, oy)
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
