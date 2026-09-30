"""CladForge — the edges of the cladding, and what happens at each.

Every boundary edge of the clad region is one of six kinds, each with its own colour in
the 2D view and its own treatment:

    corner  orange  a chain corner                  no offset; a mitre opens the mitre gap
    jamb    orange  a window or door side           no offset; the board is mitred to the
                                                    reveal lining, across the mitre gap
    top     purple  the top of the cladding, sloped or level, and under a cill
    side    green   a free end of a run
    bottom  blue    the bottom, and the top of a splash zone over a roof or slab
    head    red     a window or door head           lined: see the head lining

An offset pulls the whole trimmable buildup (battens, counter-battens, noggins and
boards) back from its edges. Every value is set per chain.
"""

import math

from shapely.geometry import LineString
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from cladding_primitives import clip_bounds, openings

EDGE_DEFAULTS = {"top": 10.0, "side": 0.0, "bottom": 10.0}
MITRE_GAP = 10.0        # between two mitred boards, measured straight across the joint
PROFILE_GAP = 1.0       # a board stops this far short of a corner profile's nose
FRAME_SETBACK = 50.0    # a window or door frame's outer face, behind the wall face, when not found
FRAME_REACH = 600.0     # with no wall depth known: how far behind the wall face a frame may be
HEAD_AIR = 10.0         # vented at the back: air over the head lining, and its gap off the frame
MAX_EDGE = 100.0
TOL = 0.6
KINDS = ("corner", "jamb", "top", "side", "bottom", "head")


def settings(elev):
    """The chain's edge settings, as carried on each of its elevation records."""
    def num(key, default):
        try:
            v = elev.get(key)
            return min(MAX_EDGE, max(0.0, float(default if v is None else v)))
        except (TypeError, ValueError):
            return default
    return {"top": num("edge_top", EDGE_DEFAULTS["top"]), "side": num("edge_side", EDGE_DEFAULTS["side"]),
            "bottom": num("edge_bottom", EDGE_DEFAULTS["bottom"]), "gap": num("mitre_gap", MITRE_GAP),
            "vent": "back" if elev.get("head_vent") == "back" else "front", "air": num("head_air", HEAD_AIR),
            "pgap": num("profile_gap", PROFILE_GAP)}


def mitre_pullback(gap, k):
    """How far each of two mitred boards is pulled back along its own length so that the
    joint between them is *gap* straight across. The cut plane runs at u - k·s, so moving
    it by x along u moves it x / sqrt(1 + k²) square to itself; each board takes half."""
    return gap / 2.0 * math.sqrt(1.0 + k * k)


