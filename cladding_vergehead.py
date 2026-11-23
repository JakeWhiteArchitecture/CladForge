"""CladForge — a verge between two mono ridges: the mono ridge run on down it.

A raking edge of a roof slope with a monopitch's head verge (cladding_head) at both of its
ends, its own head at the top and another roof's at the foot (the job's short raking edge
of one roof between its own mono ridge and the clerestory's below), is not finished as a
verge (no G3 strip, no ZI-05 flashing): the mono ridge turns the corner and runs down it.

  * **the cap**: the head verge cap's section (cladding_head._cap_ring), laid across a
    slope that is level that way (pitch 0), along the raking edge: its underside the same
    rest off the roof, its drop out past the edge by what the plywood oversails it (the
    cladding on the wall below: cladding_verge.join_verges) and HEAD_CLEAR, down HEAD_DROP;
  * **its ends mitred** onto the head caps at the corners, each cut on the plane that
    bisects the two caps' lines (cladding_verge._cut_end), and those caps cut on the same
    plane at that end, with no stop end there (cladding_verge.close_cap_ends);
  * **the trays' edges turned up** along it, RIDGE_UPSTAND, as at a ridge.

verge_heads finds them, before anything is built, over every face in view whatever its
chain ("verge_caps" on the slope, "head_mitres" on the faces whose caps it meets);
verge_head_parts builds them, group by group, after the head caps.
"""
import math

from cladding_constants import _parse, _prism, _rect, chain_params, frame_to_world, frame_v

VERGE_HEAD_REACH = 400.0    # mm – a head's end this near a verge's end is at it
MITRE_RUN = 400.0           # mm – each cap run on past the corner before it is cut on the mitre


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _unit(a):
    s = math.sqrt(_dot(a, a))
    return [x / s for x in a] if s > 0 else a


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _sides(elev):
    """{"l"/"r": (u, v0, v1)}: the face's plumb side edges at its two ends, from its outline."""
    from cladding_verge import VERGE_RAKE
    edges = []
    for pg in elev.get("polygons") or []:
        ring = pg["exterior"]
        for a, b in zip(ring, ring[1:] + ring[:1]):
            du, dv = abs(a[0] - b[0]), abs(a[1] - b[1])
            if dv >= 300.0 and (du < 0.5 or du <= VERGE_RAKE * dv):
                edges.append((min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1])))
    if not edges:
        return {}
    out = {}
    lo = min(e[0] for e in edges)
    hi = max(e[1] for e in edges)
    for side, u in (("l", lo), ("r", hi)):
        mine = [e for e in edges if abs((e[0] if side == "l" else e[1]) - u) < 1.0]
        if mine:
            out[side] = (u, min(e[2] for e in mine), max(e[3] for e in mine))
    return out


def _plumb_mitre(p, d, q, e):
    """The mitre between two caps meeting at a corner: a plumb plane, bisecting their lines on
    plan (each line through its point, *d* and *e* pointing away from the corner into its
    cap), through the corner where the two lines cross on plan. Plumb, so a cap laid up a
    slope and one laid level are both cut square up their drops, which then meet on one
    plumb line, and neither is cut on a slant. (point, normal)."""
    a = _unit([d[0], d[1], 0.0])
    b = _unit([e[0], e[1], 0.0])
    normal = _unit([a[0] - b[0], a[1] - b[1], 0.0])
    det = a[0] * (-b[1]) - a[1] * (-b[0])
    if abs(det) < 1e-6:                     # in line on plan: halfway, square to them
        corner = [(x + y) / 2.0 for x, y in zip(p, q)]
        return corner, (a if _dot(a, a) > 0 else normal)
    rx, ry = q[0] - p[0], q[1] - p[1]
    t = (rx * (-b[1]) - ry * (-b[0])) / det
    corner = [p[0] + t * a[0], p[1] + t * a[1], (p[2] + q[2]) / 2.0]
    if math.dist(corner[:2], p[:2]) > VERGE_HEAD_REACH or math.dist(corner[:2], q[:2]) > VERGE_HEAD_REACH:
        corner = [(x + y) / 2.0 for x, y in zip(p, q)]
    return corner, normal


def verge_heads(params):
    """Before anything is built: each roof slope's verges with a head verge at both ends
    ("verge_caps", {side: {"u", "v0", "v1", "ends": {"lo"/"hi": {"point", "normal"}}}}), and
    each face whose cap one of them meets told where to mitre it ("head_mitres", [{"span":
    [u0, u1] of its head, "u": the end, "point", "normal"}])."""
    from cladding_head import heads_of
    from cladding_verge import verge_sides
    elevs = [e for e in params.get("elevations") or [] if e.get("ok", True) and e.get("polygons")]
    ends, parsed = [], {}
    for e in elevs:
        e.pop("verge_caps", None)
        e.pop("head_mitres", None)
        try:
            pe = _parse(dict(chain_params(params, e), elevations=[e]))
            heads = heads_of(e, pe)
        except Exception:   # noqa: BLE001 — a face that will not say is left out
            continue
        parsed[e.get("name")] = pe
        U = [float(c) for c in e["frame"]["u"]]
        for h in heads:
            for u, into in ((h["u0"], U), (h["u1"], [-c for c in U])):
                ends.append({"face": e, "head": h, "u": u, "point": frame_to_world(e["frame"], u, h["v"], 0.0), "axis": into})
    for e in elevs:
        pe = parsed.get(e.get("name"))
        if pe is None:
            continue
        sides = verge_sides(e, pe)
        if not sides:
            continue
        V = [float(c) for c in frame_v(e["frame"])]
        for side, (u, v0, v1) in _sides(e).items():
            if side not in sides:
                continue
            found = {}
            for key, v, along in (("lo", v0, V), ("hi", v1, [-c for c in V])):
                P = frame_to_world(e["frame"], u, v, 0.0)
                near = min(ends, key=lambda q: math.dist(q["point"], P), default=None)
                if near is None or math.dist(near["point"], P) > VERGE_HEAD_REACH:
                    break
                corner, normal = _plumb_mitre(P, along, near["point"], near["axis"])
                found[key] = {"point": corner, "normal": normal, "end": near}
            if len(found) < 2:
                continue
            e.setdefault("verge_caps", {})[side] = {"u": u, "v0": v0, "v1": v1,
                                                   "ends": {k: {"point": f["point"], "normal": f["normal"]} for k, f in found.items()}}
            for f in found.values():
                q = f["end"]
                q["face"].setdefault("head_mitres", []).append({"span": [q["head"]["u0"], q["head"]["u1"]], "u": q["u"],
                                                                 "point": f["point"], "normal": f["normal"]})


