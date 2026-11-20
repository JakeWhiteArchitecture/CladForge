"""CladForge — windows and doors in standing seam cladding: the sill and the head.

After VMZINC's standard detail "Junctions with window" (VMZ-SN-MA-RA-07), for every opening
of an upright wall clad in standing seam:

  * **The sill**: a plywood sill board on the structural sill, falling outward at 1 degree
    (the sheet's minimum) from the frame to the front of the wall's plywood. **The capping
    piece** lies on it from the frame, where it is turned up, out over the front, and drops
    in front of the cladding below to cover its head by SILL_COVER (60, dimensioned),
    clear of the tips of that cladding's seams and hemmed at its foot. **The continuous
    folded strip** under its front edge holds it.
  * **The head**: **the continuous strip** behind the foot of the cladding over the opening,
    which that cladding's sheet is welted round: it hangs HEAD_DRIP below the trays' feet
    as the drip. And **the continuous welt piece** under the head lining, WELT_BACK out from
    the frame (the sheet's 10 mm), which the lining's inner edge is welted into.
  * **The jambs** stay as the chain's reveal detail has them: the seam stood on each
    reveal corner, the lining its second tray (cladding_geometry). The sheet's own jamb, a
    welted corner on a sheet clip, is not drawn.

Only the 60 mm, the 10 mm and the 1 degree are on the sheet; the other sizes are scaled from
it. Whether a chain's openings get them is its "window_flashings" setting: on, or none.

Not modelled: the insect mesh, the mastic at the frame, the sheet clips, the capping's
ends turned up against the jambs."""

import math

from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient

from cladding_constants import _prism, frame_to_world, frame_v, world_to_frame

SILL_FALL = 1.0        # deg – the sill board's fall outward (the sheet's minimum)
SILL_BOARD = 18.0      # mm – the plywood sill board (scaled)
SILL_COVER = 60.0      # mm – the capping's drop over the head of the cladding below (dimensioned)
SILL_GAP = 10.0        # mm – taken between the sill and the cladding below where none is found there
SILL_CLEAR = 5.0       # mm – the drop, clear of the tips of the cladding's seams (scaled)
SILL_UP = 15.0         # mm – the capping turned up against the frame (scaled)
SILL_HEM = 12.0        # mm – the hem at the drop's foot (scaled)
SILL_STRIP = (30.0, 20.0)   # mm – the folded strip: on the board back from its front, and down its face (scaled)
HEAD_DRIP = 20.0       # mm – the head strip, below the feet of the trays over the opening (scaled)
HEAD_UP = 40.0         # mm – and up behind them (scaled)
WELT_BACK = 10.0       # mm – the welt piece, out from the frame (dimensioned)
WELT_W = 40.0          # mm – its width under the head lining (scaled)
WALL_PITCH = 75.0      # deg – flatter than this a face is a roof: its openings are rooflights, not these
MIN_W = 100.0          # mm – an opening narrower than this gets none


def has_window_flashings(elev, p):
    """Do this face's openings take the sill and head of RA-07? Only an upright wall in
    standing seam, unless its chain says none."""
    pitch = elev.get("pitch")
    if not p.get("seam") or elev.get("window_flashings") == "none":
        return False
    return pitch is None or float(pitch) >= WALL_PITCH


def _sheet(path, t):
    return list(LineString(path).buffer(t / 2.0, cap_style=2, join_style=2, mitre_limit=3.0).exterior.coords)[:-1]


def sill_rings(fd, back, face, seam_h, t, foot):
    """The sill in section, as (board, capping, strip): x the depth from the wall's face
    (out is positive), y the height above the structural sill. *fd*: the frame's depth;
    *back*, *face*: the back and the front of the wall's trays; *foot*: the capping's foot."""
    fall = math.tan(math.radians(SILL_FALL))
    top = lambda x: SILL_BOARD - (x - fd) * fall                               # noqa: E731 — the board's top
    board = [(fd, 0.0), (back, 0.0), (back, top(back)), (fd, top(fd))]
    xd = face + seam_h + SILL_CLEAR + t / 2.0                                   # the drop's line, outside the seams' tips
    g = 2.5 * t
    lift = 1.5 * t                                                              # the capping lies on the strip, a sheet up
    cap = [(fd + t / 2.0, top(fd) + SILL_UP), (fd + t / 2.0, top(fd) + lift), (xd, top(xd) + lift), (xd, foot),
           (xd - g, foot), (xd - g, foot + SILL_HEM)]
    xs = xd - t - 1.0                                                           # the strip's nose, inside the drop
    strip = [(back - SILL_STRIP[0], top(back - SILL_STRIP[0]) + t / 2.0), (xs, top(xs) + t / 2.0), (xs, top(back) - SILL_STRIP[1])]
    return board, _sheet(cap, t), _sheet(strip, t)


def head_rings(fd, back, t, feet, lining):
    """The head in section, as (strip, welt piece or None): y the height above the
    structural head. *feet*: where the trays over the opening end; *lining*: the underside
    of the head lining, or None where there is none."""
    x = back - 1.0 - t / 2.0                                                    # behind the trays, on the plywood
    strip = [(x, feet + HEAD_UP), (x, feet - HEAD_DRIP + 6.0), (x + 8.0, feet - HEAD_DRIP)]
    welt = None
    if lining is not None:
        y = lining - t
        welt = [(fd + WELT_BACK + WELT_W, y), (fd + WELT_BACK, y), (fd + WELT_BACK, y - 8.0)]
    return _sheet(strip, t), (_sheet(welt, t) if welt else None)


