"""CladForge — the valley: two roof slopes falling towards each other and meeting on a
line, level or (more often) sloping.

After VMZINC's standard "Valley detail" for slopes over 14° (VMZ-SN-MA-KI-b2). Each face
of the pair carries a "valleys" record per valley (fabric_extract.valley_links, kept by
the app): the partner's name, the valley line in this face's own (u, v), k = tan(half the
angle between the two normals), and the partner's directions, so each face is finished on
its own and the two need not share a chain or its settings.

  * The cladding stands back from the valley line so that VALLEY_CLEAR (200 mm, the
    detail's minimum) is left clear between the two slopes' trays, measured straight
    across the valley. It is cut before seams are finished, so a seam that runs out at the
    valley is ended there like any other.
  * Everything under it (deck, battens, sheathing, insulation) runs to the plane that
    bisects the valley and is cut on it, so the two slopes' layers meet without running
    through each other.
  * The valley gutter: one folded sheet lying on both decks, under the trays, running up
    each slope to VALLEY_LAP beyond the trays' ends. Built once per valley, by its master.

VALLEY_LAP is the detail's "R = overlap, see installation guide": it is not dimensioned
there and is scaled from the drawing. Not modelled: the continuous welt strip the trays'
ends hook to, the sheet clips along the gutter's edges, the water flow controller, the
gutter's ends at the eaves and the head of the valley (it is cut square)."""

import math

from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient

from cladding_booleans import _difference, iter_polygons, polygon_to_rings
from cladding_constants import _prism, frame_to_world, frame_v
from cladding_ridge import _same_frame, _board

VALLEY_CLEAR = 200.0   # mm – clear between the two slopes' trays, straight across the valley (dimensioned, a minimum)
VALLEY_LAP = 180.0     # mm – the trays over the gutter, "R" (scaled: see the installation guide)
VALLEY_MIN_PITCH = 14.0   # deg – the detail is for slopes steeper than this
EDGE_TOL = 15.0        # mm – an end this close to the valley line is an end at the valley
CLAD = ("plank", "panel", "seam")
OWN = ("valley_gutter", "ridge_cap", "ridge_filler", "verge_strip")
_MIN_AREA = 100.0      # mm² – a scrap of cladding smaller than this, left by the cut, is dropped


def valleys_of(elev):
    """The face's valleys, each {"with", "p1", "p2", "k", "master", "other_up", "other_n",
    "m": the unit normal to the line, in the face, pointing into it, "length", "back": how
    far the cladding stands back from the line}."""
    out = []
    for v in (elev or {}).get("valleys") or []:
        try:
            p1, p2 = [float(c) for c in v["p1"]], [float(c) for c in v["p2"]]
            k = float(v["k"])
            length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            if k <= 0 or length < 1.0:
                continue
            m = [-(p2[1] - p1[1]) / length, (p2[0] - p1[0]) / length]
            inside = v.get("inside") or _inside(elev)
            if (inside[0] - p1[0]) * m[0] + (inside[1] - p1[1]) * m[1] < 0:
                m = [-m[0], -m[1]]
            out.append({"with": v.get("with"), "p1": p1, "p2": p2, "k": k, "master": bool(v.get("master")),
                        "other_up": [float(c) for c in v["other_up"]], "other_n": [float(c) for c in v["other_n"]],
                        "m": m, "length": length, "back": VALLEY_CLEAR / 2.0 * math.sqrt(1.0 + k * k)})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _inside(elev):
    """A point well inside the face: the centroid of its largest patch."""
    best = max((Polygon(pg["exterior"]) for pg in elev.get("polygons") or []), key=lambda g: g.area, default=None)
    c = best.centroid if best is not None else None
    return [c.x, c.y] if c is not None else [float(elev["width"]) / 2.0, float(elev["height"]) / 2.0]


