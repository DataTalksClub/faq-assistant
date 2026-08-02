"""Unit tests for the RAG orchestration (mocked chat + index)."""

from conftest import mock_chat, mock_index, record

from faq_assistant.answering import (
    SOURCE_LABELS,
    answer_question,
    build_context,
    detect_query_intent,
    fallback_sources,
    generate_answer,
    resolve_sources,
    retrieve,
    search,
)
from faq_assistant.models import RagAnswer, SearchResult


# --- retrieval filter ------------------------------------------------------

def test_search_course_channel_filters_course_and_general(cfg):
    index = mock_index()
    search(cfg, index, "q", "course", "llm-zoomcamp")
    assert index.search.call_args.kwargs["filter_dict"] == {"course": ["llm-zoomcamp", ""]}


def test_search_non_course_filters_general_docs_only(cfg):
    index = mock_index()
    search(cfg, index, "q", "docs", None)
    assert index.search.call_args.kwargs["filter_dict"] == {"course": ""}


def test_search_drops_results_below_min_score(cfg):
    cfg["retrieval"]["min_score"] = 0.5
    index = mock_index([record(id="keep", score=0.9), record(id="drop", score=0.1)])
    results = search(cfg, index, "q", "docs", None)
    assert [r.id for r in results] == ["keep"]


def test_unqualified_temporal_query_excludes_historical_cohorts(cfg):
    index = mock_index([
        record(id="current", source_id="status", cohort="2026", current=True,
               authority="canonical", score=0.5),
        record(id="old", source_id="old", cohort="2025", authority="historical", score=2.0),
        record(id="evergreen", source_id="faq", score=0.8),
    ])
    outcome = retrieve(cfg, index, "project submission link", "course", "llm-zoomcamp",
                       original_question="Where do I submit my project?")
    assert [result.id for result in outcome.results] == ["evergreen", "current"]
    assert outcome.filtered_historical == 1


def test_explicit_cohort_query_keeps_requested_historical_cohort(cfg):
    index = mock_index([
        record(id="current", cohort="2026", current=True, authority="canonical"),
        record(id="requested", cohort="2025", authority="historical"),
    ])
    results = search(cfg, index, "2025 project", "course", "llm-zoomcamp",
                     original_question="Where was the 2025 project submitted?")
    assert [result.id for result in results] == ["requested"]


def test_non_temporal_technical_query_can_retrieve_historical_material(cfg):
    index = mock_index([record(id="historical", cohort="2025", authority="historical")])
    results = search(cfg, index, "docker compose error", "course", "llm-zoomcamp",
                     original_question="How do I fix this Docker Compose error?")
    assert [result.id for result in results] == ["historical"]


def test_project_policy_question_is_not_implicitly_temporal(cfg):
    intent = detect_query_intent(
        cfg, "Is it allowed to use LLMs for code generation in the project?", "llm-zoomcamp"
    )
    assert intent.temporal is False


def test_retrieval_deduplicates_chunks_by_source_id(cfg):
    cfg["retrieval"]["max_chunks_per_source"] = 1
    index = mock_index([
        record(id="a:1", source_id="a", score=2.0),
        record(id="a:2", source_id="a", score=1.9),
        record(id="b:1", source_id="b", score=1.0),
    ])
    outcome = retrieve(cfg, index, "technical topic", "course", "llm-zoomcamp")
    assert [result.id for result in outcome.results] == ["a:1", "b:1"]
    assert outcome.deduplicated == 1


def test_detect_query_intent_uses_configured_current_cohort(cfg):
    intent = detect_query_intent(cfg, "What is the current project deadline?", "llm-zoomcamp")
    assert intent.temporal is True
    assert intent.current_cohort == "2026"
    assert intent.requested_cohort == ""


# --- source resolution -----------------------------------------------------

def _results():
    return [
        SearchResult(id="faq:1", source_type="faq", title="F", url="u1"),
        SearchResult(id="gh:1", source_type="github", title="G", url="u2"),
        SearchResult(id="cd:1", source_type="course_docs", title="C", url="u3"),
    ]


def test_resolve_sources_maps_labels_and_dedupes(cfg):
    rag = RagAnswer(answer="a", found_answer=True, source_ids=["gh:1", "faq:1", "gh:1", "missing"])
    out = resolve_sources(cfg, rag, _results())
    assert out == [
        {"id": "gh:1", "source": "course-repo", "title": "G", "url": "u2"},
        {"id": "faq:1", "source": "faq", "title": "F", "url": "u1"},
    ]


def test_resolve_sources_collapses_chunks_sharing_a_url(cfg):
    # Two different chunk ids of the same page share one URL -> one source line.
    results = [
        SearchResult(id="cd:1", source_type="course_docs", course="llm-zoomcamp", title="Project", url="u"),
        SearchResult(id="cd:2", source_type="course_docs", course="llm-zoomcamp", title="Project", url="u"),
    ]
    rag = RagAnswer(answer="a", found_answer=True, source_ids=["cd:1", "cd:2"])
    out = resolve_sources(cfg, rag, results)
    assert len(out) == 1
    assert out[0]["url"] == "u"


def test_resolve_sources_course_docs_get_breadcrumb_title(cfg):
    results = [
        SearchResult(id="cd:1", source_type="course_docs", course="llm-zoomcamp", title="Project", url="u3"),
    ]
    rag = RagAnswer(answer="a", found_answer=True, source_ids=["cd:1"])
    out = resolve_sources(cfg, rag, results)
    assert out[0]["title"] == "Courses › LLM Zoomcamp › Project"
    assert ">" not in out[0]["title"]


