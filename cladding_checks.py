"""CladForge — the validation table from the requirements. Not a compliance check."""

from cladding_constants import (_parse, PLANK_SPAN_TABLE, MIN_CAVITY, FIXING_EMBEDMENT,
                                MAX_BATTEN_SPAN)
from cladding_primitives import ROW_MIN

MIN_CLOSING_CUT = 100.0   # mm – narrower closing pieces are hard to fix and look wrong


def check_rules(params, infos=None):
    """Pass the *infos* from generate_preview to avoid rebuilding the geometry."""
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
        add("Panel seams", "pass" if p["has_cb"] else "warn",
            "Noggins fitted behind the horizontal seams: the counter-batten layer keeps the drainage plane clear"
            if p["has_cb"] else
            "Horizontal seams left unsupported — a noggin between vertical battens would dam the cavity. "
            "Add counter-battens to support them, or use a proprietary horizontal joint profile")

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
        from cladding_preview import _build_all
        infos = _build_all(p)[2]
    for i in infos:
        if i.get("set_out_from_openings"):
            add("Setting-out", "pass", "%s: %d opening(s) drive the setting-out; panel edges land on the "
                "jambs and the widest bay is %.0fmm" % (i["elevation"], i.get("openings", 0),
                                                        max(i.get("panel_widths") or [0])))
        cuts = [c for c in (i.get("closing_cut_left", 0), i.get("closing_cut_right", 0),
                            i.get("min_panel", 0)) if 0 < c < MIN_CLOSING_CUT]
        if i.get("short_rows"):
            add("Panel rows", "warn", "%s: row %s asked for less than %.0fmm — raised to %.0fmm, the "
                "shortest row that can be fixed" % (i["elevation"], ", ".join(str(r + 1) for r in i["short_rows"]),
                                                    ROW_MIN, ROW_MIN), len(i["short_rows"]))
        top = i.get("closing_cut_top", 0) if i.get("rows") else 0
        if 0 < top < MIN_CLOSING_CUT:
            add("Panel rows", "warn", "%s: the closing row at the top is only %.0fmm — change a row height "
                "so it is at least %.0fmm" % (i["elevation"], top, MIN_CLOSING_CUT), top)
        if cuts:
            add("Closing cut", "warn", "%s: a panel only %.0fmm wide is narrower than %.0fmm — shift the "
                "setting-out or move a joint" % (i["elevation"], min(cuts), MIN_CLOSING_CUT), min(cuts))
    # Each corner end carries its own detail, so count the ends per detail.
    per = {}
    for c in (i.get("corner") or {} for i in infos):
        for side, detail in zip(("left", "right"), c.get("details") or (c.get("detail"),) * 2):
            if any(c.get(side) or ()):
                per[detail] = per.get(detail, 0) + 1
    if per:
        add("Corners", "pass", "; ".join("%d corner end(s), %s" % (n, {
            "mitre": "mitred: the whole buildup wraps on the bisector plane",
            "lap": "master-lap: one board masters the corner and the other butts behind it, joint gap exposed",
            "butt": "square: both boards stop at the wall corner"}[d]) for d, n in sorted(per.items())))
    bad = sum(i.get("unsupported_joints", 0) for i in infos)
    if bad:
        add("End joints", "warn", "%d board end joints fall between battens — shorten max length or adjust centres" % bad, bad)
    elif infos:
        add("End joints", "pass", "All board end joints land on a batten")
    return checks
