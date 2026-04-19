"""
==========================================================
  NexMed AI - app.py
  Flask web server. Thin HTTP wrapper around main_chain.py.
  ----------------------------------------------------------
  Responsibilities:
    - Serve the frontend (index.html, script.js, style.css)
    - Expose /analyze  (Phase 1 - vision)
    - Expose /reason   (Phase 2 - reasoning)
    - Handle file uploads + JSON plumbing
  ----------------------------------------------------------
  Run:   python app.py
  Open:  http://localhost:5000
==========================================================
"""

import os
import tempfile
import traceback

from flask import Flask, request, jsonify, send_from_directory

# Pure ML logic lives in main_chain.py - we just import what we need
from main_chain import (
    vision_extractor_factory,
    reasoning_factory,
    parse_features,
    parse_report,
)


# ==========================================================
# APP SETUP
# ==========================================================
APP_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=APP_DIR, static_url_path="")


# ==========================================================
# STATIC ROUTES - serve the frontend
# ==========================================================
@app.route("/")
def index():
    return send_from_directory(APP_DIR, "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    """Serve script.js, style.css, and any other static asset."""
    return send_from_directory(APP_DIR, filename)


# ==========================================================
# API: PHASE 1 - FEATURE EXTRACTION
# ==========================================================
@app.route("/analyze", methods=["POST"])
def analyze():
    """
    Request (multipart/form-data):
      image          -> file (X-ray)
      vision_engine  -> "Groq" or "Local"
    Response (JSON):
      {
        features:     { BONE: "...", FRACTURE: "...", ... },
        features_raw: "...",   # sent back so /reason can use it verbatim
        engine:       "Groq" | "Local"
      }
    """
    try:
        if "image" not in request.files:
            return jsonify({"error": "No image uploaded"}), 400

        engine = request.form.get("vision_engine", "Groq")
        img_file = request.files["image"]

        # Save upload to a temp file -> vision functions need a path, not bytes
        suffix = os.path.splitext(img_file.filename)[1] or ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            img_file.save(tmp.name)
            tmp_path = tmp.name

        try:
            raw = vision_extractor_factory(tmp_path, model_choice=engine)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        return jsonify({
            "features":     parse_features(raw),
            "features_raw": raw,
            "engine":       engine,
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ==========================================================
# API: PHASE 2 - CLINICAL REASONING
# ==========================================================
@app.route("/reason", methods=["POST"])
def reason():
    """
    Request (JSON):
      {
        features_raw:     "...",           # raw text from /analyze
        reasoning_engine: "Groq" | "Local"
      }
    Response (JSON):
      {
        report:     { "CLINICAL SYNTHESIS": "...", ... },
        report_raw: "...",
        engine:     "Groq" | "Local"
      }
    """
    try:
        data = request.get_json(silent=True) or {}
        features_raw = (data.get("features_raw") or "").strip()
        engine = data.get("reasoning_engine", "Groq")

        if not features_raw:
            return jsonify({"error": "Missing features_raw"}), 400

        raw = reasoning_factory(features_raw, model_choice=engine)

        return jsonify({
            "report":     parse_report(raw),
            "report_raw": raw,
            "engine":     engine,
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ==========================================================
# ENTRY POINT
# ==========================================================
if __name__ == "__main__":
    print("=" * 58)
    print("  NexMed AI - Clinical Diagnostic Pipeline")
    print("  Serving UI + API on  ->  http://localhost:5000")
    print("=" * 58)
    app.run(host="0.0.0.0", port=5000, debug=False)