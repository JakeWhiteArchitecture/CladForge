"""CladForge — thin facade for Pyodide and Flask: generate_preview(params) ->
{"geometry", "dimensions", "info", "masses"}; generate_preview_geometry(params) -> list[prism].
check_rules is wrapped here so callers have one import.

Each chain carries its own cladding settings on its elevation records ("settings"): the
job's params are the defaults, a chain's settings go over them, and every group of faces
sharing settings is built on its own, so one chain's choices never change another's."""

import json

from cladding_constants import _parse, chain_params, frame_to_world, v_to_z
from cladding_profiles import profile_planks
from cladding_geometry import build_elevation
from cladding_booleans import apply_boolean_ops, clip_elevation
from cladding_primitives import buildup_depth, chain_layout, corner_detail, clip_bounds, clip_bounds_v, base_level
from cladding_edges import mitre_to_linings, finish_seams, break_at_seams
from cladding_checks import check_rules as _check_rules
from cladding_ridge import ridge_ends, ridge_parts
from cladding_verge import close_cap_ends, join_drips, join_hip_drips, verge_parts, join_verges
from cladding_valley import valley_cut, valley_parts
from cladding_hip import gablet_hip_tops, hip_ends, hip_parts
from cladding_greencoat import cladding_drips
from cladding_eaves import eaves_walls, eaves_parts, eaves_under, join_eaves
from cladding_head import head_ridges, head_parts, head_steps
from cladding_vergehead import verge_heads, verge_head_parts
from cladding_abut import abut_over, abut_parts, abut_ridges, join_abuts
from cladding_rake import rake_ends, rake_parts
from cladding_window import window_parts
from cladding_lod import at_lod, masses, tag  # noqa: F401 — at_lod is the app's and the exporters'
from cladding_match import plan_matches
from cladding_constants import crumb


def _prepare(p):
    """One group's faces made ready to build: (the faces, clipped; the chains' layout)."""
    live = [clip_elevation(e) for e in p["elevations"] if e.get("ok", True) and e.get("polygons")]
    live = [dict(e) for e in live]      # this build's own: what it notes on a face does not outlast it
    p["elevations"] = live      # the outline, splash bands and trimming all follow the clip
    layout = chain_layout(live, buildup_depth(p), corner_detail(p))
    # One course datum per chain: where the chosen member starts cladding (the elevation
    # last clicked), or failing that the lowest start of any member.
    datums, chosen = {}, {}
    for elev in live:
        if elev.get("chain"):
            lo, _hi = clip_bounds_v(elev)
            z = v_to_z(elev["frame"], max(base_level(elev, p["splash"]), lo))
            datums[elev["chain"]] = min(z, datums.get(elev["chain"], z))
            if elev.get("course_datum_from") == elev.get("name"):
                chosen[elev["chain"]] = z
    datums.update(chosen)
    for elev in live:
        if elev.get("chain") in datums:
            elev["course_datum_z"] = datums[elev["chain"]]
            elev["course_datum_locked"] = elev["chain"] in chosen
    for elev in live:
        _clear_corner_seams(elev, p)
    return live, layout


SEAM_CORNER_CLEAR = 100.0     # mm – a seam no nearer than this to where a hip or valley meets the eaves


def _clear_corner_seams(elev, p):
    """A roof face on its own in trays run with the fall, with a hip or valley, and its seams
    not shifted by hand: where a seam would land within SEAM_CORNER_CLEAR of a point where
    the face's eaves meets its hip or valley (a triangular face's seams all arrive there, a
    sliver of tray beside the gutter), the trays are slid over, up to half a tray, to the
    shift that keeps every seam clearest of those points ("auto_shift")."""
    if not (p.get("seam") and p.get("plank_orient") == "vertical") or elev.get("chain") or float(elev.get("offset") or 0.0):
        return
    if float(elev.get("pitch") or 90.0) >= 80.0 or not any(v.get("kind") in ("hip", "valley") for v in elev.get("valleys") or []):
        return
    cover = float(p["plank_w"])
    lo, hi = clip_bounds(elev)
    pts = [q for pg in elev.get("polygons") or [] for q in pg["exterior"]]
    if not pts or cover <= 2.0 * SEAM_CORNER_CLEAR:
        return
    low = min(q[1] for q in pts)
    corners = []
    for pg in elev.get("polygons") or []:
        ring = pg["exterior"]
        for a, b in zip(ring, ring[1:] + ring[:1]):
            if abs(a[1] - low) < 5.0 and abs(b[1] - low) < 5.0 and abs(a[0] - b[0]) > 300.0:       # the eaves
                corners += [u for u in (a[0], b[0]) if lo + 1.0 < u < hi - 1.0]                  # its ends inside the face
    if not corners:
        return
    mid = (lo + hi) / 2.0

    def clearance(off):
        base = mid + off + cover / 2.0
        return min(abs(((u - base + cover / 2.0) % cover) - cover / 2.0) for u in corners)
    if clearance(0.0) >= SEAM_CORNER_CLEAR:
        return
    best = max((k * 5.0 for k in range(-int(cover / 10.0), int(cover / 10.0) + 1)), key=lambda off: (clearance(off), -abs(off)))
    if clearance(best) > clearance(0.0):
        elev["offset"] = best
        elev["auto_shift"] = best


