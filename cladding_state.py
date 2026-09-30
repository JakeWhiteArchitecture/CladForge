"""CladForge — a saved state as XML: every chain, its elevations, the surfaces they are
attached to, and every setting, so a session can be closed and picked up again.

The browser gathers the state as plain data (app.js snapshotState) and this module
writes it as XML and reads it back, exactly: numbers keep every digit, and a value comes
back as the type it went in as. Each element says its type (t="num", "str", "bool",
"null", "list", "object", "nums", "points"); a list of numbers is written "a b c" and a list
of points "x,y x,y".
The surfaces are stored as extracted (frame, outline, openings, abutments), so a state
loads without the IFC and without running extraction again.

Pure Python (xml.etree), so it runs in Pyodide with the rest of the engine.
"""

import math
import re
import xml.etree.ElementTree as ET

FORMAT = "cladforge-state"
VERSION = 1
ROOT = "CladForgeState"
NOTE = ("CladForge saved state: chains, elevations, the wall surfaces they are attached to, and "
        "settings. Setting-out information, not a quantity take-off for pricing.")
# List items are named for what they hold where the parent says so; otherwise "i".
ITEM = {"chains": "chain", "elevations": "elevation", "members": "elevation", "polygons": "polygon",
        "abutments": "abutment", "picks": "pick", "holes": "hole", "notches": "notch",
        "warnings": "warning", "panel_joints": "joint", "manual": "level"}
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


class StateError(ValueError):
    """The file is not a CladForge state this version can read."""


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _is_points(v):
    return (isinstance(v, list) and v and all(isinstance(q, list) and 2 <= len(q) <= 3 and all(_num(c) for c in q)
                                              for q in v))


def _fmt(x):
    if isinstance(x, float) and not math.isfinite(x):
        raise StateError("a number in the state is not finite: %r" % x)
    return repr(x)


def _encode(value, tag, key=None):
    el = ET.Element(tag if (key is None or _NAME.match(key)) else "entry")
    if key is not None and not _NAME.match(key):
        el.set("key", key)
    if value is None:
        el.set("t", "null")
    elif isinstance(value, bool):
        el.set("t", "bool")
        el.text = "true" if value else "false"
    elif _num(value):
        el.set("t", "num")
        el.text = _fmt(value)
    elif isinstance(value, str):
        el.set("t", "str")
        el.text = value
    elif isinstance(value, dict):
        el.set("t", "object")
        for k, v in value.items():
            el.append(_encode(v, str(k), str(k)))
    elif isinstance(value, list) and value and all(_num(c) for c in value):
        el.set("t", "nums")
        el.text = " ".join(_fmt(c) for c in value)
    elif _is_points(value):
        el.set("t", "points")
        el.text = " ".join(",".join(_fmt(c) for c in q) for q in value)
    elif isinstance(value, (list, tuple)):
        el.set("t", "list")
        child = ITEM.get(key or tag, "i")
        for v in value:
            el.append(_encode(v, child))
    else:
        raise StateError("cannot save a value of type %s" % type(value).__name__)
    return el


def _number(s):
    s = s.strip()
    return int(s) if re.match(r"^-?\d+$", s) else float(s)


def _decode(el):
    t = el.get("t")
    text = el.text or ""
    if t == "null":
        return None
    if t == "bool":
        return text.strip() == "true"
    if t == "num":
        return _number(text)
    if t == "str":
        return text
    if t == "object":
        return {(c.get("key") if c.tag == "entry" and c.get("key") is not None else c.tag): _decode(c) for c in el}
    if t == "list":
        return [_decode(c) for c in el]
    if t == "nums":
        return [_number(c) for c in text.split()]
    if t == "points":
        return [[_number(c) for c in q.split(",")] for q in text.split()]
    raise StateError("unknown value type %r on <%s>" % (t, el.tag))


def to_xml(state):
    """The state (a dict from the browser) as an XML document string."""
    if not isinstance(state, dict):
        raise StateError("a state is a mapping")
    root = ET.Element(ROOT, {"format": FORMAT, "version": str(VERSION)})
    root.append(ET.Comment(" " + NOTE + " "))
    for k, v in state.items():
        root.append(_encode(v, k, k))
    ET.indent(root, space=" ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def from_xml(text):
    """The state from an XML string written by to_xml. Raises StateError on anything that
    is not a CladForge state, or one from a newer version."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as err:
        raise StateError("not an XML file: %s" % err)
    if root.tag != ROOT or root.get("format") != FORMAT:
        raise StateError("not a CladForge state file")
    try:
        version = int(root.get("version", "0"))
    except ValueError:
        raise StateError("the state's version is not a number")
    if version > VERSION or version < 1:
        raise StateError("saved by a newer CladForge (state version %d); this one reads version %d" % (version, VERSION))
    try:
        return {c.tag: _decode(c) for c in root if isinstance(c.tag, str)}
    except (ValueError, TypeError) as err:
        raise StateError("the state is damaged: %s" % err)


def load(text):
    """For the browser: {"ok": True, "state": ...} or {"ok": False, "error": ...}."""
    try:
        return {"ok": True, "state": from_xml(text)}
    except StateError as err:
        return {"ok": False, "error": str(err)}
