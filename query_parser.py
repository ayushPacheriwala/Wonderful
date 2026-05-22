"""
Parses a natural language healthcare query into structured search fields
using a Qwen model hosted on FPT AI Marketplace.

The FPT endpoint is OpenAI SDK-compatible when using:
    base_url = "https://mkp-api.fptcloud.com"   (no /v1 suffix)

Configuration (environment variables):
    FPT_API_KEY   — Bearer token from the FPT AI Marketplace dashboard
    FPT_MODEL     — model ID (default: Qwen2.5-7B-Instruct)
                    Other options: QwQ-32B, Qwen2.5-Coder-32B-Instruct
    FPT_BASE_URL  — override the base URL if needed
                    default: https://mkp-api.fptcloud.com
"""

import json
import os
import re

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()  # reads .env in the current working directory, if present

FPT_BASE_URL = os.getenv("FPT_BASE_URL", "https://mkp-api.fptcloud.com")
FPT_API_KEY  = os.getenv("FPT_API_KEY", "")
FPT_MODEL    = os.getenv("FPT_MODEL", "Qwen3-32B")

_SYSTEM = """\
You are an intent-extraction assistant for a Romanian healthcare directory.

Given a natural language query about finding a doctor, extract search fields and return ONLY a valid JSON object — no markdown, no explanation, no extra text.

Fields to extract:
  "name"       — doctor's full or partial name if mentioned (strip "Dr." / "Doctor")
  "speciality" — medical specialty in canonical English (e.g. "Cardiology", "Dermatology")
  "clinic"     — clinic or hospital name if mentioned
  "location"   — city or region if mentioned
  "intent"     — REQUIRED; one of:
                   "specific"  (user wants a named doctor)
                   "type"      (user wants any doctor of a given specialty/clinic)
                   "recommend" (user wants a recommendation)

Only include fields that are clearly present in the query.

Examples:

Query: "I want to speak to Dr. Ionut Dumitrescu from Cardiology"
{"name": "Ionut Dumitrescu", "speciality": "Cardiology", "intent": "specific"}

Query: "I want to speak to the Cardiology doctor at Clinica Cluj-Napoca Care"
{"speciality": "Cardiology", "clinic": "Clinica Cluj-Napoca Care", "intent": "type"}

Query: "Recommend me a doctor from Dermatology near Cluj"
{"speciality": "Dermatology", "location": "Cluj", "intent": "recommend"}
"""


def _extract_json(text: str) -> dict:
    """
    Extract a JSON object from the model's raw output.
    Handles bare JSON, markdown code fences, and prose-prefixed output.
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))
    obj = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if obj:
        return json.loads(obj.group(0))
    raise ValueError(f"No JSON object found in model output: {text!r}")


def _get_client() -> OpenAI:
    if not FPT_API_KEY:
        raise EnvironmentError(
            "FPT_API_KEY is not set. Export it before running:\n"
            "  export FPT_API_KEY=your_token_here"
        )
    return OpenAI(api_key=FPT_API_KEY, base_url=FPT_BASE_URL)


def parse_query(text: str) -> dict:
    """
    Parse a natural language healthcare query into structured search fields.

    Returns a dict with a subset of:
        name, speciality, clinic, location, intent
    Only fields present in the query are included (except intent, always set).

    Raises:
        EnvironmentError   — FPT_API_KEY not set
        openai.APIError    — API call failed
        ValueError         — model returned unparseable output
    """
    client = _get_client()
    response = client.chat.completions.create(
        model=FPT_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": text},
        ],
        temperature=0.0,
    )
    raw = response.choices[0].message.content
    return _extract_json(raw)
