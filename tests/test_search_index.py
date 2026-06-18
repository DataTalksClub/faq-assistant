"""Unit tests for the prebuilt-index build/load wrappers."""

from faq_assistant.search_index import build_search_index, load_search_index

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
