"""CladForge — thin facade for Pyodide and Flask: generate_preview(params) ->
{"geometry", "dimensions", "info"}; generate_preview_geometry(params) -> list[prism];
check_rules(params, infos=None) -> list[{"name", "status", "message", "value"}]."""

from cladding_constants import (_parse, PLANK_SPAN_TABLE, MIN_CAVITY, FIXING_EMBEDMENT,
                                MAX_BATTEN_SPAN)
from cladding_geometry import build_elevation
from cladding_booleans import apply_boolean_ops

MIN_CLOSING_CUT = 100.0   # mm – narrower closing pieces are hard to fix and look wrong


def _build_all(p):
    meshes, dims, infos = [], [], []
    for elev in p["elevations"]:
        if elev.get("ok", True) and elev.get("polygons"):
            m, d, i = build_elevation(p, elev)
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


def check_rules(params, infos=None):
    """Validation table from the requirements (not a compliance check). Pass the *infos*
    from generate_preview to avoid rebuilding the geometry."""
    p, checks = _parse(params), []
    add = lambda name, status, message, value=None: checks.append(  # noqa: E731
        {"name": name, "status": status, "message": message, "value": value})

    if p["cladding_type"] == "plank":
        max_span = max([400.0] + [span for min_t, span in PLANK_SPAN_TABLE if p["plank_t"] >= min_t])
        c = p["batten_centres"]
        status = "pass" if c < max_span - 0.5 else ("warn" if c <= max_span + 0.5 else "fail")
        add("Batten centres", status, "Batten centres %.0fmm vs %.0fmm max span for %.0fmm planks%s"
            % (c, max_span, p["plank_t"], " — exceeds max span" if status == "fail" else ""), c)
    else:
        add("Panel joints", "pass", "Panel joints land on battens at %.0fmm centres (locked, max %.0f)"
            % (p["batten_centres"], MAX_BATTEN_SPAN), p["batten_centres"])
        add("Panel size", "pass", "Panels %.0f x %.0fmm within max sheet size" % (p["panel_w"], p["panel_h"]))

    s = p["splash"]
    status = "pass" if s >= 150 else ("warn" if s >= 100 else "fail")
    add("Splash zone", status, "Splash zone %.0fmm%s" % (s, "" if status == "pass" else " — below the 150mm recommended minimum"), s)

    if p["insulation"]:
        first = p["cb_d"] if p["has_cb"] else p["batten_d"]
        need = p["insulation_t"] + FIXING_EMBEDMENT
        status = "pass" if first >= need else "fail"
        add("Fixing through insulation", status, "First batten layer %.0fmm deep vs %.0fmm insulation + %.0fmm%s"
            % (first, p["insulation_t"], FIXING_EMBEDMENT, "" if status == "pass" else " — insufficient embedment"), first)

    cavity = p["batten_d"] if p["battens"] == "vertical" else (p["cb_d"] if p["has_cb"] else 0.0)
    status = "pass" if cavity >= MIN_CAVITY else "fail"
    add("Cavity depth", status, "Drained cavity %.0fmm%s" % (cavity, "" if status == "pass" else " — below %.0fmm minimum" % MIN_CAVITY), cavity)

    if p["battens"] == "horizontal" and not p["has_cb"]:
        add("Buildup", "fail", "Horizontal battens without counter-battens block the cavity drainage")
    elif p["has_cb"] and not p["cb_derived"]:
        add("Buildup", "warn", "Counter-battens forced on: horizontal counter-battens interrupt the drained cavity")
    else:
        add("Buildup", "pass", "%s battens%s support %s boards" % (
            p["battens"].title(), " on vertical counter-battens" if p["has_cb"] else "", p["boards_run"]))

    if infos is None:
        infos = _build_all(p)[2]
    for i in infos:
        cuts = [c for c in (i.get("closing_cut_left", 0), i.get("closing_cut_right", 0)) if 0 < c < MIN_CLOSING_CUT]
        if cuts:
            add("Closing cut", "warn", "%s: closing cut %.0fmm is narrower than %.0fmm — shift the setting-out"
                % (i["elevation"], min(cuts), MIN_CLOSING_CUT), min(cuts))
    bad = sum(i.get("unsupported_joints", 0) for i in infos)
    if bad:
        add("End joints", "warn", "%d board end joints fall between battens — shorten max length or adjust centres" % bad, bad)
    elif infos:
        add("End joints", "pass", "All board end joints land on a batten")
    return checks