def window_parts(meshes, p, infos):
    """Add the sill and head of every opening of a standing seam wall. Records them on the
    face's info ("windows": one per opening, and "window_strips": what shows of them in
    elevation)."""
    info_by = {i["elevation"]: i for i in infos}
    extra = []
    for elev in p.get("elevations", []):
        name, frame, info = elev.get("name"), elev["frame"], info_by.get(elev.get("name"))
        if info is None or not has_window_flashings(elev, p):
            continue
        same = lambda f: all(abs(a - b) < 1e-6 for k in ("origin", "u", "n") for a, b in zip(f[k], frame[k]))   # noqa: E731
        mine = [m for m in meshes if m.get("elevation") == name]
        trays = [m for m in mine if m.get("ifc_type") == "plank" and same(m["frame"])]
        if not trays:
            continue
        back = min(float(m["depth"]) for m in trays)
        face = max(float(m["depth"]) + float(m["thickness"]) for m in trays)
        t, seam_h = float(p["seam_t"]), float(p["seam_h"])
        V, U, N = [float(c) for c in frame_v(frame)], [float(c) for c in frame["u"]], [float(c) for c in frame["n"]]
        across = [U[1] * N[2] - U[2] * N[1], U[2] * N[0] - U[0] * N[2], U[0] * N[1] - U[1] * N[0]]
        sign = 1.0 if sum(a * b for a, b in zip(across, V)) > 0 else -1.0      # which way the section's y runs
        tidy = lambda pts: [[round(x, 3), round(y, 3)] for x, y in                               # noqa: E731
                            list(orient(Polygon([(x, sign * y) for x, y in pts]), 1.0).exterior.coords)[:-1]]
        for k, d in enumerate(info.get("opening_details") or []):
            u0, u1, v0, v1 = [float(c) for c in d["rect"]]
            if u1 - u0 < MIN_W:
                continue
            fd = float(d.get("frame_depth") if d.get("frame_depth") is not None else -50.0)
            fd = min(fd, back - 20.0)                              # a frame forward of the cladding: the sill still has a board
            over = lambda q: u0 - 1.0 <= q[0] <= u1 + 1.0                                       # noqa: E731
            below = [q[1] for m in trays for q in m["profile"] if over(q) and q[1] <= v0 + 1.0]
            above = [q[1] for m in trays for q in m["profile"] if over(q) and q[1] >= v1 - 1.0]
            door = not below or max(below) < v0 - 600.0            # nothing clad under it: a door, or a window to the ground
            place = lambda v: {"origin": frame_to_world(frame, u0, v, 0.0), "u": N, "v": across, "n": U}   # noqa: E731
            made = {"opening": k + 1, "rect": [u0, u1, v0, v1], "sill": None, "head": None}
            if not door:
                foot = (max(below) - v0) - SILL_COVER if max(below) > v0 - 200.0 else -(SILL_GAP + SILL_COVER)
                board, cap, strip = sill_rings(fd, back, face, seam_h, t, foot)
                extra.append(_prism(tidy(board), 0.0, u1 - u0, place(v0), "sill_board", "%s Sill Board %d" % (name, k + 1), name))
                for ring, what in ((strip, "Sill Strip"), (cap, "Sill Capping")):
                    extra.append(_prism(tidy(ring), 0.0, u1 - u0, place(v0), "window_flashing", "%s %s %d" % (name, what, k + 1), name))
                made["sill"] = {"fall": SILL_FALL, "cover": SILL_COVER, "foot": round(v0 + foot, 1),
                                "out": round(face + seam_h + SILL_CLEAR + t, 1), "length": round(u1 - u0, 1)}
                info.setdefault("window_strips", []).append([[u0, v0 + foot], [u1, v0 + foot], [u1, v0 + SILL_BOARD], [u0, v0 + SILL_BOARD]])
            if above:
                feet = min(above) - v1
                head = next((m for m in mine if m.get("ifc_type") == "reveal" and m.get("name") == "%s Reveal %dH" % (name, k + 1)), None)
                lining = None
                if head is not None:                               # its top face is on its frame's origin, looking down
                    lining = world_to_frame(frame, head["frame"]["origin"])[1] - float(head["thickness"]) - v1
                strip, welt = head_rings(fd, back, t, feet, lining)
                extra.append(_prism(tidy(strip), 0.0, u1 - u0, place(v1), "window_flashing", "%s Head Strip %d" % (name, k + 1), name))
                if welt:
                    extra.append(_prism(tidy(welt), 0.0, u1 - u0, place(v1), "window_flashing", "%s Head Welt %d" % (name, k + 1), name))
                made["head"] = {"drip": HEAD_DRIP, "foot": round(v1 + feet - HEAD_DRIP, 1), "welt": welt is not None, "length": round(u1 - u0, 1)}
                info.setdefault("window_strips", []).append([[u0, v1 + feet - HEAD_DRIP], [u1, v1 + feet - HEAD_DRIP], [u1, v1 + feet], [u0, v1 + feet]])
            if made["sill"] or made["head"]:
                info.setdefault("windows", []).append(made)
    return meshes + extra