def verge_head_parts(meshes, p, infos):
    """The caps down each verge between two mono ridges, mitred at both ends; the head caps
    they meet, cut on the same mitres; and the trays' edges turned up under them."""
    from cladding_head import HEAD_CLEAR, HEAD_DROP, _cap_ring
    from cladding_ridge import RIDGE_REST, RIDGE_UPSTAND, _same_frame
    from cladding_verge import _cut_end
    info_by = {i["elevation"]: i for i in infos}
    out = list(meshes)
    t = float(p["seam_t"])
    for elev in p.get("elevations", []):
        name, frame = elev.get("name"), elev.get("frame")
        info = info_by.get(name)
        # the head caps of this face that a raking cap meets: cut on the mitre at that end
        for mit in elev.get("head_mitres") or []:
            caps = [m for m in out if m.get("ifc_type") == "ridge_cap" and m.get("elevation") == name and m.get("head_span")
                    and not m.get("drip_end") and abs(m["head_span"][0] - mit["span"][0]) < 1.0 and abs(m["head_span"][1] - mit["span"][1]) < 1.0]
            for cap in caps:
                f = cap["frame"]
                n = [float(c) for c in f["n"]]
                start = [float(c) for c in f["origin"]]
                far = [a + float(cap["thickness"]) * b for a, b in zip(start, n)]
                at_start = math.dist(start, mit["point"]) < math.dist(far, mit["point"])
                if at_start:
                    cap["frame"] = dict(f, origin=[a - MITRE_RUN * b for a, b in zip(start, n)])
                cap["thickness"] = float(cap["thickness"]) + MITRE_RUN
                pieces = _cut_end(cap, [(None, mit["point"], mit["normal"], "Mitre")], at_start=at_start)
                if pieces:
                    for piece in pieces:
                        if not piece.get("drip_end"):
                            piece["mitred_ends"] = sorted(set((piece.get("mitred_ends") or []) + [0 if at_start else 1]))
                    i = out.index(cap)
                    out[i:i + 1] = pieces
        caps = elev.get("verge_caps") or {}
        if not caps or info is None:
            continue
        face = float(info.get("total_depth") or 0.0)
        rest = face + float(p["seam_h"]) + RIDGE_REST
        U, V, N = [float(c) for c in frame["u"]], [float(c) for c in frame_v(frame)], [float(c) for c in frame["n"]]
        for side, vc in caps.items():
            u, v0, v1 = vc["u"], vc["v0"], vc["v1"]
            over = float((elev.get("verge_over") or {}).get(side) or 0.0)
            ring, width = _cap_ring(0.0, rest, over + HEAD_CLEAR, -HEAD_DROP, t)
            inward = U if side == "l" else [-c for c in U]
            axis = _cross(inward, N)
            up = _dot(axis, V) > 0
            v_start = v0 - MITRE_RUN if up else v1 + MITRE_RUN
            length = v1 - v0 + 2.0 * MITRE_RUN
            place = {"origin": frame_to_world(frame, u, v_start, 0.0), "u": inward, "v": N, "n": axis}
            cap = _prism(ring, 0.0, length, place, "ridge_cap", "%s Verge Head Cap %s" % (name, side.upper()), name)
            cap["verge_head"] = True
            pieces = [cap]
            for key, first in (("lo", up), ("hi", not up)):
                mit = vc["ends"][key]
                main = next(m for m in pieces if not m.get("drip_end"))
                cut = _cut_end(main, [(None, mit["point"], mit["normal"], "Mitre")], at_start=first)
                if cut:
                    i = pieces.index(main)
                    pieces[i:i + 1] = cut
            out += pieces
            # the trays' edges along it turned up, as at a ridge
            for m in meshes:
                if m.get("elevation") != name or m.get("ifc_type") != "plank" or not _same_frame(m["frame"], frame):
                    continue
                vs = [q[1] for q in m["profile"] if abs(q[0] - u) < 2.0]
                if len(vs) < 2 or max(vs) - min(vs) < 20.0:
                    continue
                a, b = (u - t, u) if side == "r" else (u, u + t)
                upst = _prism(_rect(a, min(vs) + 3.0, b, max(vs) - 3.0), float(m["depth"]) + float(m["thickness"]), RIDGE_UPSTAND,
                              m["frame"], "seam", "%s Verge Upstand" % m.get("name", "Tray"), name)
                upst.update(opacity=1.0, ridge_part=True)
                out.append(upst)
            info.setdefault("verge_caps", []).append({"side": side, "length": round(v1 - v0, 1), "over": round(over, 1),
                                                      "width": round(width, 1), "drop": HEAD_DROP})
    return out
