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
OWN = ("ridge_cap", "ridge_filler", "verge_strip", "valley_gutter", "eaves_strip", "corner_profile", "corner_trim")


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


def _hip_cut(h, kind, near, t, face, board, _elev, _p):
    """Where a part of this kind ends at a hip, as (its distance in from the hip line at
    its own mid-depth, how that grows towards the line with depth)."""
    k, mid = h["k"], near + t / 2.0
    if kind in MEET:
        return -k * mid, k
    if kind in CLAD:
        return h["back"] - k * mid, k
    # the deck, battens, closers: square, behind the cladding's end
    return h["back"] - min(k * (face - board) - RIDGE_OVERSAIL, k * near), 0.0


def _whole_corners(ring, rows, h, tol, keep):
    """Give a part back a corner the trim took off it but the cut does not. Trimmed to the
    face, a board near the line has its corner clipped: an edge along the line between two
    of its own edges. Where the cut stands further out than the trim did (a corner profile:
    the boards run on past the wall's corner line), the corner those two edges would meet
    at may lie inside the cut, and then the board has it whole. Slid along their edges
    instead, the two ends pass each other and the board grows a spike past its own top.
    *keep*: how far in from the line a point must stand to be inside the cut at every
    depth. Changes *ring* in place; returns its vshift rows, one fewer per corner restored."""
    while len(ring) > 3:
        slides = _at_hip(ring, h, tol)
        n = len(ring)
        for i in range(n):
            j = (i + 1) % n
            (a, e1), (b, e2) = (ring[i], slides[i]), (ring[j], slides[j])
            if e1 is None or e2 is None:
                continue
            det = e1[0] * e2[1] - e1[1] * e2[0]
            if abs(det) < 0.05:
                continue
            s = ((b[0] - a[0]) * e2[1] - (b[1] - a[1]) * e2[0]) / det
            u = ((b[0] - a[0]) * e1[1] - (b[1] - a[1]) * e1[0]) / det
            corner = [round(a[0] + s * e1[0], 3), round(a[1] + s * e1[1], 3)]
            if s < -1e-6 or u < -1e-6 or _dist(corner, h) < keep - 1e-6:
                continue
            ring[i][0], ring[i][1] = corner
            del ring[j]
            del rows[j]
            break
        else:
            return rows
    return rows


def hip_ends(meshes, p, infos):
    """Bring every part's end to the hip (module docstring). Returns the meshes with the
    trays' turned-up ends added. A steel chain's hips are GreenCoat's Hip 1: the trays stand
    clear of a batten on the hip line, every layer behind meets on the plane (cladding_greencoat)."""
    from cladding_greencoat import steel, hip1_lines, hip1_cut
    if steel(p):
        return cut_to_lines(meshes, p, infos, lambda e: hip1_lines(hips_of(e)), hip1_cut)     # its upstands: hip1_parts
    return cut_to_lines(meshes, p, infos, hips_of, _hip_cut, upstands=True)


