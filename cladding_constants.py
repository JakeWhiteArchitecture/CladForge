"""CladForge — shared constants, parameter parsing and the prism mesh helper.
Every generated element is a "prism": a 2D profile in an elevation's local (u, v)
frame extruded outward along the wall normal."""

TOOL_NAME = "CladForge"
TOOL_URL = "https://jakewhitearchitecture.com/cladforge/"
IFC_SCHEMA_LABEL = "IFC4X3"
SCOPE_NOTE = ("CladForge does not check compliance with Approved Document B and does not "
              "generate, position or verify cavity barriers. Cavity barrier provision "
              "remains a design decision outside the tool.")
QUANTITY_NOTE = ("Any panel or plank schedule produced is setting-out information, "
                 "not a quantity take-off for pricing.")
DISCLAIMER = "CladForge — Preliminary design aid only. User must verify all outputs before use."

MAX_BATTEN_SPAN = 600.0          # mm – ceiling on batten centres in panel mode
MIN_CAVITY = 25.0                # mm – drained cavity behind cladding
FIXING_EMBEDMENT = 25.0          # mm – batten depth beyond insulation thickness
PLANK_SPAN_TABLE = [(12.0, 400.0), (16.0, 500.0), (20.0, 600.0)]  # (min thickness, max centres)

COLORS = {  # ifc_type: (hex, opacity) — two batten tones, translucent layers, see-through cladding
    "sheathing": ("#d9c9a3", 0.35), "insulation": ("#e8d86a", 0.30), "counter_batten": ("#8b7355", 1.0),
    "batten": ("#c8a87c", 1.0), "cross_batten": ("#c8a87c", 1.0), "panel": ("#6b8fa3", 0.45),
    "plank": ("#6b8fa3", 0.45), "closer": ("#a0522d", 1.0), "reveal": ("#6b8fa3", 0.7)}
_NUMERIC = {  # name: (default, min, max)
    "sheathing_t": (9, 6, 18), "insulation_t": (100, 25, 200),
    "batten_w": (50, 25, 100), "batten_d": (38, 19, 100), "batten_centres": (400, 300, 600),
    "cb_w": (50, 25, 100), "cb_d": (38, 19, 100), "cb_centres": (600, 300, 900),
    "splash": (150, 0, 300),
    "panel_t": (9, 6, 20), "panel_w": (1200, 600, 1500), "panel_h": (2400, 1200, 3000),
    "panel_gap": (10, 0, 15), "plank_w": (150, 75, 250), "plank_t": (20, 12, 32),
    "plank_lap": (0, 0, 50), "plank_gap": (8, 0, 15), "plank_len": (3600, 1800, 6000),
    "closer_w": (50, 25, 150),
}


def _parse(params):
    """Clean parameter dict with defaults, clamped ranges and derived values."""
    p = {"elevations": list(params.get("elevations") or []), "context": params.get("context") or {}}
    for key, (default, lo, hi) in _NUMERIC.items():
        try:
            p[key] = min(hi, max(lo, float(params.get(key, default))))
        except (TypeError, ValueError):
            p[key] = float(default)
    p["sheathing"], p["insulation"] = bool(params.get("sheathing")), bool(params.get("insulation"))
    p["reveals"], p["set_out_from_openings"] = bool(params.get("reveals", True)), bool(params.get("set_out_from_openings", True))
    p["cladding_type"] = "panel" if params.get("cladding_type") == "panel" else "plank"
    p["counter_batten"] = params.get("counter_batten", "auto")   # auto | yes | no
    p["plank_orient"] = "vertical" if params.get("plank_orient") == "vertical" else "horizontal"
    p["corner"] = params.get("corner") if params.get("corner") in ("mitre", "lap", "butt") else "mitre"
    p["trim"] = bool(params.get("trim", True))
    # Derived: battens perpendicular to boards; horizontal battens on vertical counter-battens.
    vertical_planks = p["cladding_type"] == "plank" and p["plank_orient"] == "vertical"
    p["boards_run"] = "vertical" if vertical_planks else "horizontal"
    p["battens"] = "horizontal" if vertical_planks else "vertical"
    p["cb_derived"] = vertical_planks
    p["has_cb"] = {"yes": True, "no": False}.get(p["counter_batten"], vertical_planks)
    p["cover"] = (max(25.0, p["plank_w"] - p["plank_lap"]) if p["plank_lap"] > 0
                  else p["plank_w"] + p["plank_gap"])          # lapped profiles cover less than face
    if p["cladding_type"] == "panel":                          # joints on battens: width drives centres
        bay = p["panel_w"] + p["panel_gap"]
        p["batten_centres"] = bay / max(1, int(-(-bay // MAX_BATTEN_SPAN)))
    return p


def _prism(profile, depth, thickness, frame, ifc_type, name="", elevation="", holes=None):
    """Profile in elevation-local (u, v) mm; *depth* is the back face's offset from the wall."""
    color, opacity = COLORS.get(ifc_type, ("#cccccc", 1.0))
    return {"type": "prism", "profile": [[float(a), float(b)] for a, b in profile],
            "holes": [[[float(a), float(b)] for a, b in h] for h in (holes or [])],
            "depth": float(depth), "thickness": float(thickness), "frame": frame,
            "color": color, "opacity": opacity, "name": name, "ifc_type": ifc_type,
            "elevation": elevation}


def _rect(u0, v0, u1, v1):
    return [[u0, v0], [u1, v0], [u1, v1], [u0, v1]]


WORLD_UP = (0.0, 0.0, 1.0)


def frame_to_world(frame, u, v, d=0.0):
    """Frame-local (u, v, depth) → IFC world [x, y, z] (mm, Z-up): origin + u·U + v·V + d·N.
    A frame can stand (a wall: V is world Z), lie flat (a roof: N is world Z) or run along
    an edge (a trim: N along the edge). A frame with no "v" is old data and reads as world
    Z, which is exactly what n × u gives for a wall frame, so walls are unaffected."""
    o, U, N = frame["origin"], frame["u"], frame["n"]
    V = frame.get("v") or WORLD_UP
    return [o[0] + U[0] * u + V[0] * v + N[0] * d, o[1] + U[1] * u + V[1] * v + N[1] * d,
            o[2] + U[2] * u + V[2] * v + N[2] * d]


def world_to_frame(frame, p):
    """IFC world [x, y, z] → frame-local (u, v, depth). The inverse of frame_to_world for
    an orthonormal frame."""
    o, U, N = frame["origin"], frame["u"], frame["n"]
    V = frame.get("v") or WORLD_UP
    q = [p[0] - o[0], p[1] - o[1], p[2] - o[2]]
    dot = lambda a: a[0] * q[0] + a[1] * q[1] + a[2] * q[2]   # noqa: E731
    return [dot(U), dot(V), dot(N)]
