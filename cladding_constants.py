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
    "plank": ("#6b8fa3", 0.45), "closer": ("#a0522d", 1.0), "reveal": ("#6b8fa3", 0.7),
    "corner_profile": ("#c0c6cc", 1.0), "corner_trim": ("#9aa1a8", 1.0), "gasket": ("#1c1c1c", 1.0),
    "dpm": ("#10233f", 1.0),        # the damp proof membrane on a cavity barrier
    "deck": ("#c9a46a", 1.0), "seam": ("#6b8fa3", 1.0),   # a seam is its trays' own metal
    "ridge_cap": ("#7d9bab", 1.0), "ridge_filler": ("#2b2b2b", 1.0), "verge_strip": ("#7d9bab", 1.0),
    "valley_gutter": ("#8fa9b6", 1.0), "eaves_strip": ("#7d9bab", 1.0),
    "window_flashing": ("#7d9bab", 1.0), "sill_board": ("#c9a46a", 1.0),
    "mass": ("#6b8fa3", 0.9)}    # LOD 100's sheet on a picked face (cladding_lod): tinted by its cladding
# A standing seam as modelled: a closed double lock, sheet by sheet (cladding_geometry.
# _double_lock), rolled, with the same air SEAM_AIR between every sheet and the one parallel to
# it (the clip runs in it between the upstands), so the head comes out 5 sheets and 4 airs
# across, SEAM_HEAD_D deep from the top.
SEAM_AIR = 1.8          # mm – between each sheet and the next, throughout
SEAM_HEAD_D = 17.0      # mm – from the top of the seam to the foot of the head
SEAM_STANDOFF = 2.0     # mm – the metal off the deck, and off the reveal behind its linings
SEAM_CENTRES = (430.0, 530.0)   # mm – the standard tray widths, seam to seam
DECK_T = {"boards": 22.0, "ply": 18.0}   # mm – softwood boarding, or WBP plywood (the default)
DECK_SPAN = {"boards": 600.0, "ply": 600.0}   # mm – the most batten centres each deck spans
DPM_T = 1.0                     # damp proof membrane on a cavity barrier, as drawn
EPDM_T, EPDM_LAP = 2.0, 15.0    # EPDM gasket on vertical timbers: thickness, and past the timber each side
_NUMERIC = {  # name: (default, min, max)
    "sheathing_t": (9, 6, 18), "insulation_t": (100, 25, 200),
    "batten_w": (50, 25, 100), "batten_d": (38, 19, 100), "batten_centres": (400, 300, 600),
    "cb_w": (50, 25, 100), "cb_d": (38, 19, 100), "cb_centres": (600, 300, 900),
    "splash": (150, 0, 300),
    "panel_t": (9, 6, 20), "panel_w": (1200, 600, 1500), "panel_h": (2400, 1200, 3000),
    "panel_gap": (10, 0, 15), "plank_w": (150, 75, 250), "plank_t": (20, 12, 32),
    "plank_lap": (0, 0, 50), "plank_gap": (8, 0, 15), "plank_len": (3600, 1800, 6000),
    "plank_min": (300, 100, 3000), "plank_splice": (0, 0, 100),   # shortest piece a splice may leave; 0-100 fewer to more splices
    # standing seam (a plank profile): seam centres 430 or 530, seam height, sheet thickness
    "seam_centres": (530, 430, 530), "seam_h": (25, 25, 38), "seam_t": (0.6, 0.5, 1.0),
    "closer_w": (50, 25, 150),
    "barrier_gap": (200, 200, 600),     # battens stop this far short of a head or sill cavity barrier: never under 200
    "board_w": (1250, 300, 3000), "board_h": (2500, 300, 5000), "kerf": (3, 0, 10), "board_trim": (0, 0, 50),   # stock and saw
}


