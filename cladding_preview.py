"""CladForge — thin facade for Pyodide and Flask: generate_preview(params) ->
{"geometry", "dimensions", "info"}; generate_preview_geometry(params) -> list[prism].
check_rules is re-exported here so callers have one import."""

from cladding_constants import _parse
from cladding_geometry import build_elevation
from cladding_booleans import apply_boolean_ops, clip_elevation
from cladding_primitives import buildup_depth, chain_layout, corner_detail, clip_bounds_v, base_level
from cladding_checks import check_rules  # noqa: F401 — public API lives here


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
            z = float(elev["frame"]["origin"][2]) + max(base_level(elev, p["splash"]), lo)
            datums[elev["chain"]] = min(z, datums.get(elev["chain"], z))
            if elev.get("course_datum_from") == elev.get("name"):
                chosen[elev["chain"]] = z
    datums.update(chosen)
    for elev in live:
        if elev.get("chain") in datums:
            elev["course_datum_z"] = datums[elev["chain"]]
    for elev in live:
        m, d, i = build_elevation(p, elev, layout.get(elev.get("name")))
        meshes, dims = meshes + m, dims + d
        infos.append(i)
    return meshes, dims, infos


def generate_preview(params):
    p = _parse(params)
    meshes, dims, infos = _build_all(p)
    if p["trim"]:
        meshes = apply_boolean_ops(meshes, p)
    return {"geometry": _ensure_unique_names(meshes), "dimensions": dims, "info": infos}


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
