"""CladForge — the hip: two roof slopes falling away from each other and meeting on a
sloping line.

After VMZINC's standard detail "Ventilated ridge/hip G3" (VMZ-SN-MA-NO-G3-01), which is
drawn for both: a hip is finished as a ridge is (cladding_ridge), along a line that slopes.
Each face of the pair carries a hip record among its "valleys" (kind "hip":
fabric_extract.hip_link, kept by the app), so the two need not share a chain.

  * The cladding (trays, boards, panels) runs to the plane that bisects the hip and stops
    RIDGE_GAP / 2 short of it, square to the plane: 30 mm clear between the two slopes'
    trays. A standing seam tray is turned up RIDGE_UPSTAND along its raking end.
  * The deck and the battens under it are cut square, RIDGE_OVERSAIL behind the tray's end
    and no nearer the plane than the cladding. Sheathing and insulation meet on the plane.
  * The hip cap: the ridge cap's, RIDGE_LEG down each slope from its apex on the bisecting
    plane, resting on the standing seams (or the cladding face). It is drawn as its two
    legs, a sheet lying on each slope, each laid by its own face, so the two slopes need
    not be built together. Where caps meet on a face they are mitred: two hip caps at
    the head of a hip end, a hip cap and the ridge cap at the end of a ridge (whose cap
    is then two legs as well, and stops there).
  * An end that meets no other cap is the roof's edge, and the chain's to choose
    ("cap_ends"). "edge", the default: the cap runs on past the end of its line and is
    cut back to the line of the roof's own edge there, each leg to its own slope's. At
    the eaves that is HIP_PROJECT (30 mm) beyond the trays' ends, so the cap's foot
    projects past the edge of the metal and the vent slot cannot be looked up into.
    "square": cut square at the end of its line.
  * The detail allows at most HIP_SEAMS (940 mm) between two standing seams at a hip. Seams
    that run up the slope meet a hip on the rake, so they are further apart along it than
    the trays are wide; the distance is reported and checked.

Every part is cut in its own outline, each cut end slid along the part's own edge, so a
tray keeps its width and its seams stay straight; only the lean of a cut on the bisecting
plane is carried as a shift with depth ("vshift"). The chain's edge offsets do not apply
along a hip.

Not modelled: the decompression strips under the cap (they are cut between raking seams
on site), the ridge clips, the insect mesh, laps between lengths of cap, the hems at the
legs' edges, any saddle or welt at a junction of caps (they are mitred edge to edge)."""

import math

from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from cladding_constants import _prism
from cladding_edges import settings as edge_settings
from cladding_primitives import ring_at
from cladding_ridge import (RIDGE_GAP, RIDGE_UPSTAND, RIDGE_OVERSAIL, RIDGE_LEG, RIDGE_REST, MEET, CLAD, JOIN_TOL,
                            _same_frame, _board, _up_slope, ridge_of, meets_hip)
from cladding_valley import valleys_of
from cladding_eaves import EAVES_TRAY, has_eaves

HIP_SEAMS = 940.0     # mm – the most between two standing seams at a hip (dimensioned)
HIP_PROJECT = 30.0    # mm – a cap's foot beyond the trays' ends at the eaves: it hides the vent slot from below
HIP_TOL = 30.0        # mm – an edge this close to the hip line, and along it, is an end at the hip
ALONG = 0.15          # an edge is along the hip when it leans no more than this (a sine) off its line
OWN = ("ridge_cap", "ridge_filler", "verge_strip", "valley_gutter", "eaves_strip")


def cap_ends(elev):
    """How the chain ends a cap that meets no other: "edge" (cut back to the roof's own
    edge, the default) or "square"."""
    return "square" if (elev or {}).get("cap_ends") == "square" else "edge"


def hips_of(elev):
    """The face's hips, as cladding_valley.valleys_of gives its valleys; "back" is how far
    the cladding's end stands in from the hip line at the face (depth 0)."""
    out = valleys_of(elev, "hip")
    for h in out:
        h["back"] = RIDGE_GAP / 2.0 * math.sqrt(1.0 + h["k"] ** 2)
    return out


def _dist(q, h):
    return (q[0] - h["p1"][0]) * h["m"][0] + (q[1] - h["p1"][1]) * h["m"][1]


def _along(q, h):
    dx, dy = (h["p2"][0] - h["p1"][0]) / h["length"], (h["p2"][1] - h["p1"][1]) / h["length"]
    return (q[0] - h["p1"][0]) * dx + (q[1] - h["p1"][1]) * dy


