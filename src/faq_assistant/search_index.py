"""Build and load the prebuilt search index.

The index is fitted once at build time (in CI) and shipped as a packed
``zerosearch`` artifact, so the runtime loads it in milliseconds instead of
re-tokenizing the whole corpus on every cold start.
"""

import json
from pathlib import Path
from typing import Any

from zerosearch import DEFAULT_STOP_WORDS, TOKEN_RE, Index


# Canonical retrieval schema (kept in sync with the eval harness).
TEXT_FIELDS = ["title", "section", "text", "path"]
KEYWORD_FIELDS = [
    "id", "source_type", "scope", "course", "url", "repo", "path",
    "source_id", "cohort", "current", "authority",
]

DEFAULT_CORPUS_ARTIFACT = "artifacts/search/search-corpus.json"
DEFAULT_INDEX_ARTIFACT = "artifacts/search/search-index.zsx"


def fold_plural(token: str) -> str:
    """Fold regular English plural inflections to a shared base form.

    Keyword search matches exact tokens, so a singular query word
    (``prerequisite``) misses a plural FAQ entry (``prerequisites``) and vice
    versa. This folds only the common regular inflections of longer tokens;
    the same function runs at index and query time, so identical words always
    match exactly as before — folding can only *add* singular/plural matches.
    """
    if len(token) <= 4:
        return token
    if token.endswith("ies") and len(token) > 5 and token[-4] not in "aeiou":
        return token[:-3] + "y"
    if token.endswith(("sses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if len(token) > 5 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def search_tokenizer(text: str) -> list[str]:
    """Shared index/query tokenizer: zerosearch token rules plus plural folding.

    Mirrors ``zerosearch.tokenize`` (lowercase, drop 1-char tokens and stop
    words) and folds each surviving token with :func:`fold_plural`, so
    singular/plural word forms share one index term.
    """
    out: list[str] = []
    for match in TOKEN_RE.finditer(str(text)):
        token = match.group(0).lower()
        if len(token) > 1 and token not in DEFAULT_STOP_WORDS:
            out.append(fold_plural(token))
    return out


def build_search_index(
    records: list[dict[str, Any]] | None = None,
    *,
    corpus_artifact: str | Path = DEFAULT_CORPUS_ARTIFACT,
    index_artifact: str | Path = DEFAULT_INDEX_ARTIFACT,
) -> dict[str, Any]:
    """Fit the index from ``records`` and save it.

    When ``records`` is omitted, read the corpus artifact, which
    ``scripts/build_search_corpus.py`` (``make corpus``) produces from the live
    sources.
    """
    if records is None:
        path = Path(corpus_artifact)
        if not path.exists():
            raise FileNotFoundError(
                f"corpus artifact not found: {path}. Run `make corpus` "
                "(uv run --group ingest python scripts/build_search_corpus.py) first."
            )
        records = json.loads(path.read_text(encoding="utf-8"))

    index = Index(
        text_fields=TEXT_FIELDS, keyword_fields=KEYWORD_FIELDS,
        tokenizer=search_tokenizer,
    ).fit(records)

    path = Path(index_artifact)
    path.parent.mkdir(parents=True, exist_ok=True)
    index.save(path)

    return {
        "records": len(records),
        "index": str(path),
        "bytes": path.stat().st_size,
    }


def load_search_index(index_artifact: str | Path = DEFAULT_INDEX_ARTIFACT) -> Index:
    """Load the prebuilt packed index.

    The artifact stores postings, not the tokenizer, so the query side must
    tokenize exactly as the build did — hence the shared ``search_tokenizer``.
    """
    return Index.load(index_artifact, tokenizer=search_tokenizer)
