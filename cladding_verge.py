"""CladForge — the verge: the free side edge of a roof slope clad in standing seam trays
that run with the fall.

After VMZINC's standard detail "Verge junction G3" (VMZ-SN-MA-ZI-G3-01). The face's side
edge is taken as the outer face of the roof's edge (the detail's vertical plywood).

  * The verge strip: a folded sheet lying on the deck under the last tray, VERGE_LAP in
    from the edge, its nose VERGE_NOSE out beyond it, then turned down VERGE_CLEAR clear
    of the edge's face for VERGE_DROP and hemmed back at the foot.
  * The last tray runs out over the strip to VERGE_TRAY beyond the edge, where it is
    welted round the strip's nose. Its outline is widened, so the cutting list has it as
    wide as it is on the roof; the welt turned under the nose is not added.
  * The last seam stands between 150 and 370 mm from the tray's edge (the detail's rule),
    reported per verge and checked.

Only the clearance of the drop and the seam's distance are dimensioned on the detail; the
strip's other sizes are scaled from it. Which sides of a chain get a verge is the chain's
"verge" setting: both, the start of the run (the left of its first face), the end, or
none — a side that runs into a wall is not a verge and has to be switched off.

The alternative, where the roof's edge stands over a clad wall, is VMZINC's "Verge with
cladding" (VMZ-SN-MA-ZI-05), the chain's "verge_type" set to "cladding":

  * **The edge is a standing seam, the same closed double lock as between two trays**
    (_edge_seam). The last tray runs VERGE_FACE less the seam's air beyond the roof's edge
    and turns up as the undercloak, the head on the roof's side.
  * **The verge flashing is the seam's other sheet**, the overcloak: from the seam it runs
    straight down the face of the edge, past the plywood and the ventilation gap under it,
    to cover the head of the wall's cladding by VERGE_COVER (60, dimensioned). Its foot
    is hemmed inward. Each sheet of the seam is marked as its owner's, the tray's or the
    flashing's ("of").
  * The continuous folded strip is fixed down the face of the edge behind the flashing,
    its foot kicked out into the flashing's hem to hold it.

Only the 60 is dimensioned on that sheet; the gap under the plywood and the flashing's
stand-off are scaled from it. No tray is widened and the 150 to 370 rule does not apply:
the last tray is a tray, between two seams.

Not modelled: the vertical plywood and the timber behind it, the fixings, the lift the
strip gives the tray's edge, the insect mesh, and the wall's cladding stopping under the
roof's plywood (heal the wall up to the roof for that)."""

from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient

from cladding_constants import SEAM_AIR, _prism, frame_to_world, frame_v
from cladding_ridge import ridge_of, _slot, _same_frame, _up_slope

VERGE_NOSE = 36.0     # mm – the strip's nose, out beyond the roof's edge (scaled)
VERGE_TRAY = 40.0     # mm – the tray's edge beyond the roof's edge, round the nose (scaled)
VERGE_LAP = 70.0      # mm – the strip on the deck, in from the edge (scaled)
VERGE_CLEAR = 10.0    # mm – the strip's drop, clear of the edge's face (dimensioned)
VERGE_DROP = 65.0     # mm – the drop, down from the deck (scaled)
VERGE_HEM = 16.0      # mm – the hem at its foot (scaled)
VERGE_SEAM = (150.0, 370.0)   # mm – the last seam, in from the tray's edge (dimensioned)
VERGE_COVER = 60.0    # mm – ZI-05: the flashing over the head of the wall's cladding (dimensioned)
VERGE_GAP = 25.0      # mm – ZI-05: the ventilation gap under the roof's plywood (scaled)
VERGE_FACE = 5.0      # mm – ZI-05: the flashing, clear of the edge's face (scaled)
VERGE_LOCK = 10.0     # mm – ZI-05: the flashing turned down inside the seam (scaled)
VERGE_TYPES = ("g3", "cladding")
ROOF_PITCH = 75.0     # deg – steeper than this a face is a wall, and has no verge
EDGE_TOL = 12.0       # mm – a tray edge this close to the roof's edge is the edge


def verge_sides(elev, p):
    """The sides of this face that take a verge: a subset of ("l", "r"). None on a wall,
    a face that looks down, or anything but trays running with the fall."""
    pitch = elev.get("pitch")
    if not _up_slope(p) or pitch is None or float(pitch) > ROOF_PITCH or elev.get("faces_down"):
        return ()
    want = elev.get("verge") or "both"
    if want == "none":
        return ()
    start, end = ("r", "l") if elev.get("chain_reversed") else ("l", "r")
    return {"both": (start, end), "start": (start,), "end": (end,)}.get(want, (start, end))


