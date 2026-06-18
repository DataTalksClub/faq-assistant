"""Offline smoke test of the Lambda handler (routing, auth, pipeline).

Uses the prebuilt index and a stubbed chat call, so it needs no network or
OpenAI key. Run after `build_search_index.py`.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("FAQ_ASSISTANT_SHARED_SECRET", "test-secret")
os.environ.setdefault("SEARCH_INDEX_PATH", str(ROOT / "artifacts" / "search" / "search-index.zsx"))

from faq_assistant import handler  # noqa: E402
from faq_assistant.models import QueryRewrite  # noqa: E402


def stub_chat(messages, output_model, max_tokens, temperature, model=None):
    if output_model is QueryRewrite:
        content = {"query": "docker compose"}
    else:
        content = {"answer": "Use docker compose up.", "found_answer": True, "source_ids": []}
    return {
        "choices": [{"message": {"content": json.dumps(content)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def call(method, path="/", body=None, secret=None):
    handler.make_openai_chat = lambda config, usage=None: stub_chat
    event = {"requestContext": {"http": {"method": method, "path": path}}, "headers": {}}
    if secret is not None:
        event["headers"]["x-faq-assistant-secret"] = secret
    if body is not None:
        event["body"] = json.dumps(body)
    return handler.lambda_handler(event)


def main() -> int:
    assert call("GET", "/health")["statusCode"] == 200, "health check failed"
    assert call("OPTIONS")["statusCode"] == 204, "CORS preflight failed"
    assert call("POST", "/ask", {"question": "x"})["statusCode"] == 401, "missing secret should be 401"
    assert call("POST", "/ask", {"question": "x"}, secret="wrong")["statusCode"] == 401, "bad secret should be 401"
    assert call("POST", "/ask", {"question": ""}, secret="test-secret")["statusCode"] == 400, "empty question should be 400"

    response = call("POST", "/ask", {"question": "how do I start docker compose", "scope": "docs"}, secret="test-secret")
    assert response["statusCode"] == 200, f"happy path failed: {response['body']}"
    payload = json.loads(response["body"])
    assert payload["rewritten_query"] == "docker compose", payload["rewritten_query"]
    assert payload["results"], "expected retrieved results"
    assert payload["answer"].startswith("Use docker compose"), payload["answer"]
    assert payload["found_answer"] is True, payload["found_answer"]
    assert isinstance(payload["sources"], list), "expected structured sources list"

    print(f"OK: handler routing/auth/pipeline ({len(payload['results'])} results retrieved)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
