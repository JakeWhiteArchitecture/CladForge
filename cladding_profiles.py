"""CladForge — custom plank profiles: a plank's real cross-section.

A custom profile is a fixed plank, given as its section: a closed ring in (w, y), w
across the plank's width from its leading edge (0) and y through its thickness from the
back (0) to the face. It is set out like a lapped board: the plank is the section's full
width, laid at the profile's cover (the spacing between one plank and the next), so each
laps the one before by width − cover. Horizontal planks run w up the wall, vertical ones
along it.

The engine keeps every plank a plain board (its outline on the wall, the full thickness),
so set-out, trimming, openings, corners, cutting lists and checks all work as for boards.
For the finished build each plank also carries "parts": its section as stacked slabs of
the board, each the plank's outline cut to the strip of width the section fills at that
depth, which the viewer draws; the IFC sweeps the section itself, one solid a plank
(ifc_generator._section_solid). A slab whose side slopes through
the depth carries the slope as a vertex shift ("vshift": v += c + d·s), like a board
mitred to a lining. While a slider is dragged the preview skips this and shows the plain
board.

Profiles come from the library here (Cedral Click, from the drawing of three planks at
their standard spacing, scaled to Marley's 186 x 12), or from a DXF drawn the same way: two or more copies
of the section as closed polylines, at the spacing they are laid at (profile_from_dxf).
"""

import math

from shapely.geometry import LineString, Polygon, box
from shapely.geometry.polygon import orient

from cladding_booleans import iter_polygons

# Cedral Click (Marley): three planks drawn at their standard spacing, scaled to Marley's
# published size, 186 wide and 12 thick (3600 x 186 x 12; the drawing measured 188.14 x
# 12.03, its spacing 174.03). Scaled with it the spacing is 172.06: 1.61 planks per m²
# against Marley's 1.60. The face is the 12 side; the next plank's face covers the lip.
_SX, _SY = 186.0 / 188.136, 12.0 / 12.034
LIBRARY = {
    "cedral_click": {
        "name": "Cedral Click",
        "ring": [(round(w * _SX, 3), round(y * _SY, 3)) for w, y in
                 [(188.136, 0.0), (24.068, 0.0), (20.0, 7.034), (0.0, 7.034), (0.0, 12.034),
                  (173.136, 12.034), (173.136, 7.034), (170.169, 7.034), (170.169, 5.085), (188.136, 5.085)]],
        "cover": round(174.034 * _SX, 3),
    },
}
DEFAULT = "cedral_click"


def section(spec):
    """{"name", "ring", "cover", "width", "thickness"} for a library key or a given
    {"name", "ring", "cover"}, the ring anticlockwise with w and y from 0; None if it
    will not do."""
    if isinstance(spec, str):
        spec = LIBRARY.get(spec)
    if not isinstance(spec, dict) or not spec.get("ring"):
        return None
    try:
        ring = [(float(w), float(y)) for w, y in spec["ring"]]
        pg = orient(Polygon(ring), 1.0)
    except (TypeError, ValueError):
        return None
    if not pg.is_valid or pg.area < 1.0:
        return None
    w0, y0, w1, y1 = pg.bounds
    ring = [(round(w - w0, 3), round(y - y0, 3)) for w, y in list(pg.exterior.coords)[:-1]]
    width, thickness = round(w1 - w0, 3), round(y1 - y0, 3)
    try:
        cover = float(spec.get("cover") or width)
    except (TypeError, ValueError):
        cover = width
    cover = min(width, max(10.0, cover))
    return {"name": str(spec.get("name") or "Custom profile")[:60], "ring": ring, "cover": round(cover, 3),
            "width": width, "thickness": thickness}


def slabs(sec):
    """The section as slabs through its thickness: [(y0, y1, [(a0, b0, a1, b1)])], each
    strip of width the section fills between y0 and y1, its sides at a0, b0 at y0 and
    a1, b1 at y1 (straight between)."""
    ring = sec["ring"]
    pg = Polygon(ring)
    ys = sorted({round(y, 6) for _w, y in ring})
    edges = list(zip(ring, ring[1:] + ring[:1]))
    out = []
    for y0, y1 in zip(ys, ys[1:]):
        if y1 - y0 < 1e-3:
            continue
        ym = (y0 + y1) / 2
        cut = pg.intersection(LineString([(-1.0, ym), (sec["width"] + 1.0, ym)]))
        spans = sorted((min(c[0] for c in g.coords), max(c[0] for c in g.coords))
                       for g in getattr(cut, "geoms", [cut]) if g.geom_type == "LineString" and g.length > 1e-6)
        strips = []
        for a, b in spans:
            ends = []
            for w in (a, b):
                for (pw, py), (qw, qy) in edges:          # the edge this side lies on, here
                    if min(py, qy) <= ym <= max(py, qy) and abs(qy - py) > 1e-9:
                        at = pw + (qw - pw) * (ym - py) / (qy - py)
                        if abs(at - w) < 1e-6:
                            slope = (qw - pw) / (qy - py)
                            ends.append((w + slope * (y0 - ym), w + slope * (y1 - ym)))
                            break
                else:
                    ends.append((w, w))
            (a0, a1), (b0, b1) = ends
            strips.append((a0, b0, a1, b1))
        out.append((y0, y1, strips))
    return out


