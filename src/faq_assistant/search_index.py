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
    """Fit the index from ``records`` (or the corpus artifact) and save it."""
    if records is None:
        records = json.loads(Path(corpus_artifact).read_text(encoding="utf-8"))

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
