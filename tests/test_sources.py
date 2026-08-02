from types import SimpleNamespace

from faq_assistant.sources import (
    cohort_from_locator,
    load_course_status_documents,
    load_shared_course_docs_documents,
)


def test_cohort_from_locator_handles_repo_paths_and_platform_urls():
    assert cohort_from_locator("cohorts/2026/workshops/dlt/README.md") == "2026"
    assert cohort_from_locator(url="https://courses.datatalks.club/llm-zoomcamp-2025/") == "2025"
    assert cohort_from_locator("04-evaluation/code/evaluate.py") == ""


def test_course_status_documents_are_current_and_canonical(cfg):
    docs = load_course_status_documents(cfg)
    llm = next(doc for doc in docs if doc.course == "llm-zoomcamp")
    assert llm.cohort == "2026"
    assert llm.current is True
    assert llm.authority == "canonical"
    assert llm.url == "https://courses.datatalks.club/llm-zoomcamp-2026/"
    assert "project submission" in llm.text


def test_shared_logistics_docs_are_course_agnostic_canonical(monkeypatch, cfg):
    files = [SimpleNamespace(
        filename="courses/zoomcamp-logistics/ai-usage.md",
        content="# AI usage\nAI tools are allowed.",
    )]
    monkeypatch.setattr("faq_assistant.sources.read_github_files", lambda *args, **kwargs: files)
    docs = load_shared_course_docs_documents(cfg)
    assert len(docs) == 1
    assert docs[0].course is None
    assert docs[0].scope == "docs"
    assert docs[0].authority == "canonical"
    assert docs[0].source_id == "courses/zoomcamp-logistics/ai-usage.md"