def _edge_shift(rings, shifts, pt, tol=0.01):
    """The vertex shift at *pt*, on an edge of the plank's rings: straight between the
    shifts of that edge's ends. Zero if no ring carries a shift or *pt* is on none."""
    if not shifts:
        return (0.0, 0.0, 0.0, 0.0)
    for ring, sh in zip(rings, shifts):
        if not sh:
            continue
        n = len(ring)
        for i in range(n):
            p, q = ring[i], ring[(i + 1) % n]
            dx, dy = q[0] - p[0], q[1] - p[1]
            L2 = dx * dx + dy * dy
            if L2 < 1e-12:
                continue
            t = ((pt[0] - p[0]) * dx + (pt[1] - p[1]) * dy) / L2
            if -1e-6 <= t <= 1 + 1e-6 and math.hypot(p[0] + t * dx - pt[0], p[1] + t * dy - pt[1]) < tol:
                a, b = sh[i], sh[(i + 1) % n]
                return tuple(x + (y - x) * t for x, y in zip(a, b))
    return (0.0, 0.0, 0.0, 0.0)


def plank_parts(m, sec):
    """The parts of plank mesh *m* (its outline, depth and corner, any vshift to a
    lining kept) with *sec*'s section: m["section_at"] is where the section's w = 0 lies
    along m["section_axis"] ("v" for horizontal planks, "u" for vertical)."""
    axis = 1 if m.get("section_axis", "v") == "v" else 0
    at = float(m.get("section_at", 0.0))
    rings = [list(map(tuple, m["profile"]))] + [list(map(tuple, h)) for h in (m.get("holes") or [])]
    try:
        outline = Polygon(rings[0], rings[1:])
        outline = outline if outline.is_valid else outline.buffer(0)
    except Exception:   # noqa: BLE001 — a degenerate outline keeps its plain board
        return []
    shifts = m.get("vshift")
    lo, hi = outline.bounds[axis], outline.bounds[axis + 2]
    other = (outline.bounds[1 - axis] - 1.0, outline.bounds[3 - axis] + 1.0)
    parts = []
    for y0, y1, strips in slabs(sec):
        s0 = float(m["depth"]) + y0
        for a0, b0, a1, b1 in strips:
            wa, wb = at + a0, at + b0
            if wb <= lo + 1e-6 or wa >= hi - 1e-6:
                continue
            strip = box(wa, other[0], wb, other[1]) if axis == 0 else box(other[0], wa, other[1], wb)
            try:
                cut = outline.intersection(strip)
            except Exception:   # noqa: BLE001
                continue
            slope_a, slope_b = (a1 - a0) / (y1 - y0), (b1 - b0) / (y1 - y0)
            for pg in iter_polygons(cut):
                if pg.area < 0.5:
                    continue
                pg = orient(pg, 1.0)
                prings = [list(pg.exterior.coords)[:-1]] + [list(r.coords)[:-1] for r in pg.interiors]
                vsh, any_shift = [], False
                for r in prings:
                    row = []
                    for q in r:
                        a, b, c, d = _edge_shift(rings, shifts, q)
                        # a side of the strip that slopes through the slab moves with depth
                        for w, slope in ((wa, slope_a), (wb, slope_b)):
                            if slope and abs(q[axis] - w) < 1e-6:
                                if axis == 1:
                                    c, d = c - slope * s0, d + slope
                                else:
                                    a, b = a - slope * s0, b + slope
                        row.append([round(a, 6), round(b, 6), round(c, 6), round(d, 6)])
                        any_shift = any_shift or any(row[-1])
                    vsh.append(row)
                part = {"profile": [[round(u, 4), round(v, 4)] for u, v in prings[0]],
                        "holes": [[[round(u, 4), round(v, 4)] for u, v in r] for r in prings[1:]],
                        "depth": round(s0, 4), "thickness": round(y1 - y0, 4)}
                if any_shift:
                    part["vshift"] = vsh
                if m.get("corner"):
                    part["corner"] = m["corner"]
                parts.append(part)
    return parts


def profile_planks(meshes, p):
    """Give every plank of a custom profile its section as parts (the finished build)."""
    sec = p.get("section")
    if not sec:
        return meshes
    for m in meshes:
        if m.get("ifc_type") == "plank" and "section_at" in m:
            parts = plank_parts(m, sec)
            if parts:
                m["parts"] = parts
                m["section_ring"] = [list(q) for q in sec["ring"]]   # the IFC sweeps it: one solid
    return meshes