def verge_type(elev):
    """Which verge this face's chain takes: "g3" (the strip, VMZ-SN-MA-ZI-G3-01) or
    "cladding" (the flashing down over a clad wall, VMZ-SN-MA-ZI-05)."""
    want = elev.get("verge_type")
    return want if want in VERGE_TYPES else "g3"


def _edges(info):
    """{"l": (u, v0, v1), "r": ...}: the straight side edges at the two ends of the clad
    area, those at least 300 long. A raking side (a hip) is not a verge."""
    sides = [e for e in info.get("edges") or [] if e["kind"] == "side" and abs(e["p1"][0] - e["p2"][0]) < 0.5]
    if not sides:
        return {}
    us = [e["p1"][0] for e in sides]
    out = {}
    for side, u in (("l", min(us)), ("r", max(us))):
        vs = [v for e in sides if abs(e["p1"][0] - u) < 1.0 for v in (e["p1"][1], e["p2"][1])]
        if vs and max(vs) - min(vs) >= 300.0:
            out[side] = (u, min(vs), max(vs))
    return out


def _strip_ring(y0, t):
    """The strip's section: x out from the roof's edge, y the depth from the face. On the
    deck at depth y0, out to the nose, back under it, down the drop and hemmed inward."""
    g = 2.5 * t
    x = VERGE_CLEAR + t
    path = LineString([(-VERGE_LAP, y0), (VERGE_NOSE, y0), (VERGE_NOSE, y0 - g), (x, y0 - g),
                       (x, y0 - VERGE_DROP), (x - g, y0 - VERGE_DROP), (x - g, y0 - VERGE_DROP + VERGE_HEM)])
    return list(path.buffer(t / 2.0, cap_style=2, join_style=2, mitre_limit=3.0).exterior.coords)[:-1]


def verge_seam_out():
    """How far beyond the roof's edge the last tray runs to its seam at a verge with cladding:
    the seam stands over the flashing, VERGE_FACE clear of the edge's face."""
    return VERGE_FACE - SEAM_AIR


def _flashing_rings(y0, top, drop, t):
    """ZI-05's two sheets below the seam, in section (x out from the roof's edge, y the
    depth from the face): the flashing, straight down from the foot of its upstand in the
    seam (at *top*, the trays' face) to *drop* below the deck's top *y0*, hemmed inward;
    and the continuous strip behind it, down the edge's face, its foot kicked out into
    that hem."""
    g = 2.5 * t
    x = VERGE_FACE + t / 2.0                 # in line with its upstand in the seam above
    foot = y0 - drop
    flashing = LineString([(x, top), (x, foot), (x - g, foot), (x - g, foot + VERGE_HEM)])
    e = t / 2.0 + 0.5
    clip = LineString([(e, y0 - 2.0), (e, foot + VERGE_HEM + 12.0), (x - g / 2.0, foot + VERGE_HEM), (x - g / 2.0, foot + t)])
    ring = lambda path: list(path.buffer(t / 2.0, cap_style=2, join_style=2, mitre_limit=3.0).exterior.coords)[:-1]   # noqa: E731
    return ring(flashing), ring(clip)


def _edge_seam(p, frame, name, letter, u, out, v0, v1, face, tray, flashing):
    """The seam at a verge with cladding: **the same closed double lock as between two
    trays** (cladding_geometry._double_lock), sheet by sheet, its line SEAM_AIR / 2 beyond
    the last tray's edge. The tray turns up the undercloak, on the roof's side, and the
    head hangs on that side; the flashing is the overcloak, on the outside. Each sheet is
    marked as its owner's ("of"): the tray's, or the flashing's."""
    from cladding_constants import _rect
    from cladding_geometry import _double_lock, SEAM_PARTS
    line = u + out * (verge_seam_out() + SEAM_AIR / 2.0)
    parts = []
    for part, side, c0, c1, z0, z1 in _double_lock(p["seam_t"], p["seam_h"]):
        if out > 0:                          # the right verge: mirrored, so the head is still on the roof's side
            c0, c1 = -c1, -c0
        mine = side == "+"                   # the undercloak: the tray's own sheet
        owner = tray if mine else flashing
        title = "%s Verge Seam %s" % (tray, SEAM_PARTS[part]) if mine else "%s Verge Seam %s %s" % (name, letter, SEAM_PARTS[part])
        m = _prism(_rect(line + c0, v0, line + c1, v1), face + z0, z1 - z0, frame, "seam", title, name)
        m.update(seam_part=part, seam_side=side, seam_v=True, seam_line=line, seam_face=face, verge_seam=True, of=owner)
        parts.append(m)
    return parts