def _at_hip(ring, h, tol):
    """For each vertex of *ring*: None, or the unit direction to slide it in to bring it to
    the hip. A vertex is an end at the hip when an edge it is on lies along the hip line,
    within *tol* of it; it slides along its other edge (a tray's side, a board's long
    edge), or square to the line where that edge runs along the line too."""
    n = len(ring)
    near = [abs(_dist(q, h)) <= tol and -tol <= _along(q, h) <= h["length"] + tol for q in ring]

    def lean(a, b):                     # the sine of the edge's angle off the hip line; None if no edge
        dx, dy = b[0] - a[0], b[1] - a[1]
        size = math.hypot(dx, dy)
        return None if size < 0.5 else abs(dx * h["m"][0] + dy * h["m"][1]) / size
    out = []
    for i, q in enumerate(ring):
        slide = None
        if near[i]:
            sides = [(ring[j], near[j], lean(q, ring[j])) for j in ((i - 1) % n, (i + 1) % n)]
            on = [s for s in sides if s[1] and s[2] is not None and s[2] <= ALONG]
            if on:
                off = [s for s in sides if s not in on and s[2] is not None and s[2] > 0.2]
                if off:
                    r = off[0][0]
                    size = math.hypot(q[0] - r[0], q[1] - r[1])
                    slide = ((q[0] - r[0]) / size, (q[1] - r[1]) / size)
                else:
                    slide = (-h["m"][0], -h["m"][1])
        out.append(slide)
    return out


def hip_ends(meshes, p, infos):
    """Bring every part's end to the hip (module docstring). Returns the meshes with the
    trays' turned-up ends added."""
    faces = {e.get("name"): (hips_of(e), e) for e in p.get("elevations", []) if e.get("valleys")}
    faces = {name: f for name, f in faces.items() if f[0]}
    if not faces:
        return meshes
    face_by = {i["elevation"]: float(i.get("total_depth") or 0.0) for i in infos}
    board, extra = _board(p), []
    for m in meshes:
        hips, elev = faces.get(m.get("elevation")) or ((), None)
        kind = m.get("ifc_type")
        if not hips or kind in OWN or m.get("ridge_part") or m.get("hip_part") or not _same_frame(m["frame"], elev["frame"]):
            continue
        s = edge_settings(elev)
        tol = max(HIP_TOL, s["top"] + 5.0, s["side"] + 5.0, s["bottom"] + 5.0)
        face = face_by.get(m.get("elevation"), 0.0)
        near, t = float(m["depth"]), float(m["thickness"])
        mid = near + t / 2.0
        rings = [m["profile"]] + list(m.get("holes") or [])
        for h in hips:
            k = h["k"]
            if kind in MEET:
                target, lean = -k * mid, k
            elif kind in CLAD:
                target, lean = h["back"] - k * mid, k
            else:       # the deck, battens, closers: square, behind the cladding's end
                target, lean = h["back"] - min(k * (face - board) - RIDGE_OVERSAIL, k * near), 0.0
            old = m.get("vshift") or []
            shifts, moved = [], []
            for j, ring in enumerate(rings):
                was = old[j] if j < len(old) and old[j] else [[0.0] * 4] * len(ring)
                row = []
                for i, (q, sh, e) in enumerate(zip(ring, was, _at_hip(ring, h, tol))):
                    if e is None:
                        row.append(list(sh))
                        continue
                    em = e[0] * h["m"][0] + e[1] * h["m"][1]              # < 0: sliding out, towards the hip
                    step = (target - _dist(q, h)) / em
                    q[0], q[1] = round(q[0] + step * e[0], 3), round(q[1] + step * e[1], 3)
                    bu, bv = -lean / em * e[0], -lean / em * e[1]         # and on with the depth, on the plane
                    row.append([sh[0] - bu * mid, sh[1] + bu, sh[2] - bv * mid, sh[3] + bv])
                    if j == 0:
                        moved.append(i)
                shifts.append(row)
            if not moved:
                continue
            if lean or old:
                m["vshift"] = shifts
            if kind == "plank" and _up_slope(p) and len(moved) >= 2:
                up = _upstand(m, h, moved)
                if up is not None:
                    extra.append(up)
    return meshes + extra