def _polygons(geom):
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    return [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon"]


def _on_jamb(a, b, holes):
    for u0, u1, v0, v1 in holes:
        for u in (u0, u1):
            if abs(a[0] - u) < TOL and abs(b[0] - u) < TOL and \
                    min(a[1], b[1]) >= v0 - TOL and max(a[1], b[1]) <= v1 + TOL:
                return True
    return False


def _on_head(a, b, holes):
    return any(abs(a[1] - v1) < TOL and abs(b[1] - v1) < TOL and
               min(a[0], b[0]) >= u0 - TOL and max(a[0], b[0]) <= u1 + TOL for u0, u1, _v0, v1 in holes)


def _kind(a, b, corners, holes):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return None
    ny = -dx / length                      # outward normal (dy, -dx): rings are CCW outside, CW holes
    if abs(ny) < 0.3:
        if abs(dx) < TOL and any(abs(a[0] - c) < TOL for c in corners):
            return "corner"
        return "jamb" if _on_jamb(a, b, holes) else "side"
    if ny > 0:
        return "top"
    return "head" if _on_head(a, b, holes) else "bottom"


def classify_edges(elev, region):
    """[{"kind", "p1", "p2"}] for the boundary of *region*, the clad area of the face.
    A hole that is not a window or door (a pipe penetration) is left out: it has no edge
    treatment of its own."""
    lo, hi = clip_bounds(elev)
    k = (elev.get("corner_lo"), elev.get("corner_hi"))            # run order
    k_left, k_right = k[::-1] if elev.get("chain_reversed") else k
    corners = [u for u, kk in ((lo, k_left), (hi, k_right)) if kk]
    holes = openings(elev)
    out = []
    for pg in _polygons(region):
        pg = orient(pg, 1.0)
        rings = [list(pg.exterior.coords)[:-1]]
        for r in pg.interiors:
            u0, v0, u1, v1 = r.bounds
            if any(abs(u0 - h[0]) < 2 and abs(u1 - h[1]) < 2 and abs(v0 - h[2]) < 2 and abs(v1 - h[3]) < 2
                   for h in holes):
                rings.append(list(r.coords)[:-1])
        for pts in rings:
            for a, b in zip(pts, pts[1:] + pts[:1]):
                kind = _kind(a, b, corners, holes)
                if kind:
                    out.append({"kind": kind, "p1": [round(a[0], 2), round(a[1], 2)],
                                "p2": [round(b[0], 2), round(b[1], 2)]})
    return out


def offset_region(elev, region, edges=None):
    """*region* with a strip taken off along every top, side and bottom edge, as wide as
    that edge's offset, so everything trimmed to it stands back from the edge."""
    s = settings(elev)
    cuts = []
    for e in edges if edges is not None else classify_edges(elev, region):
        w = s.get(e["kind"], 0.0) if e["kind"] in EDGE_DEFAULTS else 0.0
        if w > 0:
            try:
                cuts.append(LineString([e["p1"], e["p2"]]).buffer(w, cap_style=2))
            except Exception:   # noqa: BLE001 — a degenerate edge just goes without
                continue
    if not cuts:
        return region
    try:
        cut = region.difference(unary_union(cuts))
        return cut if not cut.is_empty else region
    except Exception:   # noqa: BLE001 — GEOS trouble leaves the region as it was
        return region


# ── the corner detail at each opening ────────────────────────────────────

DETAILS = ("mitre", "lap", "profile", "butt")      # Mitred, Master, Profile, Square
OPENING_DEFAULT = {"jamb": "mitre", "jamb_master": "face", "head": "mitre", "head_master": "face"}


def opening_key(rect):
    """How a window or door is known between rebuilds: its structural corner."""
    return "%.0f,%.0f" % (rect[0], rect[2])


def opening_details(elev, p):
    """[(rect, detail)] for every window and door, detail {"jamb", "jamb_master", "head",
    "head_master"} as chosen on the elevation (elev["opening_details"], by opening_key)
    or mitred by default. Both jambs of an opening share one detail and one arrangement,
    mirrored; the head has its own. Master and Profile are panel details: with planks
    the corner stays mitred."""
    chosen = elev.get("opening_details") or {}
    panel = p.get("cladding_type") == "panel"
    out = []
    for rect in openings(elev):
        d = dict(OPENING_DEFAULT, **(chosen.get(opening_key(rect)) or {}))
        for part in ("jamb", "head"):
            if d[part] not in DETAILS or (d[part] in ("lap", "profile") and not panel):
                d[part] = "mitre"
            if d[part + "_master"] not in ("face", "reveal"):
                d[part + "_master"] = "face"
        out.append((rect, d))
    return out


def opening_frames(elev, holes, face):
    """{rect: {"depth", "from", "name", "note"}}: where each opening's reveal linings run
    back to, the outer face of its window or door frame, as a depth from the wall face
    (out is positive), tight to it.

      set      typed on the opening (opening_details[key]["frame"], mm behind the wall
               face; negative is forward of it): always wins
      model    the window or door the extractor found in the opening: an IfcWindow or
               IfcDoor only, covering most of the opening, and its face within the
               reveal, behind the cladding face and in front of the back of the wall
      default  the chain's frame setback (elev["frame_setback"], 50 mm behind the wall
               face by default), with a note where a frame was found but rejected"""
    chosen = elev.get("opening_details") or {}
    try:
        default = -float(elev.get("frame_setback") if elev.get("frame_setback") is not None else FRAME_SETBACK)
    except (TypeError, ValueError):
        default = -FRAME_SETBACK
    back = float(elev.get("wall_depth") or 0.0) or FRAME_REACH
    out = {}
    for rect in holes:
        u0, u1, v0, v1 = rect
        typed = (chosen.get(opening_key(rect)) or {}).get("frame")
        if isinstance(typed, (int, float)) and not isinstance(typed, bool):
            out[rect] = {"depth": -float(typed), "from": "set", "name": None, "note": None}
            continue
        best, area = None, (u1 - u0) * (v1 - v0)
        for fr in elev.get("frames") or []:
            a0, a1, b0, b1 = fr["rect"]
            ov = max(0.0, min(u1, a1) - max(u0, a0)) * max(0.0, min(v1, b1) - max(v0, b0))
            if ov >= 0.5 * area or ov >= 0.5 * max(1.0, (a1 - a0) * (b1 - b0)):
                if best is None or ov > best[0]:
                    best = (ov, fr)
        note = None
        if best:
            f = float(best[1]["face"])
            if -back - 1.0 <= f < face - 1.0:
                out[rect] = {"depth": f, "from": "model", "name": best[1].get("name"), "note": None}
                continue
            note = "%s found %.0f mm %s the wall face, outside the reveal: the chain's frame position is used" % (
                best[1].get("name") or "a window or door", abs(f), "behind" if f < 0 else "in front of")
        out[rect] = {"depth": default, "from": "default", "name": None, "note": note}
    return out


def reveal_treatment(detail, master, face, pull, board, gap, pgap=0.0):
    """How the face board and the lining meet at a jamb or head: (w, lining).

    *w* = (e, k) is how far the face board's edge moves into the opening at depth s,
    e + k·s. *lining* = (k, ext) is the lining's front end treatment (its u runs inward
    from the cladding face, so its end sits at ext·-1 - k·s), or None where it runs
    flush to the cladding face.
      mitre    both cut on the bisector, the mitre gap straight across the joint
      Master   "face": the face board runs past and covers the lining's edge, and the
               lining stops a panel joint gap short of the board's back;
               "reveal": the lining runs out flush to the face board's outside face,
               and the face board stops a panel joint gap short of the lining
      profile  both stop the profile's nose (D = board thickness) and *pgap* short of
               the arris
      square   both stop square at the opening line (a placeholder)"""
    if detail == "mitre":
        # One cut on the bisector through the outer arris (the lining's opening face at the
        # cladding face) and the inner corner (its back face at the board's back), each
        # side pulled off it by half the mitre gap: the board's edge runs from -pull at its
        # back to board - pull at its face; the lining's front end from board + pull (in
        # from the cladding face) at its back to pull at its opening face.
        return (board - face - pull, 1.0), (1.0, -(board + pull))
    if detail == "lap":
        return ((board, 0.0), (0.0, -(board + gap))) if master == "face" else ((-gap, 0.0), None)
    if detail == "profile":
        return (-pgap, 0.0), (0.0, -(board + pgap))
    return (0.0, 0.0), None


def _opening_shift(a, b, treatments, air):
    """The shift, as (du_e, du_k, dv_e, dv_k) at depth s: u += du_e + du_k·s, v += dv_e +
    dv_k·s, for a board edge a→b lying along an opening's jamb or head, or None.
    *treatments* is [(rect, w_jamb, w_head)] with w = (e, k) as from reveal_treatment.
    Over a head vented at the back the lining is *air* lower, and the board comes down
    to it whatever the detail."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return None
    nx, ny = dy / length, -dx / length
    for (u0, u1, v0, v1), wj, wh in treatments:
        inside_v = min(a[1], b[1]) >= v0 - TOL and max(a[1], b[1]) <= v1 + TOL
        if nx > 0.9 and abs(a[0] - u0) < TOL and abs(b[0] - u0) < TOL and inside_v:
            return (wj[0], wj[1], 0.0, 0.0)                  # board left of the opening
        if nx < -0.9 and abs(a[0] - u1) < TOL and abs(b[0] - u1) < TOL and inside_v:
            return (-wj[0], -wj[1], 0.0, 0.0)                # board right of it: mirrored
        if ny < -0.9 and abs(a[1] - v1) < TOL and abs(b[1] - v1) < TOL and \
                min(a[0], b[0]) >= u0 - TOL and max(a[0], b[0]) <= u1 + TOL:
            return (0.0, 0.0, -(wh[0] + air), -wh[1])        # board over the head
    return None


def _split(ring, holes):
    """The ring with a vertex added wherever an edge along a jamb or head line runs past
    the end of the opening, so only the part beside the opening is mitred."""
    out = []
    for a, b in zip(ring, ring[1:] + ring[:1]):
        out.append(a)
        cuts = []
        for u0, u1, v0, v1 in holes:
            if abs(a[0] - b[0]) < TOL and (abs(a[0] - u0) < TOL or abs(a[0] - u1) < TOL):
                cuts += [(v, 1) for v in (v0, v1)]
            if abs(a[1] - b[1]) < TOL and abs(a[1] - v1) < TOL:
                cuts += [(u, 0) for u in (u0, u1)]
        ts = set()
        for c, axis in cuts:
            span = b[axis] - a[axis]
            if abs(span) > 1e-9:
                t = (c - a[axis]) / span
                if 1e-6 < t < 1 - 1e-6:
                    ts.add(round(t, 9))
        for t in sorted(ts):
            out.append([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t])
    return out


def _ring_shifts(ring, holes, treatments, air):
    """(ring, shifts) with a shift per vertex. A vertex between two mitred edges takes
    both (the corner of an opening). One between a mitred edge and a plain one takes the
    shift where the plain edge runs the way the shift moves it (it just gets longer or
    shorter along itself); otherwise it is doubled, one copy for each edge, so the end
    steps rather than slopes."""
    ring = _split([list(p) for p in ring], holes)
    n = len(ring)
    edge = [_opening_shift(ring[i], ring[(i + 1) % n], treatments, air) for i in range(n)]
    edge = [e if e and any(e) else None for e in edge]      # a square end is no shift at all
    pts, shifts = [], []
    for i in range(n):
        before, after = edge[i - 1], edge[i]
        if before and after:
            same = before == after
            shifts.append(before if same else tuple(x + y for x, y in zip(before, after)))
            pts.append(ring[i])
        elif before or after:
            sh = before or after
            plain = (ring[i - 1], ring[i]) if after else (ring[i], ring[(i + 1) % n])
            along_u = abs(plain[1][1] - plain[0][1]) < TOL          # plain edge horizontal
            along_v = abs(plain[1][0] - plain[0][0]) < TOL          # plain edge vertical
            if (along_u and not (sh[2] or sh[3])) or (along_v and not (sh[0] or sh[1])):
                pts.append(ring[i])
                shifts.append(sh)
            else:
                pts += [ring[i], ring[i]]
                shifts += [before or (0.0, 0.0, 0.0, 0.0), after or (0.0, 0.0, 0.0, 0.0)]
        else:
            pts.append(ring[i])
            shifts.append((0.0, 0.0, 0.0, 0.0))
    return pts, shifts


def mitre_to_linings(meshes, p, infos):
    """Meet every board edge that runs along a window or door jamb or head, holes left
    by trimming included, to the reveal linings with that opening's corner detail
    (mitred by default). Runs on the finished boards, after trimming, and records the
    result as a shift per ring vertex ("vshift")."""
    if not p.get("reveals", True):
        return meshes
    face_by = {i["elevation"]: i.get("total_depth", 0.0) for i in infos}
    board = p["panel_t"] if p.get("cladding_type") == "panel" else p["plank_t"]
    per = {}
    for elev in p.get("elevations", []):
        details = opening_details(elev, p)
        if details:
            s = settings(elev)
            face, pull = face_by.get(elev.get("name", ""), 0.0), mitre_pullback(s["gap"], 1.0)
            treat = [(rect, reveal_treatment(d["jamb"], d["jamb_master"], face, pull, board, p["panel_gap"], s["pgap"])[0],
                      reveal_treatment(d["head"], d["head_master"], face, pull, board, p["panel_gap"], s["pgap"])[0])
                     for rect, d in details]
            per[elev.get("name", "")] = ([r for r, _d in details], treat, s["air"] if s["vent"] == "back" else 0.0)
    for m in meshes:
        if m.get("ifc_type") not in ("panel", "plank") or m.get("elevation") not in per:
            continue
        holes, treat, air = per[m["elevation"]]
        rings = [m["profile"]] + list(m.get("holes") or [])
        done = [_ring_shifts(r, holes, treat, air) for r in rings]
        if not any(any(sh) for _p, shifts in done for sh in shifts):
            continue
        m["profile"] = [[round(u, 3), round(v, 3)] for u, v in done[0][0]]
        m["holes"] = [[[round(u, 3), round(v, 3)] for u, v in r] for r, _s in done[1:]]
        m["vshift"] = [[list(sh) for sh in shifts] for _r, shifts in done]
    return meshes
