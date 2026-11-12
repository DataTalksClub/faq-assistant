"""Public live state reaches the answer without changing retrieval or exposing students."""

import json

import pytest

from conftest import mock_chat, mock_index
from faq_assistant import handler
from faq_assistant.answering import answer_question
from faq_assistant.course_state import validate_course_state
from test_handler import event


@pytest.fixture
def snapshot():
    return {
        "schema_version": 1,
        "fetched_at": "2026-11-12T12:00:00Z",
        "course": {
            "slug": "ml-2026", "title": "ML Zoomcamp 2026",
            "source_url": "https://courses.datatalks.club/ml-2026/",
            "starts_at": None, "start_date": "2026-09-01", "end_date": None,
            "finished": False,
        },
        "homeworks": [{
            "slug": "homework-1", "title": "Homework 1", "state": "CLOSED",
            "starts_at": None, "due_date": "2026-10-01T23:59:59Z",
            "source_url": "https://courses.datatalks.club/ml-2026/homework/homework-1/",
        }],
        "projects": [{
            "slug": "project-1", "title": "Project 1", "state": "PEER_REVIEWING",
            "starts_at": None, "submission_due_date": "2026-11-10T23:59:59Z",
            "peer_review_due_date": "2026-11-17T23:59:59Z",
            "peer_review_open": True, "peer_review_opens_at": None,
            "assignments_created": True,
            "source_url": "https://courses.datatalks.club/ml-2026/project/project-1/",
        }],
    }


def test_live_snapshot_is_answer_context_and_resolves_source(cfg, snapshot):
    chat = mock_chat(answer="Peer review is open through November 17.", source_ids=["cmp:course-state"])
    index = mock_index([])
    result = answer_question(cfg, index, chat, "Have peer reviews been assigned?", "course",
                             "machine-learning-zoomcamp", course_state=snapshot)
    messages = chat.call_args_list[-1].args[0]
    assert "prefer it over indexed documents" in messages[0]["content"]
    assert "not whether this student" in messages[0]["content"]
    assert '"assignments_created": true' in messages[1]["content"]
    assert '"peer_review_opens_at": null' in messages[1]["content"]
    assert "cmp:course-state" not in chat.call_args_list[0].args[0][1]["content"]
    assert result["sources"] == [{"id": "cmp:course-state", "source": "cmp",
                                   "title": "Live CMP course state",
                                   "url": snapshot["course"]["source_url"]}]


def test_historical_question_does_not_get_current_live_snapshot(cfg, snapshot):
    chat = mock_chat()
    answer_question(cfg, mock_index([]), chat, "When did peer review open in 2024?", "course",
                    "machine-learning-zoomcamp", course_state=snapshot)
    assert len(chat.call_args_list) == 1  # Rewrite only; no eligible historical context.


def test_handler_passes_validated_state(monkeypatch, snapshot):
    monkeypatch.setenv("FAQ_ASSISTANT_SHARED_SECRET", "s3cret")
    chat = mock_chat(source_ids=["cmp:course-state"])
    monkeypatch.setattr(handler, "make_openai_chat", lambda *args: chat)
    monkeypatch.setattr(handler, "_INDEX", mock_index([]))
    result = handler.lambda_handler(event(secret="s3cret", body={
        "question": "Have reviews been assigned?", "scope": "course",
        "course": "machine-learning-zoomcamp", "course_state": snapshot,
    }))
    assert result["statusCode"] == 200
    assert json.loads(result["body"])["sources"][0]["source"] == "cmp"


def test_open_endpoint_cannot_accept_trusted_state(monkeypatch, snapshot):
    monkeypatch.delenv("FAQ_ASSISTANT_SHARED_SECRET", raising=False)
    result = handler.lambda_handler(event(body={"question": "x", "scope": "course",
                                                "course": "machine-learning-zoomcamp",
                                                "course_state": snapshot}))
    assert result["statusCode"] == 401


@pytest.mark.parametrize("mutation", [
    lambda s: s.update(student={"mapped": True, "assignments": 3}),
    lambda s: s["projects"][0].update(assignment_count=3),
    lambda s: s["projects"][0].update(source_url="https://evil.example/ml-2026/"),
    lambda s: s["projects"][0].update(source_url="https://courses.datatalks.club/other/"),
    lambda s: s["projects"][0].update(source_url="https://courses.datatalks.club/ml-2026/../other/"),
    lambda s: s["projects"][0].update(source_url="https://courses.datatalks.club/ml-2026/%2e%2e/other/"),
    lambda s: s["projects"][0].update(peer_review_open="true"),
    lambda s: s["projects"][0].update(peer_review_due_date="tomorrow"),
    lambda s: s.update(homeworks=s["homeworks"] * 101),
    lambda s: s["course"].update(title="x" * 70000),
])
def test_private_malformed_and_unbounded_state_rejected(cfg, snapshot, mutation):
    mutation(snapshot)
    with pytest.raises((ValueError, TypeError)):
        validate_course_state(snapshot, cfg, "course", "machine-learning-zoomcamp")


def test_invalid_state_is_400_before_chat(monkeypatch, snapshot):
    monkeypatch.setenv("FAQ_ASSISTANT_SHARED_SECRET", "s3cret")
    snapshot["student"] = {"mapped": True}
    chat = mock_chat()
    monkeypatch.setattr(handler, "make_openai_chat", lambda *args: chat)
    result = handler.lambda_handler(event(secret="s3cret", body={"question": "x", "scope": "course",
        "course": "machine-learning-zoomcamp", "course_state": snapshot}))
    assert result["statusCode"] == 400
    chat.assert_not_called()


def test_current_state_source_survives_omitted_model_citation(cfg, snapshot):
    result = answer_question(cfg, mock_index([]), mock_chat(source_ids=[]),
                             "Have peer review items been assigned?", "course",
                             "machine-learning-zoomcamp", course_state=snapshot)
    assert result["sources"][0]["id"] == "cmp:course-state"


def test_supplied_current_cohort_overrides_stale_index_config(cfg, snapshot):
    cfg["courses"]["machine-learning-zoomcamp"]["current_cohort"] = "2025"
    cfg["courses"]["machine-learning-zoomcamp"]["platform_url"] = "https://courses.datatalks.club/ml-2025/"
    chat = mock_chat(source_ids=["cmp:course-state"])
    result = answer_question(cfg, mock_index([]), chat, "Have peer reviews been assigned for 2026?",
                             "course", "machine-learning-zoomcamp", course_state=snapshot)
    assert result["found_answer"] is True
    messages = chat.call_args_list[-1].args[0]
    assert "current cohort for this course is 2026" in messages[0]["content"]
    assert "CURRENT_PLATFORM: https://courses.datatalks.club/ml-2026/" in messages[1]["content"]
    assert result["sources"][0]["id"] == "cmp:course-state"
    assert cfg["courses"]["machine-learning-zoomcamp"]["current_cohort"] == "2025"


def test_historical_question_excludes_live_state_when_index_current_is_stale(cfg, snapshot):
    cfg["courses"]["machine-learning-zoomcamp"]["current_cohort"] = "2025"
    chat = mock_chat()
    answer_question(cfg, mock_index([]), chat, "When did peer review open in 2025?", "course",
                    "machine-learning-zoomcamp", course_state=snapshot)
    assert len(chat.call_args_list) == 1
