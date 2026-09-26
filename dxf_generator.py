"""
CladForge — DXF export: one flattened elevation per detected region.

    meshes_to_dxf_string(meshes, params, infos=None) -> str

Each elevation is drawn in its own (u, v) frame, moved to origin and laid out
left to right. Layers: WALL, OPENING, SPLASH_ZONE, SHEATHING, INSULATION,
COUNTER_BATTEN, BATTEN, CLADDING, CLOSER, DIMS, NOTES. Output is DXF R12
(AC1009), readable by every CAD package.
"""

import math

from cladding_constants import (_parse, TOOL_NAME, IFC_SCHEMA_LABEL, SCOPE_NOTE, QUANTITY_NOTE,
                                DISCLAIMER)
from cladding_primitives import buildup_depth, corner_ring, splash_rings

LAYERS = {
    "WALL":           {"color": 7, "linetype": "CONTINUOUS"},
    "OPENING":        {"color": 1, "linetype": "DASHED"},
    "SPLASH_ZONE":    {"color": 6, "linetype": "DASHED"},
    "SHEATHING":      {"color": 8, "linetype": "CONTINUOUS"},
    "INSULATION":     {"color": 2, "linetype": "CONTINUOUS"},
    "COUNTER_BATTEN": {"color": 3, "linetype": "CONTINUOUS"},
    "BATTEN":         {"color": 4, "linetype": "CONTINUOUS"},
    "CLADDING":       {"color": 5, "linetype": "CONTINUOUS"},
    "CLOSER":         {"color": 1, "linetype": "CONTINUOUS"},
    "DIMS":           {"color": 7, "linetype": "CONTINUOUS"},
    "NOTES":          {"color": 7, "linetype": "CONTINUOUS"},
}
_LAYER_FOR_TYPE = {"batten": "BATTEN", "cross_batten": "BATTEN", "counter_batten": "COUNTER_BATTEN",
                   "sheathing": "SHEATHING", "insulation": "INSULATION", "panel": "CLADDING",
                   "plank": "CLADDING", "closer": "CLOSER"}
_ELEV_GAP = 2500.0     # mm between elevations on the sheet
_TEXT = 50.0           # mm dimension / note text height
_TITLE = 120.0


class _DxfWriter:
    """Builds a DXF R12 (AC1009) string from LINE and TEXT entities."""

    def __init__(self):
        self._entities, self._texts, self._layers, self._linetypes = [], [], {}, {}

    def add_linetype(self, name, pattern):
        self._linetypes[name] = pattern

    def add_layer(self, name, color=7, linetype="CONTINUOUS"):
        self._layers[name] = {"color": color, "linetype": linetype}

    def add_line(self, start, end, layer="0"):
        if math.hypot(end[0] - start[0], end[1] - start[1]) > 0.01:
            self._entities.append((start, end, layer))

    def add_ring(self, ring, layer, ox=0.0, oy=0.0):
        pts = [(x + ox, y + oy) for x, y in ring]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            self.add_line(a, b, layer)

    def add_text(self, text, position, height=_TEXT, layer="0"):
        self._texts.append((text, position, height, layer))

    def to_string(self):
        lines = []
        a = lines.append
        a("  0"); a("SECTION"); a("  2"); a("HEADER")
        a("  9"); a("$ACADVER"); a("  1"); a("AC1009")
        a("  9"); a("$MEASUREMENT"); a(" 70"); a("     1")
        a("  0"); a("ENDSEC")
        a("  0"); a("SECTION"); a("  2"); a("TABLES")
        a("  0"); a("TABLE"); a("  2"); a("LTYPE"); a(" 70"); a("     %d" % (len(self._linetypes) + 1))
        a("  0"); a("LTYPE"); a("  2"); a("CONTINUOUS"); a(" 70"); a("     0"); a("  3"); a("Solid line")
        a(" 72"); a("    65"); a(" 73"); a("     0"); a(" 40"); a("0.0")
        for lt_name, pattern in self._linetypes.items():
            a("  0"); a("LTYPE"); a("  2"); a(lt_name); a(" 70"); a("     0"); a("  3"); a("")
            a(" 72"); a("    65"); a(" 73"); a("     %d" % (len(pattern) - 1)); a(" 40"); a("%.4f" % pattern[0])
            for val in pattern[1:]:
                a(" 49"); a("%.4f" % val)
        a("  0"); a("ENDTAB")
        a("  0"); a("TABLE"); a("  2"); a("LAYER"); a(" 70"); a("     %d" % (len(self._layers) + 1))
        a("  0"); a("LAYER"); a("  2"); a("0"); a(" 70"); a("     0"); a(" 62"); a("     7"); a("  6"); a("CONTINUOUS")
        for lname, lprops in self._layers.items():
            a("  0"); a("LAYER"); a("  2"); a(lname); a(" 70"); a("     0")
            a(" 62"); a("     %d" % lprops["color"]); a("  6"); a(lprops["linetype"])
        a("  0"); a("ENDTAB"); a("  0"); a("ENDSEC")
        a("  0"); a("SECTION"); a("  2"); a("ENTITIES")
        for start, end, layer in self._entities:
            a("  0"); a("LINE"); a("  8"); a(layer)
            a(" 10"); a("%.4f" % start[0]); a(" 20"); a("%.4f" % start[1]); a(" 30"); a("0.0")
            a(" 11"); a("%.4f" % end[0]); a(" 21"); a("%.4f" % end[1]); a(" 31"); a("0.0")
        for text, pos, height, tlayer in self._texts:
            a("  0"); a("TEXT"); a("  8"); a(tlayer)
            a(" 10"); a("%.4f" % pos[0]); a(" 20"); a("%.4f" % pos[1]); a(" 30"); a("0.0")
            a(" 40"); a("%.4f" % height); a("  1"); a(text.replace("\n", " "))
        a("  0"); a("ENDSEC"); a("  0"); a("EOF")
        return "\r\n".join(lines) + "\r\n"


