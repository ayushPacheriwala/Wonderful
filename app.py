"""
Flask web server for the healthcare doctor search chat interface.

Routes:
    GET  /              — serve the UI
    POST /api/chat      — {"messages": [...]} → {"reply": "...", "doctors": [...]}
    POST /api/transcribe — multipart audio → {"text": "..."}
"""

import json
import os
import re
import sqlite3

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from openai import OpenAI

from search_doctor import DB_PATH, search_by_fields

load_dotenv()

FPT_API_KEY   = os.getenv("FPT_API_KEY", "")
FPT_MODEL     = os.getenv("FPT_MODEL", "Qwen3-32B")
FPT_BASE_URL  = os.getenv("FPT_BASE_URL", "https://mkp-api.fptcloud.com")
FPT_STT_MODEL = os.getenv("FPT_STT_MODEL", "whisper-large-v3-turbo")
STT_URL       = "https://mkp-api.fptcloud.com/v1/audio/transcriptions"

app = Flask(__name__, static_folder="static")

# ---------------------------------------------------------------------------
# Conversational agent system prompt
# ---------------------------------------------------------------------------

CHAT_SYSTEM = """\
You are a helpful healthcare assistant for a Romanian doctor directory.
Your job is to help users find the right doctor through natural conversation.
Always reply with valid JSON only — no other text, no markdown, no preamble.

When you have enough information to search (at least one of: doctor name, medical specialty, or clinic name), respond with:
{"action":"search","name":"...","speciality":"...","clinic":"...","location":"...","min_experience":10,"min_rating":4.5,"open_day":"Saturday","open_time":"18:00","intent":"...","message":"One friendly sentence telling the user you're looking it up"}

When you need more information, respond with:
{"action":"ask","message":"Your friendly clarifying question (one question at a time)"}

Field rules:
- name: doctor's full or partial name, no titles (e.g. "Ionut Dumitrescu")
- speciality: canonical English specialty (e.g. "Cardiology", "Dermatology", "Psychiatry", "Obstetrics and Gynecology")
- clinic: clinic or hospital name if mentioned
- location: Romanian city or county if mentioned
- min_experience: integer years when user specifies a minimum (e.g. "at least 10 years", "10+ years" → 10). Omit if not mentioned.
- min_rating: float between 3.0 and 5.0 when user specifies a minimum (e.g. "4.5+ rating", "rated 4 or higher" → 4.0). Omit if not mentioned.
- open_day: full English day name when the user wants the clinic open on that day (e.g. "Saturday"). Omit otherwise.
- open_time: 24-hour HH:MM when the user wants the clinic open at that time (e.g. "6pm" → "18:00", "after 5pm" → "17:00"). Omit otherwise.
- intent: "recommend" when user wants a suggestion, "specific" when asking for a named doctor, "type" for any doctor of a given type
- Omit fields that were not mentioned — do not guess
- Keep messages warm, concise, and professional
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """Parse JSON from model output, stripping thinking blocks and markdown fences."""
    # Strip Qwen3 thinking blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))
    obj = re.search(r"\{.*\}", text, re.DOTALL)
    if obj:
        return json.loads(obj.group(0))
    raise ValueError(f"No JSON in model output: {text!r}")


def _filter_phrases(params: dict) -> list:
    """Human-readable descriptions of the active numeric/availability filters."""
    out = []
    if params.get("min_experience") is not None:
        out.append(f"with at least {params['min_experience']} years of experience")
    if params.get("min_rating") is not None:
        out.append(f"rated {params['min_rating']}+")
    if params.get("open_day"):
        out.append(f"open on {params['open_day']}")
    if params.get("open_time"):
        out.append(f"open at {params['open_time']}")
    return out


def _recommendation_text(results: list, params: dict) -> str:
    """Generate a natural language recommendation from search results."""
    filters = _filter_phrases(params)
    filter_suffix = (" " + " and ".join(filters)) if filters else ""

    if not results:
        parts = []
        if params.get("speciality"):
            parts.append(params["speciality"] + " doctors")
        if params.get("location"):
            parts.append(f"in {params['location']}")
        what = " ".join(parts) if parts else "doctors matching your request"
        if filters:
            return f"I couldn't find any {what}{filter_suffix}. Try loosening the filters."
        return f"I couldn't find any {what}. Could you try a different location or specialty?"

    top   = results[0]
    name  = f"**Dr. {top['full_name']}**"
    intent = params.get("intent", "recommend")

    if intent == "specific":
        if len(results) == 1:
            reply = f"I found {name} — a {top['speciality']} specialist at {top['clinic_name']} in {top['location']}, rated {top['rating']}/5."
        else:
            reply = f"I found {len(results)} doctors with that name. The highest-rated is {name}, a {top['speciality']} specialist in {top['location']} ({top['rating']}/5). All results are shown on the right."

    elif intent == "type":
        reply = (f"The top {top['speciality']} doctor I found is {name} at {top['clinic_name']} "
                 f"in {top['location']}, with a {top['rating']}/5 rating and {top['years_experience']} years of experience.")
        if len(results) > 1:
            reply += f" {len(results) - 1} other{'s' if len(results) > 2 else ''} are also shown on the right."

    else:  # recommend
        reply = (f"My top recommendation is {name}, a {top['speciality']} specialist at "
                 f"{top['clinic_name']} in {top['location']}. "
                 f"They have a {top['rating']}/5 rating and {top['years_experience']} years of experience.")
        if len(results) > 1:
            reply += f" I've also listed {len(results) - 1} other option{'s' if len(results) > 2 else ''} on the right."

    if filter_suffix:
        reply += f" (Filtered{filter_suffix}.)"

    return reply


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    data     = request.get_json(silent=True) or {}
    messages = data.get("messages", [])
    if not messages:
        return jsonify({"error": "messages required"}), 400

    llm = OpenAI(api_key=FPT_API_KEY, base_url=FPT_BASE_URL)

    try:
        resp = llm.chat.completions.create(
            model=FPT_MODEL,
            messages=[{"role": "system", "content": CHAT_SYSTEM}] + messages,
            temperature=0.2,
        )
        raw    = resp.choices[0].message.content or ""
        parsed = _extract_json(raw)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if parsed.get("action") == "search":
        conn    = sqlite3.connect(DB_PATH)
        results = search_by_fields(
            conn,
            name=parsed.get("name"),
            speciality=parsed.get("speciality"),
            clinic=parsed.get("clinic"),
            location=parsed.get("location"),
            intent=parsed.get("intent", "recommend"),
            min_experience=parsed.get("min_experience"),
            min_rating=parsed.get("min_rating"),
            open_day=parsed.get("open_day"),
            open_time=parsed.get("open_time"),
            limit=5,
        )
        conn.close()
        # Lead with the model's own preamble sentence, then append our rich recommendation
        preamble = parsed.get("message", "")
        rec      = _recommendation_text(results, parsed)
        reply    = f"{preamble} {rec}".strip() if preamble else rec
        return jsonify({"reply": reply, "doctors": results})

    return jsonify({"reply": parsed.get("message", "Could you tell me more?"), "doctors": []})


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
        return jsonify({"text": resp.json().get("text", "")})
    except requests.HTTPError as e:
        return jsonify({"error": f"STT error {e.response.status_code}: {e.response.text}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    app.run(debug=False, port=port)