def _upstand(tray, h, moved):
    """A tray's end turned up along the hip: a strip its own thickness wide on its raking
    end, between its seams, standing RIDGE_UPSTAND off its face."""
    outer = float(tray["depth"]) + float(tray["thickness"])
    ring = ring_at(tray, 0, outer)
    pts = sorted((ring[i] for i in moved), key=lambda q: _along(q, h))
    a, b = pts[0], pts[-1]
    size = math.hypot(b[0] - a[0], b[1] - a[1])
    if size < 20.0:
        return None
    dx, dy = (b[0] - a[0]) / size, (b[1] - a[1]) / size
    a, b = (a[0] + 3.0 * dx, a[1] + 3.0 * dy), (b[0] - 3.0 * dx, b[1] - 3.0 * dy)
    t, (mx, my) = float(tray["thickness"]), h["m"]
    strip = orient(Polygon([a, b, (b[0] + t * mx, b[1] + t * my), (a[0] + t * mx, a[1] + t * my)]), 1.0)
    up = _prism([[round(x, 3), round(y, 3)] for x, y in list(strip.exterior.coords)[:-1]], outer, RIDGE_UPSTAND,
                tray["frame"], "seam", "%s Hip Upstand" % tray.get("name", "Tray"), tray.get("elevation"))
    up.update(opacity=1.0, hip_part=True)
    return up


def _solve(one, two):
    """Where two lines of a face meet, and how that point moves with depth. A line is
    (n, c, rate): the points x with n·x = c at the sheet's mid-depth, c growing by *rate*
    for each mm of depth. None where they are parallel."""
    (n1, c1, r1), (n2, c2, r2) = one, two
    det = n1[0] * n2[1] - n1[1] * n2[0]
    if abs(det) < 0.05:
        return None
    at = lambda a, b: ((a * n2[1] - b * n1[1]) / det, (n1[0] * b - n2[0] * a) / det)     # noqa: E731
    return at(c1, c2), at(r1, r2)


def _cap_lines(elev, by):
    """The lines of this face a cap leg lies along: its hips, and its ridge where a hip
    runs into an end of that (the ridge cap is otherwise one folded sheet: cladding_ridge).
    Each {"kind", "with", "p1", "p2", "m": into the face, "k", "length"}."""
    lines = [dict(h, kind="hip") for h in hips_of(elev)]
    r = ridge_of(elev)
    other = by.get(r["with"]) if r else None
    ro = ridge_of(other) if other else None
    if ro and (meets_hip(elev, r) or meets_hip(other, ro)):
        lines.append({"kind": "ridge", "with": r["with"], "p1": [r["u0"], r["v"]], "p2": [r["u1"], r["v"]],
                      "m": [0.0, -1.0], "k": r["k"], "length": r["u1"] - r["u0"], "master": r["master"]})
    return lines


def _roof_edge(elev, end, d):
    """The edge of the face's outline that leaves the point *end* other than along the
    direction *d* (a cap's own line): the roof's edge there, as (unit normal, n·x on it).
    None where the outline has no corner at that point."""
    best = None
    for pg in elev.get("polygons") or []:
        ring = pg["exterior"]
        for i, q in enumerate(ring):
            gap = math.hypot(q[0] - end[0], q[1] - end[1])
            if gap > JOIN_TOL:
                continue
            for r in (ring[i - 1], ring[(i + 1) % len(ring)]):
                gx, gy = r[0] - q[0], r[1] - q[1]
                size = math.hypot(gx, gy)
                if size < 1.0:
                    continue
                across = abs(gx * d[1] - gy * d[0]) / size             # the sine of its angle off the cap's line
                if across > 0.2 and (best is None or (gap, -across) < best[0]):
                    n = (-gy / size, gx / size)
                    best = ((gap, -across), n, n[0] * q[0] + n[1] * q[1])
    return None if best is None else (best[1], best[2])


