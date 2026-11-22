"""Every engine module the app loads in the browser (Pyodide) imports only modules that are
loaded too: a module missing from app.py's PY_MODULES or app.js's list works under pytest and
the Flask server but fails in the browser with ModuleNotFoundError."""
import ast
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _browser_list():
    js = open(os.path.join(ROOT, "static", "app.js")).read()
    block = re.search(r"const modules = \[(.*?)\];", js, re.S).group(1)
    return set(re.findall(r"'([a-z_0-9]+)'", block))


def _server_list():
    tree = ast.parse(open(os.path.join(ROOT, "app.py")).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "PY_MODULES" for t in node.targets):
            return set(ast.literal_eval(node.value))
    raise AssertionError("PY_MODULES not found in app.py")


def _local_imports(name):
    tree = ast.parse(open(os.path.join(ROOT, name + ".py")).read())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module.split(".")[0])
    return {m for m in out if os.path.exists(os.path.join(ROOT, m + ".py"))}


def test_the_browser_and_the_server_load_the_same_modules():
    assert _browser_list() == _server_list()


def test_every_module_imported_by_a_loaded_module_is_loaded_too():
    loaded = _browser_list()
    missing = {(m, dep) for m in loaded for dep in _local_imports(m) if dep not in loaded}
    assert not missing, "imported but not loaded in the browser: %s" % sorted(missing)