def cut_to_lines(meshes, p, infos, lines_of, cut, upstands=False):
    """Bring the end of every part of a face to each of the lines *lines_of*(face) gives
    (a hip; a raking corner: cladding_rake), where *cut* says a part of its kind ends.
    Each end is slid along the part's own edge; a cut that leans is carried as a shift
    with depth."""
    faces = {e.get("name"): (lines_of(e), e) for e in p.get("elevations", []) if e.get("valleys")}
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
            target, lean = cut(h, kind, near, t, face, board, elev, p)
            old = m.get("vshift") or []
            shifts, moved = [], []
            for j, ring in enumerate(rings):
                was = old[j] if j < len(old) and old[j] else [[0.0] * 4] * len(ring)
                was = _whole_corners(ring, [list(sh) for sh in was], h, tol, target + abs(lean) * t / 2.0)
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
            if upstands and kind == "plank" and _up_slope(p) and len(moved) >= 2:
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
    up = _prism([[round(x, 3), round(y, 3)] for x, y in list(strip.exterior.coords)[:-1]], outer, h.get("upstand", RIDGE_UPSTAND),
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


def _leg(line, lines, rest, t, elev, drop, ends, pyramid=False):
    """One leg of a cap, as a sheet lying on its slope: its outline in the face's own
    (u, v) at the sheet's mid-depth, and each corner's shift with depth. From the cap's
    apex, on the plane that bisects the hip (or ridge), RIDGE_LEG down the slope.

    An end that meets another cap's on this face is mitred to it: both run to the line
    their apexes meet on and their edges meet on. Any other end is the chain's to choose
    (*ends*): "edge", the cap runs on and is cut back to the line of the roof's own edge
    there, and where that edge is downhill of it (the eaves) to HIP_PROJECT beyond the
    trays' ends, *drop* past the edge; or "square", cut square at the end of its line.
    *pyramid*: an end that meets another line is cut on that line's bisecting plane, edge and
    apex alike (the ridge cap's pyramid end over Hip 1), not mitred to a cap along it."""
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
            on = (o["m"], ob - o["k"] * mid, -o["k"])                  # the plane bisecting the line it meets
            cut = (_solve(apex, on), _solve(edge, on if pyramid else (o["m"], ob - o["k"] * mid + RIDGE_LEG, 0.0)))
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
    each face's hips on its info ("hips", "hip_strips").

    A steel chain's hips are GreenCoat's Hip 1 (cladding_greencoat.hip1_parts). Where they
    run up into a ridge or meet at the top of a hip end, the ridge cap carries on over the
    apex as a little hip of its own: the same legs as the zinc cap's, raised to clear the
    Hip 1 flashing, the hips' legs cut square across where the ridge cap's edge line meets
    them (little_hip); each Hip 1 stops HIP1_LAP inside that, under the cap."""
    from cladding_greencoat import steel, hip1_parts, little_hip, little_rest
    metal = steel(p)
    if metal:
        meshes = hip1_parts(meshes, p, infos)
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
        if metal:
            raised = max(rest, little_rest(meshes, elev, t) or rest)
            if raised > rest and any(x["kind"] == "ridge" for x in lines):
                # the cap raised over Hip 1 along the whole ridge: its vented fillers made up to meet it
                for m in meshes:
                    if m.get("ifc_type") == "ridge_filler" and m.get("elevation") == name and _same_frame(m["frame"], frame):
                        m["thickness"] = round(raised - float(m["depth"]), 3)
            rest = raised
        drop = EAVES_TRAY if has_eaves(elev, p) else 0.0
        short = lambda n: str(n or "").replace("Elevation ", "")              # noqa: E731
        if metal:
            end = _pyramid_end(lines, rest, t, elev)                         # a hip end's face: the cap's end triangle
            if end is not None:
                ring, shifts = end
                leg = _prism(ring, rest, t, frame, "ridge_cap", "%s Ridge Cap End" % name, name)
                leg.update(vshift=[shifts], cap_leg=True, pyramid_end=True)
                extra.append(leg)
        for h in lines:
            ridge = h["kind"] == "ridge"
            if metal and not ridge:
                continue                                                     # Hip 1 (hip1_parts): no zinc cap along it
            ring, shifts = _leg(h, lines, rest, t, elev, drop, cap_ends(elev), pyramid=metal)
            leg = _prism(ring, rest, t, frame, "ridge_cap", "%s Ridge Cap" % name if ridge else "%s Hip Cap %s" % (name, short(h["with"])), name)
            leg["vshift"] = [shifts]
            leg["cap_leg"] = True
            leg["ridge_info" if ridge else "hip_info"] = {"length": round(h["length"], 1), "leg": RIDGE_LEG, "thickness": t,
                                                          "between": [name, h["with"]]}
            extra.append(leg)
            if ridge:
                continue
            if drop:
                # the cap's foot at the eaves closed: a stop end along the foot of the leg, from its underside
                # down to the trays' face, so the vent slot under the cap cannot be looked into from the front
                vs = [q[1] for q in ring]
                low = min(vs)
                us = [q[0] for q in ring if q[1] <= low + 0.5]
                face = float(info.get("total_depth") or 0.0)
                if len(us) >= 2 and max(us) - min(us) > 5.0 and rest - face > 1.0:
                    stop = _prism([[min(us), low], [max(us), low], [max(us), low + t], [min(us), low + t]], face, rest - face,
                                  frame, "ridge_cap", "%s Hip Stop End %s" % (name, short(h["with"])), name)
                    stop.update(tray_sheet=True, of=leg["name"], stop_end=True)
                    extra.append(stop)
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


def _pyramid_end(lines, rest, t, elev):
    """On a face whose hips meet at its top (a hip end) under a ridge cap that runs on over
    the apex (a steel chain: cladding_greencoat.little_hip), the cap's end: one triangle on
    the slope, from the apex down between the two hips' bisecting planes to a level edge
    RIDGE_LEG down the slope, so with the ridge cap's legs (cut on the same planes: _leg,
    *pyramid*) it closes the cap's end as a pyramid. (ring, shifts) as _leg gives, or None."""
    from cladding_greencoat import little_hip
    hips = [h for h in lines if h["kind"] == "hip" and little_hip(elev, h, lines) is not None]
    if len(hips) != 2 or any(h["kind"] == "ridge" for h in lines):
        return None
    mid = rest + t / 2.0
    planes = [(h["m"], h["m"][0] * h["p1"][0] + h["m"][1] * h["p1"][1] - h["k"] * mid, -h["k"]) for h in hips]
    apex = _solve(planes[0], planes[1])
    if apex is None:
        return None
    edge = ((0.0, 1.0), apex[0][1] - RIDGE_LEG, 0.0)
    pts = [apex, _solve(planes[0], edge), _solve(planes[1], edge)]
    if any(q is None for q in pts):
        return None
    ring = [[round(q[0], 3), round(q[1], 3)] for q, _r in pts]
    shifts = [[-r_[0] * mid, r_[0], -r_[1] * mid, r_[1]] for _q, r_ in pts]
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1]))
    if area < 0:
        ring, shifts = ring[::-1], shifts[::-1]
    return ring, shifts


