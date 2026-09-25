"""
CladForge — Flask server.

Serves the UI and the Python engine sources, so Pyodide can import them in the
browser. Everything else runs client-side: extraction, coursing, checks and both
exports. The one route that does real work is /api/import, the IfcOpenShell
fallback for models web-ifc cannot build.
"""

import os
import tempfile

from flask import Flask, jsonify, make_response, render_template, request, send_from_directory
from werkzeug.exceptions import HTTPException

from ifc_import import import_ifc

ROOT = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"),
            static_folder=os.path.join(ROOT, "static"))
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024

PY_MODULES = ("cladding_constants", "cladding_primitives", "cladding_geometry", "cladding_booleans",
              "cladding_checks", "cladding_preview", "fabric_extract", "dxf_generator",
              "ifc_generator")


def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.route("/")
def index():
    return _no_store(make_response(render_template("index.html")))


@app.route("/<name>.py")
def serve_module(name):
    """Python source for Pyodide. Only the engine modules are exposed."""
    if name not in PY_MODULES:
        return jsonify({"success": False, "error": "unknown module"}), 404
    return _no_store(send_from_directory(ROOT, name + ".py", mimetype="text/plain"))


@app.route("/api/import", methods=["POST"])
def api_import():
    """Server-side IFC tessellation, the fallback when web-ifc cannot build a model."""
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"success": False, "error": "no file uploaded"}), 400
    tmp = tempfile.NamedTemporaryFile(suffix=".ifc", delete=False)
    try:
        upload.save(tmp.name)
        tmp.close()
        return jsonify(dict(import_ifc(tmp.name), success=True))
    finally:
        _unlink(tmp.name)


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


@app.errorhandler(Exception)
def on_error(exc):
    if isinstance(exc, HTTPException):
        return exc
    app.logger.exception("request failed")
    return jsonify({"success": False, "error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=os.environ.get("FLASK_DEBUG") == "1")
