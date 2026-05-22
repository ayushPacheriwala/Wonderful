"""
Flask web server for the healthcare doctor search interface.

Routes:
    GET  /                  — serve the UI
    POST /api/search        — JSON {"query": "..."} → search results
    POST /api/transcribe    — multipart audio file → {"text": "..."}
"""

import os

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

from search_doctor import handle_query

load_dotenv()

FPT_API_KEY   = os.getenv("FPT_API_KEY", "")
FPT_STT_MODEL = os.getenv("FPT_STT_MODEL", "whisper-large-v3-turbo")
STT_URL       = "https://mkp-api.fptcloud.com/v1/audio/transcriptions"

app = Flask(__name__, static_folder="static")


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/search", methods=["POST"])
def search():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400
    try:
        results = handle_query(query, limit=5)
        return jsonify({"query": query, "results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/transcribe", methods=["POST"])
def transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "audio file is required"}), 400

    audio = request.files["audio"]
    try:
        resp = requests.post(
            STT_URL,
            headers={"Authorization": f"Bearer {FPT_API_KEY}"},
            files={"file": (audio.filename or "audio.webm", audio.stream, audio.mimetype)},
            data={"model": FPT_STT_MODEL, "language": "en", "response_format": "json"},
            timeout=60,
        )
        resp.raise_for_status()
        text = resp.json().get("text", "")
        return jsonify({"text": text})
    except requests.HTTPError as e:
        return jsonify({"error": f"STT API error {e.response.status_code}: {e.response.text}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    app.run(debug=False, port=port)