def _band(v, width):
    """The strip *width* wide along the valley line, run on past both its ends."""
    (x1, y1), (x2, y2) = v["p1"], v["p2"]
    dx, dy = (x2 - x1) / v["length"], (y2 - y1) / v["length"]
    reach = width + 50.0
    return LineString([(x1 - dx * reach, y1 - dy * reach), (x2 + dx * reach, y2 + dy * reach)]).buffer(width, cap_style=2)


def valley_cut(meshes, p):
    """Stand the cladding back from every valley line (module docstring). Runs on the
    trimmed boards and trays, before linings are mitred and seams finished."""
    faces = {e.get("name"): (valleys_of(e), e["frame"]) for e in p.get("elevations", []) if e.get("valleys")}
    if not faces:
        return meshes
    out = []
    for mesh in meshes:
        valleys, frame = faces.get(mesh.get("elevation")) or ((), None)
        if not valleys or mesh.get("ifc_type") not in CLAD or not _same_frame(mesh["frame"], frame):
            out.append(mesh)
            continue
        try:
            poly = Polygon(mesh["profile"], mesh.get("holes") or [])
            poly = poly if poly.is_valid else poly.buffer(0)
            cut = poly
            for v in valleys:
                band = _band(v, v["back"])
                if cut is not None and not cut.is_empty and cut.intersects(band):
                    cut = _difference(cut, band)
        except Exception:   # noqa: BLE001 — a part that will not cut is left whole
            out.append(mesh)
            continue
        if cut is poly:
            out.append(mesh)
            continue
        parts = sorted((pg for pg in iter_polygons(cut) if pg.area > _MIN_AREA), key=lambda pg: (pg.bounds[1], pg.bounds[0]))
        for i, pg in enumerate(parts):
            m = dict(mesh)
            m["profile"], m["holes"] = polygon_to_rings(pg)
            if len(parts) > 1:
                m["name"] = "%s%s" % (mesh.get("name", ""), chr(118 + i) if i < 4 else "v%d" % i)   # v, w, x, y
            out.append(m)
    return out


def _on_line(q, v):
    """(how far the point stands in from the valley line, is it an end at the valley)."""
    dist = (q[0] - v["p1"][0]) * v["m"][0] + (q[1] - v["p1"][1]) * v["m"][1]
    dx, dy = (v["p2"][0] - v["p1"][0]) / v["length"], (v["p2"][1] - v["p1"][1]) / v["length"]
    along = (q[0] - v["p1"][0]) * dx + (q[1] - v["p1"][1]) * dy
    return dist, abs(dist) <= EDGE_TOL and -EDGE_TOL <= along <= v["length"] + EDGE_TOL