def _clip_sheet(m, n, c):
    """Keep the part of a sheet laid in its face's frame (a cap leg, an upstand: one ring in
    the face's (u, v), its corners' shifts with depth in "vshift") on the side n·x <= c of a
    line, a new corner where an edge crosses it, its shift taken pro rata. False if nothing
    is left of it, True otherwise (*m* changed in place)."""
    ring = [list(q) for q in m["profile"]]
    old = m.get("vshift") or []
    rows = [list(r) for r in old[0]] if old and old[0] else [[0.0] * 4 for _q in ring]
    side = [n[0] * q[0] + n[1] * q[1] - c for q in ring]
    if all(s <= 1e-6 for s in side):
        return True
    if all(s >= -1e-6 for s in side):
        return False
    pts, shifts = [], []
    for i in range(len(ring)):
        j = (i + 1) % len(ring)
        if side[i] <= 1e-6:
            pts.append(ring[i])
            shifts.append(rows[i])
        if (side[i] < -1e-6 < side[j]) or (side[j] < -1e-6 < side[i]):
            f = side[i] / (side[i] - side[j])
            pts.append([round(ring[i][k] + f * (ring[j][k] - ring[i][k]), 3) for k in (0, 1)])
            shifts.append([rows[i][k] + f * (rows[j][k] - rows[i][k]) for k in range(4)])
    if len(pts) < 3:
        return False
    m["profile"] = pts
    if old:
        m["vshift"] = [shifts] + list(old[1:])
    return True


def _hip_tops(elev, p, info):
    """Where a hip on this face runs up to the foot of a verge set in from the face's end (a
    gablet: the hip ends at the top of the hip end, under the small gable, and the slope's
    rake rises from there), the seam on the rake's line meets the hip a little below the
    hip's top. [(the hip record, S: where that seam meets it, T: the hip's top, the seam's
    u)], in the face's own (u, v)."""
    from cladding_verge import _outline_sides, verge_sides
    sides = verge_sides(elev, p)
    edges = _outline_sides(elev)
    lines = sorted(float(u) for u in (info.get("seam_lines") or []))
    out = []
    if not sides or not lines:
        return out
    for side in sides:
        if side not in edges:
            continue
        u_v = edges[side][0]
        foot = min((q[1] for pg in elev.get("polygons") or [] for q in pg["exterior"] if abs(q[0] - u_v) < 1.0), default=None)
        if foot is None:
            continue
        for h in hips_of(elev):
            lo, hi = sorted((h["p1"], h["p2"]), key=lambda q: q[1])
            if math.hypot(hi[0] - u_v, hi[1] - foot) > JOIN_TOL:
                continue                                                 # this hip does not run up to the rake's foot
            near = [u for u in lines if (u <= u_v + 1.0 if side == "l" else u >= u_v - 1.0)]
            if not near:
                continue
            u_s = max(near) if side == "l" else min(near)
            if abs(u_s - u_v) > 400.0 or abs(hi[0] - lo[0]) < 1.0:
                continue
            f = (u_s - lo[0]) / (hi[0] - lo[0])
            if not 0.0 < f < 1.0:
                continue
            out.append((h, (u_s, lo[1] + f * (hi[1] - lo[1])), (hi[0], hi[1]), u_s))
    return out


