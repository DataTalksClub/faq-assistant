"""Offline smoke test of the Lambda handler (routing, auth, pipeline).

The pipeline assertions run against a stubbed chat call *and* a stubbed index,
so they are deterministic and need no network or OpenAI key. We deliberately do
not retrieve from the live corpus here: it is rebuilt daily and drifts, so
asserting that a specific topic answers above ``min_score`` turns a content
change into a spurious deploy failure. A separate, content-agnostic check
confirms the freshly built real index loads and is non-empty. Run after
`build_search_index.py`.
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
from faq_assistant.search_index import load_search_index  # noqa: E402


def stub_chat(messages, output_model, max_tokens, temperature, model=None):
    if output_model is QueryRewrite:
        content = {"query": "docker compose"}
    else:
        content = {"answer": "Use docker compose up.", "found_answer": True, "source_ids": []}
    return {
        "choices": [{"message": {"content": json.dumps(content)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


class StubIndex:
    """Returns one deterministic record so the pipeline test does not depend on
    live corpus content. Score clears any ``min_score``."""

    def search(self, query, filter_dict=None, boost_dict=None, num_results=6):
        return [
            {
                "id": "doc-1",
                "score": 1.0,
                "source_type": "docs",
                "course": "",
                "section": "Environment",
                "title": "Docker Compose",
                "text": "Run docker compose up to start the services.",
                "url": "https://datatalks.club/docs/docker.html",
            }
        ]


def call(method, path="/", body=None, secret=None):
    handler.make_openai_chat = lambda config, usage=None: stub_chat
    handler._INDEX = StubIndex()
    event = {"requestContext": {"http": {"method": method, "path": path}}, "headers": {}}
    if secret is not None:
        event["headers"]["x-faq-assistant-secret"] = secret
    if body is not None:
        event["body"] = json.dumps(body)
    return handler.lambda_handler(event)


def check_real_index() -> None:
    """Content-agnostic canary: the freshly built index loads and is non-empty."""
    index = load_search_index(os.environ["SEARCH_INDEX_PATH"])
    results = index.search(query="course", filter_dict=None, boost_dict={}, num_results=5)
    assert results, "real index returned no results for a broad query (empty/broken corpus?)"


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
    assert payload["answer"].startswith("Use docker compose"), payload["answer"]
    assert payload["found_answer"] is True, payload["found_answer"]
    assert isinstance(payload["sources"], list), "expected structured sources list"

    check_real_index()

    print("OK: handler routing/auth/pipeline + index canary")
    return 0


if __name__ == "__main__":
    sys.exit(main())
