"""
CladForge — Flask server.

Serves the UI and the Python engine sources (so Pyodide can import them in the
browser), and mirrors every engine entry point as a JSON API for server-side
use. The browser runs extraction, coursing and DXF in Pyodide; IFC export is
requested from /api/download first and falls back to the IfcOpenShell WASM
wheel when the page is served statically.
"""

import datetime
import os
import tempfile

from flask import Flask, jsonify, make_response, render_template, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException

from cladding_preview import check_rules, generate_preview
from dxf_generator import meshes_to_dxf
from fabric_extract import extract_elevation
from ifc_generator import meshes_to_ifc
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


def _stamp():
    return datetime.datetime.now().strftime("%d-%m-%y")


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


@app.route("/api/extract", methods=["POST"])
def api_extract():
    payload = request.get_json(force=True) or {}
    return jsonify({"success": True, "elevation": extract_elevation(payload)})


@app.route("/api/preview", methods=["POST"])
def api_preview():
    params = request.get_json(force=True) or {}
    out = generate_preview(params)
    return jsonify({"success": True, "geometry": out["geometry"], "dimensions": out["dimensions"],
                    "info": out["info"]})


@app.route("/api/check", methods=["POST"])
def api_check():
    params = request.get_json(force=True) or {}
    return jsonify({"success": True, "checks": check_rules(params)})


@app.route("/api/download", methods=["POST"])
def api_download():
    """IFC4X3 export. Trimming is always applied before export."""
    params = dict(request.get_json(force=True) or {})
    params["trim"] = True
    out = generate_preview(params)
    path = meshes_to_ifc(out["geometry"], params, out["info"])
    resp = send_file(path, as_attachment=True, download_name="CladForge_%s.ifc" % _stamp(),
                     mimetype="application/x-step")
    resp.call_on_close(lambda: _unlink(path))
    return resp


@app.route("/api/download_dxf", methods=["POST"])
def api_download_dxf():
    params = dict(request.get_json(force=True) or {})
    params["trim"] = True
    out = generate_preview(params)
    path = meshes_to_dxf(out["geometry"], params)
    resp = send_file(path, as_attachment=True, download_name="CladForge_%s.dxf" % _stamp(),
                     mimetype="application/dxf")
    resp.call_on_close(lambda: _unlink(path))
    return resp


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
