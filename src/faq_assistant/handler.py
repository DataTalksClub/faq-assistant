"""AWS Lambda entry point (behind a Function URL).

Authenticated with the same shared-secret header as the old Worker ``/ask``
endpoint: callers (e.g. the Slack ack Lambda) POST ``{question, scope, course}``
and get the answer JSON back. Slack signature handling and posting live in the
ack Lambda, not here.
"""

import base64
import hmac
import json
import os
import re

from faq_assistant.answering import answer_question, make_openai_chat
from faq_assistant.generated_config import CONFIG
from faq_assistant.search_index import load_search_index

_INDEX = None

_CORS_HEADERS = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "access-control-allow-headers": "content-type,x-faq-assistant-secret",
}


def _index():
    global _INDEX
    if _INDEX is None:
        _INDEX = load_search_index(os.environ.get("SEARCH_INDEX_PATH", "search-index.zsx"))
    return _INDEX


def lambda_handler(event, context=None):
    http = (event.get("requestContext") or {}).get("http") or {}
    method = str(http.get("method") or "GET").upper()
    path = str(http.get("path") or "/")

    if method == "OPTIONS":
        return _response(204, None)

    if method == "GET" and path.rstrip("/") in ("", "/health"):
        return _response(200, {"ok": True, "app": CONFIG["app"]["name"]})

    if method != "POST":
        return _response(404, {"error": "Not found"})

    if not _authorized(event):
        return _response(401, {"error": "Unauthorized"})

    body = _parse_body(event)
    question = _clean_question(str(body.get("question", "")))
    if not question:
        return _response(400, {"error": "`question` is required"})

    scope = str(body.get("scope") or "docs")
    course = body.get("course")

    usage: list[dict] = []
    chat = make_openai_chat(CONFIG, usage)
    try:
        result = answer_question(
            CONFIG, _index(), chat, question, scope, course, source="api", usage=usage
        )
    except Exception as error:  # noqa: BLE001 - return the error to the caller
        return _response(500, {"error": str(error) or "Unknown error"})
    return _response(200, result)


def _authorized(event) -> bool:
    secret_env = CONFIG.get("api", {}).get("shared_secret_env", "")
    secret = os.environ.get(secret_env, "") if secret_env else ""
    if not secret:
        return True  # no shared secret configured -> open (matches the old Worker)
    received = _header(event, "x-faq-assistant-secret")
    return hmac.compare_digest(secret, received)


def _header(event, name: str) -> str:
    headers = event.get("headers") or {}
    return str(headers.get(name) or headers.get(name.lower()) or "")


def _parse_body(event) -> dict:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _clean_question(text: str) -> str:
    text = re.sub(r"<@[A-Z0-9]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _response(status: int, data):
    headers = {"content-type": "application/json; charset=utf-8", **_CORS_HEADERS}
    body = "" if data is None else json.dumps(data)
    return {"statusCode": status, "headers": headers, "body": body}
