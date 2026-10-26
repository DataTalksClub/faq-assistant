"""Unit tests for the prebuilt-index build/load wrappers."""

from faq_assistant.search_index import (
    build_search_index,
    fold_plural,
    load_search_index,
    search_tokenizer,
)

RECORDS = [
    {"id": "faq:1", "source_type": "faq", "scope": "course", "course": "llm-zoomcamp",
     "section": "G", "title": "Joining", "text": "join the course after it started",
     "url": "u1", "repo": "", "path": ""},
    {"id": "docs:1", "source_type": "docs", "scope": "docs", "course": "",
     "section": "G", "title": "Slack", "text": "join the datatalks slack community",
     "url": "u2", "repo": "", "path": ""},
]


def test_build_and_load_round_trip(tmp_path):
    artifact = tmp_path / "index.zsx"
    result = build_search_index(records=RECORDS, index_artifact=artifact)

    assert result["records"] == 2
    assert artifact.exists() and artifact.stat().st_size > 0

    index = load_search_index(artifact)
    hits = index.search("join", filter_dict={"course": ["llm-zoomcamp", ""]}, num_results=5)
    assert {h["id"] for h in hits} == {"faq:1", "docs:1"}


def test_build_reads_corpus_artifact_when_records_none(tmp_path):
    import json

    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps(RECORDS), encoding="utf-8")
    artifact = tmp_path / "index.zsx"

    build_search_index(corpus_artifact=corpus, index_artifact=artifact)
    index = load_search_index(artifact)
    assert index.search("slack", filter_dict={"course": ""})[0]["id"] == "docs:1"


def test_fold_plural_collapses_regular_inflections():
    assert fold_plural("prerequisites") == "prerequisite"
    assert fold_plural("prerequisite") == "prerequisite"
    assert fold_plural("attempts") == "attempt"
    assert fold_plural("cohorts") == "cohort"
    assert fold_plural("courses") == "course"
    assert fold_plural("classes") == "class"
    assert fold_plural("reviews") == "review"


def test_fold_plural_leaves_short_and_irregular_tokens():
    assert fold_plural("mlops") == "mlops"
    assert fold_plural("docs") == "docs"
    assert fold_plural("status") == "status"
    assert fold_plural("is") == "is"


def test_singular_query_matches_plural_document(tmp_path):
    records = [
        {"id": "faq:1", "source_type": "faq", "scope": "course", "course": "machine-learning-zoomcamp",
         "section": "G", "title": "What are the prerequisites for the course?",
         "text": "You need to program and feel comfortable in the terminal.",
         "url": "u1", "repo": "", "path": ""},
    ]
    artifact = tmp_path / "index.zsx"
    build_search_index(records=records, index_artifact=artifact)
    index = load_search_index(artifact)
    hits = index.search("prerequisite", filter_dict={"course": ["machine-learning-zoomcamp", ""]})
    assert [h["id"] for h in hits] == ["faq:1"]
    assert search_tokenizer("prerequisites") == search_tokenizer("prerequisite")