def _build_all(p, ready=None):
    """Every face of one group, built. *ready*: the group as _prepare left it, where the
    caller has done that already (_groups)."""
    meshes, dims, infos = [], [], []
    live, layout = ready or _prepare(p)
    for elev in live:
        m, d, i = build_elevation(p, elev, layout.get(elev.get("name")))
        meshes, dims = meshes + m, dims + d
        infos.append(i)
    return meshes, dims, infos


def _groups(params):
    """[(p, (faces, layout))]: every settings group parsed and made ready, and each face
    given the batten lines of the chain directly above or below it (cladding_match)."""
    out = []
    for sub, _elevs in setting_groups(params):
        p = _parse(sub)
        out.append((p, _prepare(p)))
    plan_matches([(p, live, layout) for p, (live, layout) in out],
                 [e.get("name") for e in params.get("elevations") or []], build_elevation)
    return out


def setting_groups(params):
    """The job's faces grouped by their chain's settings, in order: [(params for the group,
    its elevation records)]. Faces without settings (an old state) take the job's own."""
    groups = {}
    for e in params.get("elevations") or []:
        st = e.get("settings") or {}
        groups.setdefault(json.dumps(st, sort_keys=True), (st, []))[1].append(e)
    if not groups:
        return [(params, [])]
    return [(dict(params, **st, elevations=elevs), elevs) for st, elevs in groups.values()]


def params_for(params, names):
    """The params a set of faces (one chain's, by name) is built with: the first named face's
    chain settings over the job's."""
    names = set(names or [])
    elev = next((e for e in params.get("elevations") or [] if e.get("name") in names), None)
    return chain_params(params, elev)


def parse_for(params, names):
    return _parse(params_for(params, names))


def check_rules(params, infos=None):
    """The checks, chain by chain where the chains differ (each named with its chain)."""
    groups = setting_groups(params)
    if len(groups) <= 1 or infos is None:
        return _check_rules(groups[0][0], infos)
    out = []
    for sub, elevs in groups:
        names = {e.get("name") for e in elevs}
        label = ", ".join(sorted({str(e.get("chain") or e.get("name")) for e in elevs}))
        for c in _check_rules(sub, [i for i in infos if i.get("elevation") in names]):
            out.append(dict(c, name="%s · %s" % (label, c["name"])))
    return out


def link_seams(params):
    """Before the build proper: carry each linked face's seam lines over from the face it
    follows ("seam_from": that face's name, set with the pipette, or implied by a mitred
    head-wall abutment), as "seam_lines" in its own u. The lines are read off a plain build
    of the face followed, so they are whatever its own setting-out gives: its openings, its
    chain's offset, its corners and verges. Only between faces whose seams run the same
    way (their u axes parallel). A face followed may itself follow another: up to three deep."""
    elevs = params.get("elevations") or []
    by = {e.get("name"): e for e in elevs}
    for e in elevs:
        e.pop("seam_lines", None)
    linked = [e for e in elevs if by.get(e.get("seam_from")) not in (None, e)]
    for _ in range(3 if linked else 0):
        infos = {}
        for sub, _elevs in setting_groups(params):
            for i in _build_all(_parse(sub))[2]:
                infos[i["elevation"]] = i
        changed = False
        for e in linked:
            src = by[e["seam_from"]]
            got = infos.get(src.get("name")) or {}
            Us, Uf, o = src["frame"]["u"], e["frame"]["u"], e["frame"]["origin"]
            way = sum(a * b for a, b in zip(Us, Uf))
            if abs(way) < 0.99 or not got.get("seam_lines"):
                lines = {"from": src.get("name"), "lines": [], "why": "its seams do not run the same way" if abs(way) < 0.99 else "it has no seams"}
            else:
                to = lambda u: round(sum((w - c) * a for w, c, a in zip(frame_to_world(src["frame"], u, 0.0, 0.0), o, Uf)), 2)   # noqa: E731
                lines = {"from": src.get("name"), "lines": sorted(to(u) for u in got["seam_lines"]),
                         "span": sorted(to(u) for u in got["seam_span"]), "mirror": way < 0, "implied": bool(e.get("seam_from_implied"))}
            if lines != e.get("seam_lines"):
                e["seam_lines"], changed = lines, True
        if not changed:
            break