def _plane_line(m, point, normal, force=False):
    """The plane through *point* with *normal* (world) where it crosses a sheet laid in its
    frame and extruded square to it, as a line in the sheet's (u, v) at its mid-depth:
    (n, c), the plane's front (the side *normal* points to) where n·x > c. None where the
    plane is not square to the sheet's face (it would cut it on a slant through its
    thickness)."""
    f = m["frame"]
    fu, fn = [float(c) for c in f["u"]], [float(c) for c in f["n"]]
    fv = f.get("v") or [fn[1] * fu[2] - fn[2] * fu[1], fn[2] * fu[0] - fn[0] * fu[2], fn[0] * fu[1] - fn[1] * fu[0]]
    dot = lambda a, b: sum(x * y for x, y in zip(a, b))                     # noqa: E731
    size = math.sqrt(dot(normal, normal))
    nn = [c / size for c in normal]
    if abs(dot(fn, nn)) > 0.02 and float(m["thickness"]) > 2.0 and not force:
        return None                                  # a thin sheet is cut at its mid-depth whatever the plane's slant (or *force*d)
    a, b = dot(fu, nn), dot(fv, nn)
    k = math.hypot(a, b)
    if k < 1e-9:
        return None
    mid = float(m["depth"]) + float(m["thickness"]) / 2.0
    c = dot([p - o for p, o in zip(point, f["origin"])], nn) - mid * dot(fn, nn)
    return (a / k, b / k), c / k


def _as_plate(m):
    """A box (a rectangular outline square to its frame, run along its frame's n with no
    shift) as the same solid laid across its thinnest dimension: a plate whose outline is
    its biggest face and whose thickness is that dimension. A board run along a wall's foot,
    or a sheet standing off a face, can then be cut by a plane as a line on that face
    (_plane_line). None where it is not such a box."""
    if m.get("vshift") or m.get("holes") or m.get("corner") or len(m["profile"]) != 4:
        return None
    us = sorted({round(q[0], 3) for q in m["profile"]})
    vs = sorted({round(q[1], 3) for q in m["profile"]})
    if len(us) != 2 or len(vs) != 2:
        return None
    f = m["frame"]
    fu, fn = [float(c) for c in f["u"]], [float(c) for c in f["n"]]
    fv = [float(c) for c in (f.get("v") or [fn[1] * fu[2] - fn[2] * fu[1], fn[2] * fu[0] - fn[0] * fu[2], fn[0] * fu[1] - fn[1] * fu[0]])]
    d0, L = float(m["depth"]), float(m["thickness"])
    axes = [(us[1] - us[0], fu, us[0]), (vs[1] - vs[0], fv, vs[0]), (L, fn, d0)]   # (size, direction, start)
    thin = min(range(3), key=lambda k: axes[k][0])
    a, b = [axes[k] for k in range(3) if k != thin]
    origin = [o + axes[0][2] * x + axes[1][2] * y + axes[2][2] * z for o, x, y, z in zip(f["origin"], fu, fv, fn)]
    origin = [o - axes[thin][2] * t + 0.0 for o, t in zip(origin, axes[thin][1])]     # the thin axis is the plate's depth
    plate = dict(m, frame={"origin": origin, "u": a[1], "v": b[1], "n": axes[thin][1]},
                 profile=[[0.0, 0.0], [a[0], 0.0], [a[0], b[0]], [0.0, b[0]]], depth=axes[thin][2], thickness=axes[thin][0])
    plate.pop("vshift", None)
    return plate