# ── dimensions and notes ─────────────────────────────────────────────────

def _draw_dim_line(dxf, p1, p2, offset, label=None, norm=None, layer="DIMS"):
    """Linear dimension between p1 and p2 offset along *norm* (unit normal)."""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length < 1:
        return
    nx, ny = norm if norm else (-dy / length, dx / length)
    off = abs(offset)
    d1 = (p1[0] + nx * off, p1[1] + ny * off)
    d2 = (p2[0] + nx * off, p2[1] + ny * off)
    gap, over = 30.0, 50.0
    dxf.add_line((p1[0] + nx * gap, p1[1] + ny * gap), (p1[0] + nx * (off + over), p1[1] + ny * (off + over)), layer)
    dxf.add_line((p2[0] + nx * gap, p2[1] + ny * gap), (p2[0] + nx * (off + over), p2[1] + ny * (off + over)), layer)
    dxf.add_line(d1, d2, layer)
    tick = 20.0
    tdx, tdy, tnx, tny = dx / length * tick, dy / length * tick, nx * tick, ny * tick
    for d in (d1, d2):
        dxf.add_line((d[0] - tdx - tnx, d[1] - tdy - tny), (d[0] + tdx + tnx, d[1] + tdy + tny), layer)
    text = label if label is not None else "%.0f" % length
    dxf.add_text(text, ((d1[0] + d2[0]) / 2 + nx * 30, (d1[1] + d2[1]) / 2 + ny * 30), _TEXT, layer)


def _corner_ring(ring, mesh):
    """Ring drawn at the element's outer face, so a corner end shows its cut length."""
    corner = mesh.get("corner")
    return ring if not corner else corner_ring(ring, corner, float(mesh["depth"]) + float(mesh["thickness"]))


def _wrap(text, width=70):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


def _text_block(dxf, lines, x, y, height=_TEXT, layer="NOTES", spacing=1.6):
    for i, line in enumerate(lines):
        dxf.add_text(line, (x, y - i * height * spacing), height, layer)
    return y - len(lines) * height * spacing


def _schedule(p, info, meshes):
    """Setting-out schedule lines for one elevation (not a quantity take-off)."""
    counts = {}
    for m in meshes:
        counts[m["ifc_type"]] = counts.get(m["ifc_type"], 0) + 1
    lines = ["SETTING-OUT SCHEDULE (setting-out information only, not a quantity take-off)"]
    lines.append("Buildup: %s%s%s battens %.0fx%.0f @ %.0f c/c%s" % (
        "sheathing %.0fmm, " % p["sheathing_t"] if p["sheathing"] else "",
        "insulation %.0fmm, " % p["insulation_t"] if p["insulation"] else "",
        info.get("battens", ""), p["batten_w"], p["batten_d"], info.get("batten_centres", 0),
        ", counter-battens %.0fx%.0f @ %.0f c/c" % (p["cb_w"], p["cb_d"], p["cb_centres"]) if p["has_cb"] else ""))
    lines.append("Battens: %d no.  Counter-battens: %d no.  Noggins: %d no.  Cavity closers: %d no." % (
        counts.get("batten", 0), counts.get("counter_batten", 0), counts.get("cross_batten", 0),
        counts.get("closer", 0)))
    if counts.get("reveal"):
        lines.append("Reveal linings: %d no., mitred to the face panel. Not drawn: they are "
                     "perpendicular to this view." % counts["reveal"])
    if info.get("openings"):
        lines.append("Openings: %d. Setting-out %s." % (info["openings"], "starts from the structural "
                     "openings, so panel edges land on the jambs" if info.get("set_out_from_openings")
                     else "is centred on the elevation"))
    if p["cladding_type"] == "panel":
        rows = info.get("rows") or []
        lines.append("Panels: %d pieces in %d rows, joint gap %.0f. Rows bottom up: %s (top row is the "
                     "closing cut); every row is dimensioned on the right." % (
                         counts.get("panel", 0), len(rows), p["panel_gap"], ", ".join("%.0f" % h for h in rows)))
    else:
        lines.append("Planks: %d pieces %.0fx%.0f, %d courses @ %.0f cover (%s)" % (
            counts.get("plank", 0), p["plank_w"], p["plank_t"], info.get("n_courses", 0), info.get("cover", 0),
            "lapped %.0f" % p["plank_lap"] if p["plank_lap"] > 0 else "open joint %.0f" % p["plank_gap"]))
    lines.append("Closing cuts: left %.0f  right %.0f  top %.0f.  Splash zone %.0f from abutments." % (
        info.get("closing_cut_left", 0), info.get("closing_cut_right", 0), info.get("closing_cut_top", 0), p["splash"]))
    corner = info.get("corner") or {}
    ends = [(side, corner.get(side) or (0.0, 0.0)) for side in ("left", "right")]
    if any(any(v) for _s, v in ends):
        names = {"mitre": "mitred", "lap": "master-lap, open joint", "butt": "square"}
        details = dict(zip(("left", "right"), corner.get("details") or (corner.get("detail"),) * 2))
        lines.append("Corners: %s. Boards are drawn to their outer face, which is the cut length." % (
            ", ".join("%s end %s, %s %.0fmm at the cladding face" % (
                side, names.get(details[side], details[side]), "wraps" if (k + ext) > 0 else "is cut back",
                abs(k * buildup_depth(p) + ext))
                for side, (k, ext) in ends if k or ext)))
    return lines