def test_resolve_sources_llm_repo_gets_module_lesson_title(cfg):
    results = [
        SearchResult(
            id="gh:1",
            source_type="github",
            course="llm-zoomcamp",
            title="Multi-Agent Systems",
            url="u",
            path="03-orchestration/lessons/07-multi-agent.md",
        ),
    ]
    rag = RagAnswer(answer="a", found_answer=True, source_ids=["gh:1"])
    out = resolve_sources(cfg, rag, results)
    assert out[0]["title"] == "03. Orchestration › 07. Multi-Agent Systems"
    assert ">" not in out[0]["title"]


def test_resolve_sources_other_repos_keep_title_without_adapter(cfg):
    results = [
        SearchResult(
            id="gh:1",
            source_type="github",
            course="data-engineering-zoomcamp",
            title="Docker Compose",
            url="u",
            path="01-docker-terraform/docker-sql/09-docker-compose.md",
        ),
    ]
    rag = RagAnswer(answer="a", found_answer=True, source_ids=["gh:1"])
    out = resolve_sources(cfg, rag, results)
    assert out[0]["title"] == "Docker Compose"


def test_resolve_sources_empty_when_not_found(cfg):
    rag = RagAnswer(answer="no", found_answer=False, source_ids=["faq:1"])
    assert resolve_sources(cfg, rag, _results()) == []


def test_source_labels_collapse_to_three():
    assert {SOURCE_LABELS[k] for k in SOURCE_LABELS} == {"faq", "course-repo", "docs"}
    assert SOURCE_LABELS["course_docs"] == "docs"
    assert SOURCE_LABELS["github"] == "course-repo"


# --- answer generation -----------------------------------------------------

def test_generate_answer_without_context_returns_not_found(cfg):
    answer, found, sources = generate_answer(cfg, mock_chat(), "q", "q", "docs", None, [])
    assert found is False
    assert sources == []
    assert "couldn't find" in answer.lower()


def test_generate_answer_includes_current_cohort_guardrails(cfg):
    chat = mock_chat(answer="Use the platform.", source_ids=["status"])
    result = SearchResult(id="status", source_type="course_status", title="Current", text="Platform")
    generate_answer(cfg, chat, "Where do I submit?", "project submission", "course",
                    "llm-zoomcamp", [result])
    messages = chat.call_args.args[0]
    assert "configured current cohort for this course is 2026" in messages[0]["content"]
    assert "CURRENT_PLATFORM: https://courses.datatalks.club/llm-zoomcamp-2026/" in messages[1]["content"]


def test_build_context_exposes_freshness_metadata():
    context = build_context([SearchResult(
        id="x", source_id="page", cohort="2026", current=True,
        authority="canonical", path="cohorts/2026/project.md",
    )])
    assert "source_id: page" in context
    assert "cohort: 2026" in context
    assert "current: true" in context
    assert "authority: canonical" in context


def test_fallback_sources_course_lists_faq_docs_repo(cfg):
    out = fallback_sources(cfg, "course", "llm-zoomcamp")
    labels = [s["source"] for s in out]
    assert "faq" in labels
    assert "docs" in labels
    faq = next(s for s in out if s["source"] == "faq")
    assert faq["url"] == "https://datatalks.club/faq/llm-zoomcamp.html"
    assert any(s["url"] == "https://courses.datatalks.club/llm-zoomcamp-2026/" for s in out)


def test_fallback_sources_non_course_is_docs_home(cfg):
    assert fallback_sources(cfg, "docs", None) == [
        {"source": "docs", "title": "DataTalks.Club docs", "url": "https://datatalks.club/docs/"}
    ]


def test_answer_question_not_found_points_to_instructors(cfg):
    chat = mock_chat(found_answer=False)
    result = answer_question(cfg, mock_index([record(id="x")]), chat, "huh?", "course", "llm-zoomcamp")
    assert result["found_answer"] is False
    assert "ask the instructors" in result["answer"]
    assert any(s["source"] == "faq" for s in result["sources"])


def test_answer_question_temporal_fallback_points_to_current_platform(cfg):
    chat = mock_chat(found_answer=False)
    result = answer_question(
        cfg, mock_index([record(id="x")]), chat,
        "When is the project deadline?", "course", "llm-zoomcamp",
    )
    assert "courses.datatalks.club/llm-zoomcamp-2026" in result["answer"]
    assert "ask the instructors" in result["answer"]


def test_answer_question_not_found_non_course_points_to_community_managers(cfg):
    chat = mock_chat(found_answer=False)
    result = answer_question(cfg, mock_index([record(id="x")]), chat, "huh?", "docs", None)
    assert "community managers" in result["answer"]
    assert result["sources"] == [
        {"source": "docs", "title": "DataTalks.Club docs", "url": "https://datatalks.club/docs/"}
    ]


def test_answer_question_returns_structured_payload(cfg):
    index = mock_index([record(id="faq:1", source_type="faq", title="T", url="U", text="some text")])
    chat = mock_chat(rewrite="docker", answer="Do this.", found_answer=True, source_ids=["faq:1"])

    result = answer_question(cfg, index, chat, "How?", "course", "llm-zoomcamp")

    assert result["rewritten_query"] == "docker"
    assert result["answer"] == "Do this."
    assert result["found_answer"] is True
    assert result["sources"] == [{"id": "faq:1", "source": "faq", "title": "T", "url": "U"}]
    assert "results" not in result            # dropped from the response
    assert chat.call_count == 2               # rewrite + answer