def _join_rake_seam(out, name, frame, u_s, T):
    """The verge's edge seam (its sheets laid in the face's frame, standing on the rake's line)
    carried down from the rake's foot *T* to the tray seam on the same line below it, each
    sheet to the top of the same sheet of that seam (the overcloak to the overcloak, its bend
    to its bend: the tray seam's head is cut on the hip's slant, so its sheets end at
    different heights), and set on that seam's line, so the two meet end to end. Nothing
    where there is no tray seam there."""
    on = lambda m: abs(float(m.get("seam_line") or -1e9) - u_s) < 3.0 and _same_frame(m["frame"], frame)   # noqa: E731
    def part(m):                                                            # "overcloak bend", "undercloak", ...
        tail = m.get("name", "").split(" Seam ", 1)[-1]
        return tail[2:] if tail[:2] in ("L ", "R ") else tail
    trays = [m for m in out if m.get("elevation") == name and m.get("seam_part") and not m.get("verge_seam") and on(m)]
    edge = [m for m in out if m.get("elevation") == name and m.get("verge_seam") and on(m)
            and abs(min(q[1] for q in m["profile"]) - T[1]) < 150.0]
    if not trays or not edge:
        return
    tops = {}
    for m in trays:
        top = max(q[1] for dd in (float(m["depth"]), float(m["depth"]) + float(m["thickness"])) for q in ring_at(m, 0, dd))
        tops[part(m)] = min(tops.get(part(m), top), top)
    line = float(trays[0]["seam_line"])
    for m in edge:
        top = tops.get(part(m), min(tops.values()))
        shift = line - float(m["seam_line"])
        low = min(q[1] for q in m["profile"])
        for q in m["profile"]:
            q[0] = round(q[0] + shift, 3)
            if q[1] <= low + 0.5:
                q[1] = round(min(q[1], top - 2.0), 3)                   # a little into its partner: no gap between
        m["seam_line"] = line