def build_all_groups(params):
    """(meshes, dims, infos) for every face, each group with its chain's settings, untrimmed:
    the exporters' quick source of dimensions and setting-out info."""
    meshes, dims, infos = [], [], []
    join_verges(params)      # a verge over a clad wall: how far its plywood oversails
    join_eaves(params)
    join_abuts(params)
    link_seams(params)
    for p, ready in _groups(params):
        m, d, i = _build_all(p, ready)
        meshes, dims, infos = meshes + m, dims + d, infos + i
    return meshes, dims, infos


def generate_preview(params):
    meshes, dims, infos = [], [], []
    _crumb.faces = "every face"
    _run(join_verges, params)      # a verge over a clad wall: how far its plywood oversails
    _run(join_eaves, params)       # a roof on the head of a wall: each is told the other's build-up, whatever chain it is in
    _run(eaves_walls, params)      # and a roof's open eaves over a wall whose cladding stands proud of its edge
    _run(head_steps, params)       # a free head stepped over a lower roof's: its drop runs on down over the step
    _run(verge_heads, params)      # a verge with a mono ridge at both ends: the mono ridge run on down it
    _run(join_abuts, params)       # a roof run up to a wall that stands on above it: the wall is told how the roof is finished against it
    _run(link_seams, params)       # faces whose seams follow another's are given its seam lines
    for p, ready in _run(_groups, params):
        m, d, i = _preview_one(p, ready)
        meshes, dims, infos = meshes + m, dims + d, infos + i
    # Each mesh says the lowest level of development it is there at, and LOD 100's sheets
    # go with them (cladding_lod): the app and the exporters choose, the build is always whole.
    _crumb.faces = "every face"
    meshes = _run(tag, _ensure_unique_names(meshes))
    out = {"geometry": meshes, "dimensions": dims, "info": infos, "masses": _run(masses, meshes, setting_groups(params))}
    _crumb("")
    return out


def _crumb(text):
    """Where the preview is, left on the page (cladding_constants.crumb)."""
    crumb(text)


_crumb.faces = ""


def _run(fn, *args):
    _crumb("%s (%s)" % (fn.__name__.strip("_"), _crumb.faces))
    return fn(*args)


