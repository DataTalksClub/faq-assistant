"""Fixtures for the integration tests.

These hit the real OpenAI API through the full RAG pipeline (rewrite -> search ->
answer), so they need OPENAI_API_KEY and are NOT part of the regular check suite.

Run with:  uv run --group test --group ingest pytest tests_integration
"""

import os
from pathlib import Path

import pytest

from faq_assistant.answering import SOURCE_LABELS, answer_question, make_openai_chat, search
from faq_assistant.generated_config import CONFIG
from faq_assistant.search_index import build_search_index, load_search_index

ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "artifacts" / "search" / "search-index.zsx"
CORPUS_PATH = ROOT / "artifacts" / "search" / "search-corpus.json"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)


_load_dotenv(ROOT / ".env")


@pytest.fixture(scope="session")
def ask():
    """Return ``ask(question, scope, course=None) -> response dict``."""
    if not os.environ.get(CONFIG["openai"]["api_key_env"]):
        pytest.fail("OPENAI_API_KEY is required for integration tests", pytrace=False)
    if not INDEX_PATH.exists():
        build_search_index(corpus_artifact=CORPUS_PATH, index_artifact=INDEX_PATH)
    index = load_search_index(INDEX_PATH)

    def _ask(question, scope, course=None):
        chat = make_openai_chat(CONFIG)
        return answer_question(CONFIG, index, chat, question, scope, course, source="test")

    return _ask


@pytest.fixture(scope="session")
def retrieve():
    """Return retrieve(question, scope, course=None) -> set of retrieved source labels.

    Retrieval only (no OpenAI call), so it's deterministic.
    """
    if not INDEX_PATH.exists():
        build_search_index(corpus_artifact=CORPUS_PATH, index_artifact=INDEX_PATH)
    index = load_search_index(INDEX_PATH)

    def _retrieve(question, scope, course=None):
        results = search(CONFIG, index, question, scope, course)
        return {SOURCE_LABELS.get(r.source_type, r.source_type) for r in results}

    return _retrieve


@pytest.fixture
def source_labels():
    """Return ``labels(response) -> set`` of the cited source labels.

    Exposed as a fixture (not a bare module function) so the test module never
    has to ``from conftest import ...``; that bare import collides with the unit
    suite's ``tests/conftest.py`` when a single ``pytest`` run collects both.
    """

    def _labels(response):
        return {s["source"] for s in response["sources"]}

    return _labels