def valley_parts(meshes, p, infos):
    """Cut the layers under the cladding on each valley's bisecting plane and lay the
    gutter (module docstring). Records each face's valleys on its info ("valleys",
    "valley_strips")."""
    info_by = {i["elevation"]: i for i in infos}
    board, extra = _board(p), []
    for elev in p.get("elevations", []):
        valleys = valleys_of(elev)
        name, frame, info = elev.get("name"), elev.get("frame"), info_by.get(elev.get("name"))
        if not valleys or info is None:
            continue
        mine = [m for m in meshes if m.get("elevation") == name and m.get("ifc_type") not in OWN
                and _same_frame(m["frame"], frame)]
        for v in valleys:
            k, mx, my = v["k"], v["m"][0], v["m"][1]
            for m in mine:
                if m.get("ifc_type") in CLAD or m.get("ridge_part"):
                    continue                                  # the cladding has been stood back already
                mid = float(m["depth"]) + float(m["thickness"]) / 2.0
                rings = [m["profile"]] + list(m.get("holes") or [])
                old = m.get("vshift") or []
                shifts, any_on = [], False
                for j, ring in enumerate(rings):
                    was = old[j] if j < len(old) and old[j] else [[0.0] * 4] * len(ring)
                    row = []
                    for q, sh in zip(ring, was):
                        dist, on = _on_line(q, v)
                        if on:      # onto the line, then in along the bisecting plane as the depth grows
                            any_on = True
                            q[0], q[1] = round(q[0] + (k * mid - dist) * mx, 3), round(q[1] + (k * mid - dist) * my, 3)
                            row.append([sh[0] - k * mid * mx, sh[1] + k * mx, sh[2] - k * mid * my, sh[3] + k * my])
                        else:
                            row.append(list(sh))
                    shifts.append(row)
                if any_on:
                    m["vshift"] = shifts
            face = float(info.get("total_depth") or 0.0)
            wide = v["back"] + VALLEY_LAP                     # the gutter up this slope, from the valley line
            (x1, y1), (x2, y2) = v["p1"], v["p2"]
            info.setdefault("valley_strips", []).append(
                [[x1, y1], [x2, y2], [x2 + wide * mx, y2 + wide * my], [x1 + wide * mx, y1 + wide * my]])
            pitch = [float(elev.get("pitch") or 0.0), math.degrees(math.acos(max(-1.0, min(1.0, abs(v["other_n"][2])))))]
            info.setdefault("valleys", []).append({
                "with": v["with"], "master": v["master"], "length": round(v["length"], 1), "back": round(v["back"], 1),
                "clear": VALLEY_CLEAR, "lap": VALLEY_LAP, "pitches": [round(a, 1) for a in pitch],
                "angle": round(math.degrees(2.0 * math.atan(k)), 1)})
            if v["master"]:
                extra.append(_gutter(p, elev, v, face - board - 1.0, wide))
    return meshes + extra


def _gutter(p, elev, v, s0, wide):
    """The valley gutter: its section in the plane square to the valley line, extruded
    along it. *s0*: the depth it lies at, on the deck under the cladding."""
    name, frame = elev.get("name"), elev["frame"]
    U, V, N = frame["u"], frame_v(frame), frame["n"]
    world = lambda a, b: [a * U[i] + b * V[i] for i in range(3)]          # noqa: E731 — a face direction, in the world
    line = world((v["p2"][0] - v["p1"][0]) / v["length"], (v["p2"][1] - v["p1"][1]) / v["length"])
    up_a, up_b = world(v["m"][0], v["m"][1]), v["other_up"]
    mid = [N[i] + v["other_n"][i] for i in range(3)]
    size = math.sqrt(sum(c * c for c in mid))
    e2 = [c / size for c in mid]                                          # up out of the valley, between the two slopes
    e1 = [e2[1] * line[2] - e2[2] * line[1], e2[2] * line[0] - e2[0] * line[2], e2[0] * line[1] - e2[1] * line[0]]
    flat = lambda g: (sum(a * b for a, b in zip(g, e1)), sum(a * b for a, b in zip(g, e2)))   # noqa: E731
    apex = (0.0, s0 * math.sqrt(1.0 + v["k"] ** 2))
    (ax, ay), (bx, by) = flat(up_a), flat(up_b)
    t = float(p["seam_t"]) if p.get("seam") else 0.7
    path = LineString([(apex[0] + wide * bx, apex[1] + wide * by), apex, (apex[0] + wide * ax, apex[1] + wide * ay)])
    ring = orient(path.buffer(t / 2.0, cap_style=2, join_style=2, mitre_limit=3.0), 1.0)
    gutter = _prism([[round(x, 3), round(y, 3)] for x, y in list(ring.exterior.coords)[:-1]], 0.0, v["length"],
                    {"origin": frame_to_world(frame, v["p1"][0], v["p1"][1], 0.0), "u": e1, "v": e2, "n": line},
                    "valley_gutter", "%s Valley Gutter" % name, name)
    gutter["valley_info"] = {"length": round(v["length"], 1), "width": round(2.0 * wide, 1), "thickness": t,
                             "between": [name, v["with"]]}
    return gutter