def _leg(line, lines, rest, t, elev, drop, ends):
    """One leg of a cap, as a sheet lying on its slope: its outline in the face's own
    (u, v) at the sheet's mid-depth, and each corner's shift with depth. From the cap's
    apex, on the plane that bisects the hip (or ridge), RIDGE_LEG down the slope.

    An end that meets another cap's on this face is mitred to it: both run to the line
    their apexes meet on and their edges meet on. Any other end is the chain's to choose
    (*ends*): "edge", the cap runs on and is cut back to the line of the roof's own edge
    there, and where that edge is downhill of it (the eaves) to HIP_PROJECT beyond the
    trays' ends, *drop* past the edge; or "square", cut square at the end of its line."""
    mid = rest + t / 2.0
    m, k = line["m"], line["k"]
    (x1, y1), (x2, y2) = line["p1"], line["p2"]
    d = ((x2 - x1) / line["length"], (y2 - y1) / line["length"])
    base = m[0] * x1 + m[1] * y1
    apex, edge = (m, base - k * mid, -k), (m, base - k * mid + RIDGE_LEG, 0.0)
    corners = {}
    for end, (ex, ey), (ox, oy) in (("start", (x1, y1), (x2, y2)), ("end", (x2, y2), (x1, y1))):
        near = [o for o in lines if o is not line
                and min(math.hypot(q[0] - ex, q[1] - ey) for q in (o["p1"], o["p2"])) <= JOIN_TOL]
        cut = None
        if near:                                    # mitred to the cap it meets
            o = near[0]
            ob = o["m"][0] * o["p1"][0] + o["m"][1] * o["p1"][1]
            cut = (_solve(apex, (o["m"], ob - o["k"] * mid, -o["k"])), _solve(edge, (o["m"], ob - o["k"] * mid + RIDGE_LEG, 0.0)))
        elif ends != "square":                      # run on, and cut back to the roof's edge
            found = _roof_edge(elev, (ex, ey), d)
            if found:
                n, c = found
                if n[0] * (ex - ox) + n[1] * (ey - oy) < 0:
                    n, c = (-n[0], -n[1]), -c                            # pointing on past the end
                if n[1] < -0.5:                                          # downhill: past the trays' ends
                    c += HIP_PROJECT + (drop if abs(n[0]) < 0.02 else 0.0)
                cut = (_solve(apex, (n, c, 0.0)), _solve(edge, (n, c, 0.0)))
        if not cut or cut[0] is None or cut[1] is None:
            square = (d, d[0] * ex + d[1] * ey, 0.0)
            cut = (_solve(apex, square), _solve(edge, square))
        corners[end] = cut
    pts = [corners["start"][0], corners["end"][0], corners["end"][1], corners["start"][1]]
    ring = [[round(q[0], 3), round(q[1], 3)] for q, _r in pts]
    shifts = [[-r[0] * mid, r[0], -r[1] * mid, r[1]] for _q, r in pts]
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1]))
    if area < 0:
        ring, shifts = ring[::-1], shifts[::-1]
    return ring, shifts


def hip_parts(meshes, p, infos):
    """The cap over each hip, and over a ridge that hips run into: each face lays the leg
    that lies on it (_leg), so the two slopes of a hip need not be built together. Records
    each face's hips on its info ("hips", "hip_strips")."""
    info_by = {i["elevation"]: i for i in infos}
    by = {e.get("name"): e for e in p.get("elevations", [])}
    up_slope = _up_slope(p)
    extra = []
    for elev in p.get("elevations", []):
        name, frame, info = elev.get("name"), elev.get("frame"), info_by.get(elev.get("name"))
        lines = _cap_lines(elev, by) if info is not None else []
        if not lines:
            continue
        rest = float(info.get("total_depth") or 0.0) + (float(p["seam_h"]) if up_slope else 0.0) + RIDGE_REST
        t = float(p["seam_t"]) if p.get("seam") else 0.7
        drop = EAVES_TRAY if has_eaves(elev, p) else 0.0
        short = lambda n: str(n or "").replace("Elevation ", "")              # noqa: E731
        for h in lines:
            ring, shifts = _leg(h, lines, rest, t, elev, drop, cap_ends(elev))
            ridge = h["kind"] == "ridge"
            leg = _prism(ring, rest, t, frame, "ridge_cap", "%s Ridge Cap" % name if ridge else "%s Hip Cap %s" % (name, short(h["with"])), name)
            leg["vshift"] = [shifts]
            leg["cap_leg"] = True
            leg["ridge_info" if ridge else "hip_info"] = {"length": round(h["length"], 1), "leg": RIDGE_LEG, "thickness": t,
                                                          "between": [name, h["with"]]}
            extra.append(leg)
            if ridge:
                continue
            (x1, y1), (x2, y2) = h["p1"], h["p2"]
            dx = (x2 - x1) / h["length"]
            mx, my = h["m"]
            pitch = [float(elev.get("pitch") or 0.0), math.degrees(math.acos(max(-1.0, min(1.0, abs(h["other_n"][2])))))]
            spacing = round(float(p["plank_w"]) / abs(dx), 1) if up_slope and abs(dx) > 1e-6 else None
            info.setdefault("hips", []).append({
                "with": h["with"], "master": h["master"], "length": round(h["length"], 1), "leg": RIDGE_LEG, "gap": RIDGE_GAP,
                "pitches": [round(a, 1) for a in pitch], "angle": round(math.degrees(2.0 * math.atan(h["k"])), 1),
                "seam_spacing": spacing, "seam_limit": HIP_SEAMS})
            info.setdefault("hip_strips", []).append(
                [[x1, y1], [x2, y2], [x2 + RIDGE_LEG * mx, y2 + RIDGE_LEG * my], [x1 + RIDGE_LEG * mx, y1 + RIDGE_LEG * my]])
    return meshes + extra