# ── entry points ─────────────────────────────────────────────────────────

def meshes_to_dxf_string(meshes, params, infos=None):
    p = _parse(params)
    from cladding_preview import _build_all
    _m, dims, built = _build_all(p)          # one build gives both dims and infos
    info_by_name = {i["elevation"]: i for i in (infos or built)}
    dxf = _DxfWriter()
    dxf.add_linetype("DASHED", [10.0, 6.35, -3.175])
    for name, props in LAYERS.items():
        dxf.add_layer(name, props["color"], props["linetype"])

    by_elev = {}
    for m in meshes:
        by_elev.setdefault(m.get("elevation", ""), []).append(m)

    ox, sheet_max_x, sheet_min_y = 0.0, 0.0, 0.0
    for elev in p["elevations"]:
        if not elev.get("ok", True) or not elev.get("polygons"):
            continue
        name = elev.get("name", "Elevation")
        W, H = float(elev["width"]), float(elev["height"])
        info = info_by_name.get(name, {})
        for poly in elev["polygons"]:
            dxf.add_ring(poly["exterior"], "WALL", ox, 0.0)
            for hole in poly.get("holes", []):
                dxf.add_ring(hole, "OPENING", ox, 0.0)
        for ring in splash_rings(elev, p["splash"]):
            dxf.add_ring(ring, "SPLASH_ZONE", ox, 0.0)
        for m in by_elev.get(name, []):
            if m["ifc_type"] == "reveal":
                continue        # a reveal lining is perpendicular to this view
            layer = _LAYER_FOR_TYPE.get(m["ifc_type"], "0")
            dxf.add_ring(_corner_ring(m["profile"], m), layer, ox, 0.0)
            for hole in m.get("holes") or []:
                dxf.add_ring(hole, layer, ox, 0.0)
        for d in [d for d in dims if d["elevation"] == name]:
            _draw_dim_line(dxf, (d["p1"][0] + ox, d["p1"][1]), (d["p2"][0] + ox, d["p2"][1]),
                           d["offset"], d["label"], tuple(d["norm"]))
        _draw_dim_line(dxf, (ox, 0), (ox + W, 0), 900.0, "%.0f" % W, (0, -1))
        _draw_dim_line(dxf, (ox + W, 0), (ox + W, H), 1200.0, "%.0f" % H, (1, 0))
        storey = (elev.get("storey") or {}).get("name")
        chain = elev.get("chain")
        title = "%s%s  -  %.0f x %.0f mm%s" % ((chain.upper() + " / ") if chain else "", name.upper(), W, H,
                                              ("  -  " + storey) if storey else "")
        dxf.add_text(title, (ox, H + 500.0), _TITLE, "NOTES")
        dxf.add_text("Flattened elevation in the region plane, moved to origin. Looking at the face from outside.",
                     (ox, H + 350.0), _TEXT, "NOTES")
        y_end = _text_block(dxf, _schedule(p, info, by_elev.get(name, [])), ox, -1500.0)
        sheet_min_y = min(sheet_min_y, y_end)
        ox += W + _ELEV_GAP
        sheet_max_x = ox - _ELEV_GAP

    notes = ["%s  -  %s companion export  -  units mm" % (TOOL_NAME, IFC_SCHEMA_LABEL), DISCLAIMER]
    notes += _wrap(SCOPE_NOTE) + _wrap(QUANTITY_NOTE)
    notes.append("Layers: WALL OPENING SPLASH_ZONE SHEATHING INSULATION COUNTER_BATTEN BATTEN CLADDING CLOSER DIMS NOTES")
    _text_block(dxf, notes, max(0.0, sheet_max_x - 4200.0), sheet_min_y - 600.0, 60.0)
    return dxf.to_string()