def chain_params(params, elev):
    """The job's params with this face's chain settings over them: each chain carries its own
    cladding (type, sizes, battens, build-up) on its elevation records as "settings"."""
    st = elev.get("settings") if isinstance(elev, dict) else None
    return dict(params, **st) if st else params


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
    p["barriers"] = bool(params.get("barriers"))        # openings closed with cavity barriers on all four sides
    p["cladding_type"] = "panel" if params.get("cladding_type") == "panel" else "plank"
    p["counter_batten"] = params.get("counter_batten", "auto")   # auto | yes | no
    p["plank_orient"] = "vertical" if params.get("plank_orient") == "vertical" else "horizontal"
    # Standing seam is a plank profile: trays a seam-centre wide, run and joined like planks,
    # on a continuous deck (and membrane) on vertical battens, so the cavity always drains.
    p["plank_profile"] = params.get("plank_profile") if params.get("plank_profile") in ("seam", "custom") else "board"
    p["seam"] = p["cladding_type"] == "plank" and p["plank_profile"] == "seam"
    if p["seam"]:
        p["seam_centres"] = min(SEAM_CENTRES, key=lambda c: abs(c - p["seam_centres"]))
        p["seam_h"] = 38.0 if p["seam_h"] > 31.5 else 25.0
        p["plank_w"], p["plank_gap"], p["plank_lap"], p["plank_t"] = p["seam_centres"], 0.0, 0.0, p["seam_t"]
    # Full-length trays: each tray runs its whole run in one piece (cut from the coil), so
    # no tray is laid above another and there are no cross joints. Standing seam only.
    p["seam_full"] = p["seam"] and bool(params.get("seam_full_length"))
    # A custom profile is a fixed plank, its real section (cladding_profiles): a library one
    # by key, or one read from a drawing. It is laid as a lapped board, the section's full
    # width at its cover, so the lap is the difference; it sets the plank's size.
    p["section"] = None
    if p["cladding_type"] == "plank" and p["plank_profile"] == "custom":
        from cladding_profiles import section, DEFAULT
        p["section"] = section(params.get("custom_section") or params.get("custom_profile") or DEFAULT) \
            or section(DEFAULT)
        sec = p["section"]
        p["plank_w"], p["plank_t"] = sec["width"], sec["thickness"]
        p["plank_lap"], p["plank_gap"] = round(sec["width"] - sec["cover"], 3), 0.0
    p["seam_deck"] = "boards" if params.get("seam_deck") == "boards" else "ply"     # 18 WBP ply unless boarding
    # Where a seam runs out (the base, the top, an opening): folded down to the tray on a
    # slope, or run to the tray's end and stopped square, flush with it.
    p["seam_end"] = "flat" if params.get("seam_end") == "flat" else "folded"
    p["deck_t"] = DECK_T[p["seam_deck"]]
    # every job-wide corner detail the toggle offers (cladding_primitives.CORNER_DETAILS)
    p["corner"] = params.get("corner") if params.get("corner") in ("mitre", "lap", "profile", "butt") else "mitre"
    p["trim"] = bool(params.get("trim", True))
    p["rotate"] = bool(params.get("rotate", True))          # a piece may be cut turned 90 degrees
    # the vertical battens on the columns the client's model has standing on the face
    p["battens_from_model"] = bool(params.get("battens_from_model", True))
    p["match_battens"] = bool(params.get("match_battens", True))      # follow the chain above or below (cladding_match)
    # IFC export: panels as IfcWall (edited as walls, e.g. in Archicad) rather than IfcCovering
    p["panels_as_walls"] = bool(params.get("panels_as_walls", False))
    p["share_offcuts"] = bool(params.get("share_offcuts", False))   # an elevation may cut from earlier ones' offcuts
    # Derived: battens perpendicular to boards; horizontal battens on vertical counter-battens.
    vertical_planks = p["cladding_type"] == "plank" and p["plank_orient"] == "vertical" and not p["seam"]
    p["boards_run"] = "vertical" if p["cladding_type"] == "plank" and p["plank_orient"] == "vertical" else "horizontal"
    p["battens"] = "horizontal" if vertical_planks else "vertical"
    p["cb_derived"] = vertical_planks
    p["has_cb"] = {"yes": True, "no": False}.get(p["counter_batten"], vertical_planks)
    p["cover"] = (max(25.0, p["plank_w"] - p["plank_lap"]) if p["plank_lap"] > 0
                  else p["plank_w"] + p["plank_gap"])          # lapped profiles cover less than face
    # Splices: pieces are set out to a target between the stock length (fewest splices)
    # and the shortest piece a splice may leave (most); never shorter than that minimum.
    p["plank_min"] = min(p["plank_min"], p["plank_len"])
    p["plank_target"] = p["plank_len"] - p["plank_splice"] / 100.0 * (p["plank_len"] - p["plank_min"])
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


def frame_v(frame):
    """The frame's v as a vector: world Z for a wall, up the slope for a pitched face."""
    return frame.get("v") or WORLD_UP


def v_to_z(frame, v):
    """The world height of the frame's local v (along u = 0, on the face). Up a slope a
    millimetre of v is less than a millimetre of height; a level face has no height to
    read, so its v stands in for one and comes back unchanged through z_to_v."""
    rise = frame_v(frame)[2]
    return float(frame["origin"][2]) + float(v) * (rise if abs(rise) > 1e-6 else 1.0)


def z_to_v(frame, z):
    """The frame's local v at a world height: the inverse of v_to_z."""
    rise = frame_v(frame)[2]
    return (float(z) - float(frame["origin"][2])) / (rise if abs(rise) > 1e-6 else 1.0)


def world_to_frame(frame, p):
    """IFC world [x, y, z] → frame-local (u, v, depth). The inverse of frame_to_world for
    an orthonormal frame."""
    o, U, N = frame["origin"], frame["u"], frame["n"]
    V = frame.get("v") or WORLD_UP
    q = [p[0] - o[0], p[1] - o[1], p[2] - o[2]]
    dot = lambda a: a[0] * q[0] + a[1] * q[1] + a[2] * q[2]   # noqa: E731
    return [dot(U), dot(V), dot(N)]
