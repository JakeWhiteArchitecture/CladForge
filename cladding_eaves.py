"""CladForge — the eaves: the level bottom edge of a roof slope clad in standing seam trays
that run with the fall.

No drawing of the eaves was supplied with the others (ridge, verge, valley), so this is the
usual standing seam eaves in the same manner as cladding_verge's strip, and every size
here is assumed: agree them against the supplier's eaves detail. The face's bottom edge is
taken as the outer face of the roof's edge (the fascia, or the back of the gutter).

  * The eaves apron: a folded sheet lying on the deck under the trays, EAVES_LAP up the
    slope from the edge, its nose EAVES_NOSE out beyond it, turned back under, then down
    EAVES_CLEAR clear of the edge's face for EAVES_DROP, with a drip kicked out at its foot.
  * The trays, and their seams, run out over the apron to EAVES_TRAY beyond the edge, where
    each is welted round the apron's nose. Their outlines are lengthened, so the cutting
    list has a tray as long as it is on the roof; the welt turned under is not added. A
    seam ends there as the chain's bottom seam end says (folded, or flat).
  * Everything under the trays (deck, battens) runs to the roof's edge. The chain's bottom
    edge offset does not apply at an eaves.

Whether a chain's slopes get one is its "eaves" setting: on, or none — a bottom edge that
runs into a wall, a parapet gutter or another roof is not an eaves and has to be switched
off. Only a level edge at least 300 long is one: a raking bottom edge is a valley.

Not modelled: the gutter and its brackets, the fascia, the continuous clip some systems
fix under the apron, the tray's welt, the apron's laps and its ends at a verge or a hip."""

from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient

from cladding_constants import _prism, frame_to_world, frame_v
from cladding_edges import settings as edge_settings
from cladding_ridge import _same_frame, _up_slope
from cladding_verge import ROOF_PITCH, VERGE_TRAY

EAVES_NOSE = 35.0     # mm – the apron's nose, out beyond the roof's edge (assumed)
EAVES_TRAY = 40.0     # mm – the trays' ends beyond the roof's edge, round the nose (assumed)
EAVES_LAP = 100.0     # mm – the apron on the deck, up from the edge (assumed)
EAVES_CLEAR = 10.0    # mm – the apron's drop, clear of the edge's face (assumed)
EAVES_DROP = 50.0     # mm – the drop, down from the deck (assumed)
EAVES_KICK = 10.0     # mm – the drip kicked out at its foot (assumed)
EAVES_MIN = 300.0     # mm – a level bottom edge shorter than this is not an eaves
EDGE_TOL = 12.0       # mm – an end this close to where the bottom offset left it is an end at the eaves
OWN = ("ridge_cap", "ridge_filler", "verge_strip", "valley_gutter", "eaves_strip")


def has_eaves(elev, p):
    """Does this face take an eaves apron: a roof slope that looks up, clad in trays that
    run with the fall, its chain's eaves not switched off."""
    pitch = elev.get("pitch")
    if not _up_slope(p) or pitch is None or float(pitch) > ROOF_PITCH or elev.get("faces_down"):
        return False
    return (elev.get("eaves") or "on") != "none"


def _edges(info):
    """[(u0, u1, v)]: the level bottom edges of the clad area, those at least EAVES_MIN long."""
    out = []
    for e in info.get("edges") or []:
        if e["kind"] == "bottom" and abs(e["p1"][1] - e["p2"][1]) < 0.5:
            u0, u1 = sorted((e["p1"][0], e["p2"][0]))
            if u1 - u0 >= EAVES_MIN:
                out.append((u0, u1, (e["p1"][1] + e["p2"][1]) / 2.0))
    return sorted(out)


