"""Unit tests for the Lambda handler (mocked chat + index)."""

import base64
import json

import pytest

from conftest import mock_chat, mock_index, record

from faq_assistant import handler


@pytest.fixture(autouse=True)
def wire_handler(monkeypatch):
    """Point the handler at a mocked index + chat and set the shared secret."""
    monkeypatch.setattr(handler, "_INDEX", mock_index([record(id="faq:1", source_type="faq", title="T", url="U")]))
    monkeypatch.setattr(handler, "make_openai_chat",
                        lambda config, usage=None: mock_chat(rewrite="q", source_ids=["faq:1"]))
    monkeypatch.setenv("FAQ_ASSISTANT_SHARED_SECRET", "s3cret")


def event(method="POST", path="/ask", body=None, secret=None, is_b64=False):
    ev = {"requestContext": {"http": {"method": method, "path": path}}, "headers": {}}
    if secret is not None:
        ev["headers"]["x-faq-assistant-secret"] = secret
    if body is not None:
        raw = json.dumps(body)
        if is_b64:
            raw = base64.b64encode(raw.encode()).decode()
            ev["isBase64Encoded"] = True
        ev["body"] = raw
    return ev


def test_health_ok():
    resp = handler.lambda_handler(event("GET", "/health"))
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["ok"] is True


def test_options_preflight():
    assert handler.lambda_handler(event("OPTIONS"))["statusCode"] == 204


def test_unknown_get_is_404():
    assert handler.lambda_handler(event("GET", "/nope"))["statusCode"] == 404


def test_missing_secret_is_401():
    assert handler.lambda_handler(event(body={"question": "x"}))["statusCode"] == 401


def test_wrong_secret_is_401():
    assert handler.lambda_handler(event(body={"question": "x"}, secret="nope"))["statusCode"] == 401


def test_missing_question_is_400():
    resp = handler.lambda_handler(event(body={"question": "   "}, secret="s3cret"))
    assert resp["statusCode"] == 400


def test_happy_path_returns_structured_answer():
    resp = handler.lambda_handler(event(body={"question": "How?", "scope": "course", "course": "llm-zoomcamp"}, secret="s3cret"))
    assert resp["statusCode"] == 200
    payload = json.loads(resp["body"])
    assert payload["found_answer"] is True
    assert payload["sources"] == [{"id": "faq:1", "source": "faq", "title": "T", "url": "U"}]
    assert "results" not in payload


def test_base64_encoded_body_is_decoded():
    resp = handler.lambda_handler(event(body={"question": "How?"}, secret="s3cret", is_b64=True))
    assert resp["statusCode"] == 200


def test_handler_errors_become_500(monkeypatch):
    monkeypatch.setattr(handler, "make_openai_chat", lambda config, usage=None: _boom)
    resp = handler.lambda_handler(event(body={"question": "How?"}, secret="s3cret"))
    assert resp["statusCode"] == 500
    assert "error" in json.loads(resp["body"])


def _boom(*args, **kwargs):
    raise RuntimeError("kaboom")