def gablet_hip_tops(meshes, p, infos):
    """The top of a hip under a gablet's gable, after the G3 hip. The seam on the rake's line
    meets the hip a little below the hip's top, and stands in the plane of the gable
    cladding's face, which the verge's drip or flashing hangs in front of. **The hip cap is
    cut on that plane**: on the hip end's side its leg runs on up to meet the verge's drip
    face, and is cut square across where the drip's foot comes down to it (a vertical plane
    square to the gable face through the rake's foot); on the slope's side its leg, which would ride over the
    seam, ends against it. The trays' upstands along the hip are cut on it too. **The abutment parts at the gable's foot**, the hip end's (the plywood stood up
    the gable, the trays' upstands, the saddles) and the gable's own (its edge flashing),
    where they rise through the slope's plane at its corner, are cut back to the underside
    of the slope's plywood."""
    from cladding_constants import frame_to_world, frame_v
    if not p.get("seam") or not _up_slope(p):
        return meshes
    info_by = {i["elevation"]: i for i in infos}
    by = {e.get("name"): e for e in p.get("elevations", [])}
    out = list(meshes)
    dot = lambda a, b: sum(x * y for x, y in zip(a, b))                     # noqa: E731
    for elev in p.get("elevations", []):
        name, info = elev.get("name"), info_by.get(elev.get("name"))
        if info is None:
            continue
        for h, S, T, u_s in _hip_tops(elev, p, info):
            frame = elev["frame"]
            world_s = frame_to_world(frame, S[0], S[1], 0.0)
            world_t = frame_to_world(frame, T[0], T[1], 0.0)                 # the rake's foot: where the drip comes down
            U = [float(c) for c in frame["u"]]
            out_dir = [-c for c in U] if T[0] > u_s else U                  # out past the verge
            V = [float(c) for c in frame_v(frame)]
            size = math.hypot(V[0], V[1])
            up_rake = [V[0] / size, V[1] / size, 0.0]                        # level, up the rake: the drip's foot is square to it
            # the cap legs and the upstands, on this face and the hip end, cut on the seam's plane
            for face in (name, h["with"]):
                ef = by.get(face)
                if ef is None:
                    continue
                for m in [x for x in out if x.get("elevation") == face and _same_frame(x["frame"], ef["frame"])
                          and ((x.get("cap_leg") and not x.get("ridge_info") and " Hip Cap " in x.get("name", ""))
                               or (x.get("hip_part") and " Hip Upstand" in x.get("name", "")))]:
                    near = min(math.dist(frame_to_world(m["frame"], q[0], q[1], float(m["depth"])), world_s) for q in m["profile"])
                    if near > 1500.0:
                        continue                                             # another hip's
                    kept = True
                    for at, normal in ((world_s, [-c for c in out_dir]), (world_t, up_rake)):
                        line = _plane_line(m, at, normal, force=True)       # an upstand cut at its mid-height: under the cap
                        if line is None:
                            continue
                        (n, c) = line                                        # keep its back: out past the verge, down the rake
                        if not _clip_sheet(m, n, c):
                            kept = False
                            break
                    if not kept:
                        out.remove(m)
            # the verge's edge seam carried down the rake's line to meet the tray seam below it, on that
            # seam's own line, so the two read as one seam from the eaves to the ridge
            _join_rake_seam(out, name, frame, u_s, T)
            # the hip end's abutment parts at the gable's foot, cut back under this slope's plywood
            decks = [m for m in out if m.get("elevation") == name and m.get("ifc_type") == "deck" and _same_frame(m["frame"], frame)]
            if not decks:
                continue
            soffit = min(float(m["depth"]) for m in decks)
            N = [float(c) for c in frame["n"]]
            under = [o + soffit * n for o, n in zip(frame["origin"], N)]
            walls = set((elev.get("verge_walls") or {}).values())               # the gable standing on the hip end, and its parts
            for m in [x for x in out if (x.get("elevation") == h["with"] or x.get("elevation") in walls) and "Abutment" in x.get("name", "")]:
                pts = [frame_to_world(m["frame"], q[0], q[1], float(m["depth"]) + dd) for q in m["profile"] for dd in (0.0, float(m["thickness"]))]
                if max(dot([a - b for a, b in zip(q, under)], N) for q in pts) <= 0.5:
                    continue                                                 # below the slope's plywood already
                if min(math.dist(q, world_s) for q in pts) > 2500.0:
                    continue
                line = _plane_line(m, under, N)
                if line is not None:                                         # a sheet on the hip end: cut as a line on it
                    (n, c) = line
                    if not _clip_sheet(m, n, c):
                        out.remove(m)
                    continue
                # a board run along the gable's foot: laid as a plate across its thinnest dimension and cut on
                # the slope's line; a folded section run along it (a tray's upstand): its end cut on the plane
                plate = _as_plate(m)
                if plate is None:
                    from cladding_verge import _cut_end
                    fn = [float(c) for c in m["frame"]["n"]]
                    ends = [[frame_to_world(m["frame"], q[0], q[1], float(m["depth"]) + dd) for q in m["profile"]]
                            for dd in (0.0, float(m["thickness"]))]
                    high = [max(dot([a - b for a, b in zip(q, under)], N) for q in e) for e in ends]
                    at_start = high[0] > high[1]
                    pieces = _cut_end(m, [(None, under, N, "Slope")], at_start=at_start)
                    if pieces:
                        old_end = ends[0] if at_start else ends[1]
                        axis = fn if not at_start else [-c for c in fn]          # out past that end
                        o_e = old_end[0]
                        reach = max(dot([a - b for a, b in zip(q, o_e)], axis) for pc in pieces
                                    for q in (frame_to_world(pc["frame"], x, y, dd) for x, y in ring_at(pc, 0, 0.0)
                                              for dd in (float(pc["depth"]), float(pc["depth"]) + float(pc["thickness"]))))
                        if reach <= 60.0:                                    # it may nudge its foot a little on, no further
                            i = out.index(m)
                            out[i:i + 1] = pieces
                    continue
                line = _plane_line(plate, under, N)
                if line is None:
                    continue
                (n, c) = line
                i = out.index(m)
                if _clip_sheet(plate, n, c):
                    out[i] = plate
                else:
                    out.pop(i)
    return out
