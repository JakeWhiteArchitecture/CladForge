"""CladForge — thin facade for Pyodide and Flask: generate_preview(params) ->
{"geometry", "dimensions", "info"}; generate_preview_geometry(params) -> list[prism].
check_rules is wrapped here so callers have one import.

Each chain carries its own cladding settings on its elevation records ("settings"): the
job's params are the defaults, a chain's settings go over them, and every group of faces
sharing settings is built on its own, so one chain's choices never change another's."""

import json

from cladding_constants import _parse, chain_params, v_to_z
from cladding_profiles import profile_planks
from cladding_geometry import build_elevation
from cladding_booleans import apply_boolean_ops, clip_elevation
from cladding_primitives import buildup_depth, chain_layout, corner_detail, clip_bounds_v, base_level
from cladding_edges import mitre_to_linings, finish_seams
from cladding_checks import check_rules as _check_rules
from cladding_ridge import ridge_ends, ridge_parts


def _build_all(p):
    meshes, dims, infos = [], [], []
    live = [clip_elevation(e) for e in p["elevations"] if e.get("ok", True) and e.get("polygons")]
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
        m, d, i = build_elevation(p, elev, layout.get(elev.get("name")))
        meshes, dims = meshes + m, dims + d
        infos.append(i)
    return meshes, dims, infos


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


def build_all_groups(params):
    """(meshes, dims, infos) for every face, each group with its chain's settings, untrimmed:
    the exporters' quick source of dimensions and setting-out info."""
    meshes, dims, infos = [], [], []
    for sub, _elevs in setting_groups(params):
        m, d, i = _build_all(_parse(sub))
        meshes, dims, infos = meshes + m, dims + d, infos + i
    return meshes, dims, infos


def generate_preview(params):
    meshes, dims, infos = [], [], []
    for sub, _elevs in setting_groups(params):
        m, d, i = _preview_one(sub)
        meshes, dims, infos = meshes + m, dims + d, infos + i
    return {"geometry": _ensure_unique_names(meshes), "dimensions": dims, "info": infos}


def _preview_one(params):
    """One group of faces sharing their settings, built, trimmed, mitred and finished."""
    p = _parse(params)
    meshes, dims, infos = _build_all(p)
    if p["trim"]:
        meshes = apply_boolean_ops(meshes, p)
    # Boards are mitred to the reveal linings on the finished outline, so the edges of
    # holes left by trimming are mitred as well as the ends that land on a jamb.
    meshes = mitre_to_linings(meshes, p, infos)
    meshes = finish_seams(meshes, p)     # where a seam runs out: folded down, or flat
    meshes = ridge_ends(meshes, p)       # two slopes meeting at a ridge: cut on its bisecting plane
    meshes = meshes + ridge_parts(p, meshes, infos)    # and the cap over it
    if p["trim"]:                        # a custom profile's real section; a plain board while dragging
        meshes = profile_planks(meshes, p)
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