def _apron_ring(y0, t):
    """The apron's section: x out from the roof's edge, y the depth from the face. On the
    deck at depth y0, out to the nose, back under it, down the drop, the drip kicked out."""
    g = 2.5 * t
    x = EAVES_CLEAR + t
    path = LineString([(-EAVES_LAP, y0), (EAVES_NOSE, y0), (EAVES_NOSE, y0 - g), (x, y0 - g),
                       (x, y0 - EAVES_DROP), (x + EAVES_KICK, y0 - EAVES_DROP - EAVES_KICK)])
    return list(path.buffer(t / 2.0, cap_style=2, join_style=2, mitre_limit=3.0).exterior.coords)[:-1]


def _lower(q, others, v):
    """Bring a bottom vertex to the level *v*, sliding it along its other edge: straight
    down a tray's side, and on down the rake of an end cut to a hip or a valley."""
    for r in others:
        du, dv = q[0] - r[0], q[1] - r[1]
        if dv < -1.0 and abs(du) < 3.0 * abs(dv):          # an edge coming down to it, not too flat
            q[0] = round(q[0] + du / dv * (v - q[1]), 3)
            break
    q[1] = round(v, 3)


def eaves_parts(meshes, p, infos):
    """Run the trays out at each eaves, the layers under them to the edge, and add the
    apron. Records each face's eaves on its info ("eaves", "eaves_strips")."""
    info_by = {i["elevation"]: i for i in infos}
    extra = []
    for elev in p.get("elevations", []):
        name, frame, info = elev.get("name"), elev["frame"], info_by.get(elev.get("name"))
        if info is None or not has_eaves(elev, p):
            continue
        mine = [m for m in meshes if m.get("elevation") == name and m.get("ifc_type") not in OWN
                and _same_frame(m["frame"], frame)]
        trays = [m for m in mine if m.get("ifc_type") == "plank"]
        if not trays:
            continue
        back, t = min(float(m["depth"]) for m in trays), float(p["seam_t"])
        tol = edge_settings(elev)["bottom"] + EDGE_TOL
        reach = VERGE_TRAY + 5.0            # a tray widened over a verge strip is still at the eaves
        V, U, N = frame_v(frame), frame["u"], frame["n"]
        toward = [-float(c) for c in V]                                    # out over the edge: down the slope
        along = [float(c) for c in U]
        across = [along[1] * toward[2] - along[2] * toward[1], along[2] * toward[0] - along[0] * toward[2],
                  along[0] * toward[1] - along[1] * toward[0]]
        sign = 1.0 if sum(a * b for a, b in zip(across, N)) > 0 else -1.0     # which way the section's y runs
        section = orient(Polygon([(x, sign * y) for x, y in _apron_ring(back - 1.0, t)]), 1.0)
        section = [[round(x, 3), round(y, 3)] for x, y in list(section.exterior.coords)[:-1]]
        for n, (u0, u1, v) in enumerate(_edges(info)):
            for m in mine:
                out = v - EAVES_TRAY if m.get("ifc_type") in ("plank", "seam") else v
                pts = m["profile"]
                at = [u0 - reach <= q[0] <= u1 + reach and v - 1.0 <= q[1] <= v + tol for q in pts]
                for i, q in enumerate(pts):
                    if at[i]:
                        _lower(q, [pts[j] for j in ((i - 1) % len(pts), (i + 1) % len(pts)) if not at[j]], out)
            apron = _prism([list(q) for q in section], 0.0, u1 - u0, {"origin": frame_to_world(frame, u0, v, 0.0), "u": toward, "v": across, "n": along},
                           "eaves_strip", "%s Eaves Apron %d" % (name, n + 1), name)
            apron["eaves_info"] = {"length": round(u1 - u0, 1), "lap": EAVES_LAP, "nose": EAVES_NOSE, "drop": EAVES_DROP,
                                   "clear": EAVES_CLEAR, "tray": EAVES_TRAY, "thickness": t}
            extra.append(apron)
            info.setdefault("eaves", []).append(dict(apron["eaves_info"], u0=round(u0, 1), u1=round(u1, 1), v=round(v, 1)))
            info.setdefault("eaves_strips", []).append([[u0, v - EAVES_NOSE], [u1, v - EAVES_NOSE], [u1, v + EAVES_LAP], [u0, v + EAVES_LAP]])
    return meshes + extra