def _preview_one(p, ready=None):
    """One group of faces sharing their settings (*p*, parsed), built, trimmed, mitred and
    finished."""
    _crumb.faces = ", ".join(str(e.get("name")) for e in p.get("elevations") or [])
    meshes, dims, infos = _run(_build_all, p, ready)
    if p["trim"]:
        meshes = _run(apply_boolean_ops, meshes, p)
    meshes = _run(valley_cut, meshes, p)       # the cladding stands back from a valley, before its seams are finished
    # Boards are mitred to the reveal linings on the finished outline, so the edges of
    # holes left by trimming are mitred as well as the ends that land on a jamb.
    meshes = _run(mitre_to_linings, meshes, p, infos)
    meshes = _run(finish_seams, meshes, p)     # where a seam runs out: folded down, or flat
    _run(head_ridges, p)                     # a monopitch's head verge is cut as half a ridge
    _run(abut_ridges, p)                     # and so is a slope run up to a wall: the head-wall abutment
    meshes = _run(ridge_ends, meshes, p, infos)    # two slopes meeting at a ridge: cut to it, the vent slot left
    meshes = meshes + _run(ridge_parts, p, meshes, infos)    # and the cap over it
    meshes = _run(hip_ends, meshes, p, infos)      # two slopes meeting at a hip: the same, along a sloping line
    meshes = _run(hip_parts, meshes, p, infos)
    meshes = _run(rake_ends, meshes, p, infos)     # a leaning wall's corner with another wall: mitred, or under a corner profile
    meshes = _run(rake_parts, meshes, p, infos)
    meshes = _run(verge_parts, meshes, p, infos)   # a roof slope's free side edges: the last tray out over a verge strip
    meshes = _run(valley_parts, meshes, p, infos)  # the layers under it meet on the valley, and the gutter lies on them
    meshes = _run(eaves_parts, meshes, p, infos)   # a roof slope's level bottom edge: the trays turned round the plywood's end into the drip
    meshes = _run(join_drips, meshes, p)           # where it meets a G3 verge: the verge drip down to the back of the eaves drip
    meshes = _run(join_hip_drips, meshes, p)       # where it runs into a hip: the two slopes' drips mitred on the hip's plan line
    meshes = _run(eaves_under, meshes, p, infos)   # and the wall it comes down to the head of stops under its soffit
    meshes = _run(window_parts, meshes, p, infos)  # a standing seam wall's openings: the sill capping and the head strip
    meshes = _run(cladding_drips, meshes, p, infos)   # a coated steel standing seam wall's foot: GreenCoat's cladding drip
    meshes = _run(head_parts, meshes, p, infos)    # a monopitch's level top edge: the G3 head verge over it
    meshes = _run(verge_head_parts, meshes, p, infos)    # and down a verge between two of them, mitred onto both
    meshes = _run(abut_parts, meshes, p, infos)    # a slope run up to a wall: the G3 flashing up it, or the trays turned up under its cladding
    meshes = _run(abut_over, meshes, p, infos)     # and that wall's layers stop above the roof
    meshes = _run(close_cap_ends, meshes, p)       # a ridge or head verge cap that ends at a verge: the verge up to its plane, a stop end
    meshes = _run(gablet_hip_tops, meshes, p, infos)   # a hip under a gablet's gable: its cap stops on the rake's seam, the slope dressed out to it
    meshes = _run(break_at_seams, meshes, p)       # last: each tray's flat drawn back to its own upstand, clear of the next tray's
    if p["trim"]:                        # a custom profile's real section; a plain board while dragging
        meshes = _run(profile_planks, meshes, p)
    if p.get("seam"):                    # a metal facade reads solid: its reveal linings too
        for m in meshes:
            if m.get("ifc_type") == "reveal":
                m["opacity"] = 1.0
    return meshes, dims, infos


def generate_preview_geometry(params):
    return generate_preview(params)["geometry"]


def _ensure_unique_names(meshes):
    counts, seen = {}, {}
    for m in meshes:
        m["name"] = m.get("name") or m.get("ifc_type", "Element").replace("_", " ").title()
        counts[m["name"]] = counts.get(m["name"], 0) + 1
    for m in meshes:
        if counts[m["name"]] > 1:
            seen[m["name"]] = seen.get(m["name"], 0) + 1
            m["name"] = "%s %d" % (m["name"], seen[m["name"]])
    return meshes


# ── trying Splices settings (Optimise splices) ───────────────────────────

SPLICE_STEPS = tuple(range(0, 101, 10))     # the Splices slider, % from Fewer to More


def splice_trial(params, names, area):
    """One Splices setting tried: the plank setting-out rebuilt at params["plank_splice"]
    and packed onto stock lengths (the quick check, each elevation on its own), for the
    given elevations. The stock length and the shortest piece stay as set, and joints
    still land on battens. The app runs one per call, so it can say how far it has got."""
    from cladding_nesting import chain_plan
    p = parse_for(params, names)
    out = generate_preview(params)
    plan = chain_plan(out["geometry"], p, names, area)
    return {"splice": p["plank_splice"], "piece": round(p["plank_target"]), "n_boards": plan["n_boards"],
            "waste": plan["waste"], "n_pieces": plan["n_pieces"], "lower_bound": plan["lower_bound"],
            "too_long": len(plan["oversize"])}


def rank_splices(trials, current):
    """Best first: fewest planks too long to cut from the stock (they are left out of the
    packing, so a setting with any would look better than it is), then fewest stock
    lengths, then least waste, then fewest pieces (less cutting and fixing), then nearest
    the current setting (no change for nothing)."""
    return sorted(trials, key=lambda t: (t.get("too_long", 0), t["n_boards"], round(t["waste"], 4),
                                         t.get("n_pieces", 0), abs(t["splice"] - current)))