# ── reading a profile from a DXF ─────────────────────────────────────────

def _dxf_pairs(text):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for i in range(0, len(lines) - 1, 2):
        yield lines[i].strip(), lines[i + 1].strip()


def dxf_polylines(text):
    """The closed polylines (LWPOLYLINE, or POLYLINE with VERTEXes) in DXF *text*, as
    lists of (x, y). Arcs (bulges) are taken as straight."""
    out, cur, closed, kind, pending_x = [], None, False, None, None

    def done():
        if cur and len(cur) >= 3 and (closed or math.dist(cur[0], cur[-1]) < 1e-6):
            ring = cur[:-1] if math.dist(cur[0], cur[-1]) < 1e-6 else cur
            if len(ring) >= 3:
                out.append(ring)

    in_entities = False
    for code, value in _dxf_pairs(text):
        if code == "2" and value in ("ENTITIES", "BLOCKS"):
            in_entities = True
        if not in_entities:
            continue
        if code == "0":
            if kind == "LWPOLYLINE" or (kind == "POLYLINE" and value in ("SEQEND",)):
                done()
                cur, kind = None, None
            if value == "LWPOLYLINE":
                cur, closed, kind = [], False, "LWPOLYLINE"
            elif value == "POLYLINE":
                cur, closed, kind = [], False, "POLYLINE"
            elif value == "VERTEX" and kind == "POLYLINE":
                pass
            elif kind not in ("POLYLINE",):
                cur, kind = None, None
            continue
        if kind is None:
            continue
        if code == "70" and cur is not None and (kind == "LWPOLYLINE" or not cur):
            try:
                closed = closed or bool(int(value) & 1)
            except ValueError:
                pass
        elif code == "10":
            pending_x = float(value)
        elif code == "20" and pending_x is not None:
            cur.append((pending_x, float(value)))
            pending_x = None
    if kind == "LWPOLYLINE":
        done()
    return out


def profile_from_dxf(text, name="Imported profile"):
    """A custom profile from a DXF drawn like the Cedral Click one: the section as a
    closed polyline, two or more copies at the spacing they are laid at. The spacing is
    the cover; the copies may run along x or along y. The face is the side of the section
    where more of its outline lies on the outermost line (a lip or tongue is at the back).
    One copy alone covers its own width (no lap). Returns (section, message)."""
    rings = dxf_polylines(text)
    if not rings:
        return None, "No closed polylines in the drawing: draw the section as a closed polyline."
    pgs = []
    for r in rings:
        try:
            pg = Polygon(r)
            if pg.is_valid and pg.area > 1.0:
                pgs.append(pg)
        except Exception:   # noqa: BLE001
            continue
    if not pgs:
        return None, "None of the polylines is a usable closed outline."
    # the copies: the same size as the first one
    ref = pgs[0]
    same = [g for g in pgs if abs(g.area - ref.area) < 0.01 * ref.area]
    xs = sorted(g.bounds[0] for g in same)
    ys = sorted(g.bounds[1] for g in same)
    along_y = len(same) > 1 and (ys[-1] - ys[0]) > (xs[-1] - xs[0])
    def flip(pts):
        return [(y, x) for x, y in pts] if along_y else list(pts)
    first = min(same, key=lambda g: g.bounds[1] if along_y else g.bounds[0])
    ring = flip(list(first.exterior.coords)[:-1])
    w0 = min(w for w, _y in ring)
    y0, y1 = min(y for _w, y in ring), max(y for _w, y in ring)
    starts = sorted((g.bounds[1] if along_y else g.bounds[0]) for g in same)
    steps = [b - a for a, b in zip(starts, starts[1:]) if b - a > 1.0]
    width = max(w for w, _y in ring) - w0
    cover = min(steps) if steps else width
    # the face: the outermost line carrying more of the outline
    def on_line(y):
        return sum(math.dist(p, q) for p, q in zip(ring, ring[1:] + ring[:1])
                   if abs(p[1] - y) < 1e-3 and abs(q[1] - y) < 1e-3)
    if on_line(y0) > on_line(y1):
        ring = [(w, y0 + y1 - y) for w, y in ring]        # face at the high side
    sec = section({"name": name, "ring": [(w - w0, y - y0) for w, y in ring], "cover": cover})
    if not sec:
        return None, "The section could not be read."
    msg = "%s: %.1f wide, %.1f thick, at %.1f centres%s" % (
        sec["name"], sec["width"], sec["thickness"], sec["cover"],
        "" if steps else " (one copy only: draw two or more at their spacing to set the cover)")
    return sec, msg