def verge_parts(meshes, p, infos):
    """Widen the last tray at each verge and add the strip under it. Records each face's
    verges on its info ("verges", "verge_strips")."""
    info_by = {i["elevation"]: i for i in infos}
    extra = []
    for elev in p.get("elevations", []):
        name, frame, info = elev.get("name"), elev["frame"], info_by.get(elev.get("name"))
        sides = verge_sides(elev, p)
        if not sides or info is None:
            continue
        mine = [m for m in meshes if m.get("elevation") == name and _same_frame(m["frame"], frame)]
        trays = [m for m in mine if m.get("ifc_type") == "plank"]
        lines = sorted({float(m["seam_line"]) for m in mine if m.get("seam_part") and m.get("seam_v")})
        if not trays:
            continue
        back, t = min(float(m["depth"]) for m in trays), float(p["seam_t"])
        r = ridge_of(elev)
        found = _edges(info)
        for side in sides:
            if side not in found:
                continue
            u, v0, v1 = found[side]
            out = -1.0 if side == "l" else 1.0
            kind = verge_type(elev)
            decks = [m for m in mine if m.get("ifc_type") == "deck"]
            deck_t = max((float(m["thickness"]) for m in decks), default=18.0)
            drop = VERGE_DROP if kind == "g3" else deck_t + VERGE_GAP + VERGE_COVER
            if kind == "g3":
                for m in trays + [m for m in mine if m.get("ridge_part")]:
                    for q in m["profile"]:
                        if abs(q[0] - u) <= EDGE_TOL and v0 - 1.0 <= q[1]:
                            q[0] = round(u + out * VERGE_TRAY, 3)
            if r:       # at a ridge: square-ended, its foot no nearer the bisecting plane than the trays come
                v1 = _slot(r) + r["k"] * (back - 1.0 - drop)
            V, U, N = frame_v(frame), frame["u"], frame["n"]
            toward = [out * c for c in U]
            across = [V[1] * toward[2] - V[2] * toward[1], V[2] * toward[0] - V[0] * toward[2], V[0] * toward[1] - V[1] * toward[0]]
            sign = 1.0 if sum(a * b for a, b in zip(across, N)) > 0 else -1.0     # which way the section's y runs
            place = lambda: {"origin": frame_to_world(frame, u, v0, 0.0), "u": toward, "v": across, "n": [float(c) for c in V]}   # noqa: E731
            tidy = lambda pts: [[round(x, 3), round(y, 3)] for x, y in                               # noqa: E731
                                list(orient(Polygon([(x, sign * y) for x, y in pts]), 1.0).exterior.coords)[:-1]]
            letter = "L" if side == "l" else "R"
            seam = min((abs(x - u) for x in lines if abs(x - u) > EDGE_TOL), default=None)
            if kind == "cladding":
                face = back + t                        # the trays' face: where a seam's sheets start
                flashing, clip = _flashing_rings(back - 1.0, face, drop, t)
                strip = _prism(tidy(flashing), 0.0, v1 - v0, place(), "verge_strip", "%s Verge Flashing %s" % (name, letter), name)
                extra.append(_prism(tidy(clip), 0.0, v1 - v0, place(), "verge_strip", "%s Verge Clip %s" % (name, letter), name))
                edge = [m for m in trays if any(abs(q[0] - u) <= EDGE_TOL for q in m["profile"])]
                for m in edge + [m for m in mine if m.get("ridge_part")]:      # the last tray runs on to its seam, just beyond the edge
                    for q in m["profile"]:
                        if abs(q[0] - u) <= EDGE_TOL and v0 - 1.0 <= q[1]:
                            q[0] = round(u + out * verge_seam_out(), 3)
                extra.extend(_edge_seam(p, frame, name, letter, u, out, v0, v1, face, edge[0]["name"] if edge else strip["name"], strip["name"]))
                strip["verge_info"] = {"side": side, "type": kind, "length": round(v1 - v0, 1), "lap": 0.0, "nose": VERGE_FACE,
                                       "drop": round(drop, 1), "clear": VERGE_FACE, "cover": VERGE_COVER, "thickness": t,
                                       "last_seam": None, "last_tray": None if seam is None else round(seam + verge_seam_out(), 1)}
            else:
                strip = _prism(tidy(_strip_ring(back - 1.0, t)), 0.0, v1 - v0, place(),
                               "verge_strip", "%s Verge Strip %s" % (name, letter), name)
                strip["verge_info"] = {"side": side, "type": kind, "length": round(v1 - v0, 1), "lap": VERGE_LAP, "nose": VERGE_NOSE,
                                       "drop": VERGE_DROP, "clear": VERGE_CLEAR, "thickness": t,
                                       "last_seam": None if seam is None else round(seam + VERGE_TRAY, 1)}
            extra.append(strip)
            info.setdefault("verges", []).append(dict(strip["verge_info"], u=round(u, 1)))
            a, b = sorted((u - out * strip["verge_info"]["lap"], u + out * (strip["verge_info"]["nose"] + t)))
            info.setdefault("verge_strips", []).append([[a, v0], [b, v0], [b, v1], [a, v1]])
    return meshes + extra
