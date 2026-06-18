"""Build and load the prebuilt search index.

The index is fitted once at build time (in CI) and shipped as a packed
``zerosearch`` artifact, so the runtime loads it in milliseconds instead of
re-tokenizing the whole corpus on every cold start.
"""

import json
from pathlib import Path
from typing import Any

from zerosearch import Index

# Canonical retrieval schema (kept in sync with the eval harness).
TEXT_FIELDS = ["title", "section", "text"]
KEYWORD_FIELDS = ["id", "source_type", "scope", "course", "url", "repo", "path"]

DEFAULT_CORPUS_ARTIFACT = "artifacts/search/search-corpus.json"
DEFAULT_INDEX_ARTIFACT = "artifacts/search/search-index.zsx"


def build_search_index(
    records: list[dict[str, Any]] | None = None,
    *,
    corpus_artifact: str | Path = DEFAULT_CORPUS_ARTIFACT,
    index_artifact: str | Path = DEFAULT_INDEX_ARTIFACT,
) -> dict[str, Any]:
    """Fit the index from ``records`` and save it.

    When ``records`` is omitted, read the corpus artifact if present, otherwise
    fall back to the committed embedded corpus (so CI can build the index without
    re-fetching the sources).
    """
    if records is None:
        path = Path(corpus_artifact)
        if path.exists():
            records = json.loads(path.read_text(encoding="utf-8"))
        else:
            records = _load_embedded_corpus()

    index = Index(text_fields=TEXT_FIELDS, keyword_fields=KEYWORD_FIELDS).fit(records)

    path = Path(index_artifact)
    path.parent.mkdir(parents=True, exist_ok=True)
    index.save(path)

    return {
        "records": len(records),
        "index": str(path),
        "bytes": path.stat().st_size,
    }


def load_search_index(index_artifact: str | Path = DEFAULT_INDEX_ARTIFACT) -> Index:
    """Load the prebuilt packed index."""
    return Index.load(index_artifact)


def _load_embedded_corpus() -> list[dict[str, Any]]:
    import base64
    import zlib

    from faq_assistant.search_corpus import SEARCH_CORPUS_B64

    raw = zlib.decompress(base64.b64decode(SEARCH_CORPUS_B64)).decode("utf-8")
    return json.loads(raw)
