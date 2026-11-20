"""CladForge — the raking corner: the corner a leaning wall turns with another wall.

Two upright walls meet on a plumb line and are a chain corner (cladding_primitives). When
one or both of them lean, the planes meet on a line that is not plumb: on a leaning wall
it is still the end of the face, but on an upright wall beside it the corner is a raking
edge. Each face of the pair carries a record among its "valleys" (kind "rake":
fabric_extract.rake_link, kept by the app), so the two keep their own chains and settings.
Its *k* is tan of half the angle between the two normals, negative at an internal corner.

What is built follows the job's corner detail, as far as it can on a raking line:

  * Mitred (and Master and Square, which are not drawn on a raking corner): every layer is
    cut to the plane that bisects the corner. The boards stop half the chain's mitre gap
    short of it, square to the plane; the battens are cut square behind them; sheathing
    and insulation meet on the plane. At an internal corner the same plane cuts everything
    back from the corner line.
  * Profile, at an external right-angled corner: with panels the outer corner profile
    (a Rockpanel Profile D type), with plank boards the Marley-type corner profile, run up
    the raking line. The boards stop square against it, the profile gap short of its nose,
    exactly as at an upright corner. One profile per corner, carried by the corner's
    master. Anywhere else Profile falls back to the mitre.

Each cut end slides along the part's own edge (cladding_hip.cut_to_lines), so a board
keeps its width and its joints stay where they were set out.

Not modelled: the solid L of timber behind a profile at an upright corner (the battens
are cut square to the plane instead), a standing seam's corner seam or flashing on a
raking line (the trays are mitred, and the check says to agree it)."""

import math

from cladding_constants import frame_to_world, frame_v
from cladding_corners import profile_mesh, TRIM_BACK, TRIM_LEG
from cladding_edges import settings as edge_settings
from cladding_hip import cut_to_lines
from cladding_primitives import corner_detail
from cladding_ridge import RIDGE_OVERSAIL, MEET, CLAD, _board
from cladding_valley import valleys_of


def rakes_of(elev):
    """The face's raking corners, as cladding_valley.valleys_of gives its valleys; "back" is
    how far a mitred board's end stands in from the corner line at the face (depth 0)."""
    out = valleys_of(elev, "rake")
    gap = edge_settings(elev)["gap"]
    for r in out:
        r["back"] = gap / 2.0 * math.sqrt(1.0 + r["k"] ** 2)
    return out


def rake_asked(p, r):
    """The corner detail asked for at this raking corner: the one its record carries (its
    master's chain's, so both faces build the same corner whatever chain each is in), else
    this face's own."""
    return corner_detail(p, r.get("detail"), None)


def rake_detail(p, r):
    """The detail built at the raking corner *r*: "profile" where that is asked for, the
    corner takes one (external, right-angled, boards or panels) and both faces are clad
    alike; else "mitre"."""
    takes = corner_detail(p, r.get("detail"), r["k"]) == "profile" and not p.get("seam") and r.get("match", True)
    return "profile" if takes else "mitre"


def _rake_cut(r, kind, near, t, face, board, elev, p):
    k, mid = r["k"], near + t / 2.0
    if kind in MEET:
        return -k * mid, k
    if kind in CLAD:
        if rake_detail(p, r) == "profile" and kind != "seam":
            pgap = edge_settings(elev)["pgap"]
            if p["cladding_type"] == "panel":
                return board + pgap - k * face, 0.0                        # the nose (the panel's thickness) and its gap
            return TRIM_BACK + pgap - k * (face - board), 0.0              # against the side of the plank profile's nose
        return r["back"] - k * mid, k
    # battens, closers, the deck: square, behind the boards' ends
    return r["back"] - min(k * (face - board) - RIDGE_OVERSAIL, k * near), 0.0


def rake_ends(meshes, p, infos):
    """Bring every part's end to each raking corner of its face (module docstring)."""
    return cut_to_lines(meshes, p, infos, rakes_of, _rake_cut)


def rake_parts(meshes, p, infos):
    """The corner profile up each raking corner set to Profile, built by the corner's
    master. Records each face's raking corners on its info ("rakes", "profile_strips")."""
    info_by = {i["elevation"]: i for i in infos}
    board, extra = _board(p), []
    plank = p["cladding_type"] != "panel"
    for elev in p.get("elevations", []):
        name, frame, info = elev.get("name"), elev.get("frame"), info_by.get(elev.get("name"))
        rakes = rakes_of(elev) if info is not None else []
        if not rakes:
            continue
        U, V, N = frame["u"], frame_v(frame), frame["n"]
        world = lambda a, b: [a * U[i] + b * V[i] for i in range(3)]          # noqa: E731 — a face direction, in the world
        face = float(info.get("total_depth") or 0.0)
        for r in rakes:
            k, (mx, my) = r["k"], r["m"]
            (x1, y1), (x2, y2) = r["p1"], r["p2"]
            asked = rake_asked(p, r)
            detail = rake_detail(p, r)
            info.setdefault("rakes", []).append({
                "with": r["with"], "master": r["master"], "length": round(r["length"], 1), "external": k > 0,
                "angle": round(math.degrees(2.0 * math.atan(abs(k))), 1), "detail": detail, "asked": asked, "match": r.get("match", True),
                "gap": edge_settings(elev)["gap"]})
            if detail != "profile":
                continue
            # the profile's outer corner: on the bisecting plane, at the depth its nose stands at
            depth = (face - board) + TRIM_LEG if plank else face
            nose = TRIM_BACK + (TRIM_LEG - board) if plank else board       # how wide it shows on this face, about
            a, b = (x1 - k * depth * mx, y1 - k * depth * my), (x2 - k * depth * mx, y2 - k * depth * my)
            info.setdefault("profile_strips", []).append(
                [[a[0], a[1]], [b[0], b[1]], [b[0] + nose * mx, b[1] + nose * my], [a[0] + nose * mx, a[1] + nose * my]])
            if not r["master"]:
                continue
            line = world((x2 - x1) / r["length"], (y2 - y1) / r["length"])
            mesh = profile_mesh(frame_to_world(frame, a[0], a[1], depth), world(-mx, -my), line, r["length"], board,
                                [-float(c) for c in N], "%s Corner Profile %s" % (name, str(r["with"] or "").replace("Elevation ", "")),
                                name, trim=plank)
            extra.append(mesh)
            info["profiles"] = int(info.get("profiles") or 0) + 1
    return meshes + extra
