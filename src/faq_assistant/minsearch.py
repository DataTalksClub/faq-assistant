from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any


TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_+.#-]*", re.IGNORECASE)
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


class Index:
    def __init__(self, text_fields: list[str], keyword_fields: list[str] | None = None):
        self.text_fields = text_fields
        self.keyword_fields = keyword_fields or []
        self.docs: list[dict[str, Any]] = []

    def fit(self, docs: list[dict[str, Any]]) -> "Index":
        self.docs = docs
        return self

    def search(
        self,
        query: str,
        filter_dict: dict[str, str] | None = None,
        boost_dict: dict[str, float] | None = None,
        num_results: int = 10,
    ) -> list[dict[str, Any]]:
        query_terms = tokenize(query)
        if not query_terms:
            return []

        filter_dict = filter_dict or {}
        boost_dict = boost_dict or {}
        candidates = [doc for doc in self.docs if matches_filter(doc, filter_dict)]
        if not candidates:
            return []

        document_frequencies = compute_document_frequencies(candidates, self.text_fields, query_terms)
        scored = []
        for doc in candidates:
            score = score_document(
                doc=doc,
                text_fields=self.text_fields,
                query_terms=query_terms,
                document_frequencies=document_frequencies,
                document_count=len(candidates),
                boost_dict=boost_dict,
            )
            if score > 0:
                record = dict(doc)
                record["score"] = score
                scored.append(record)

        scored.sort(key=lambda record: float(record["score"]), reverse=True)
        return scored[:num_results]


def score_document(
    doc: dict[str, Any],
    text_fields: list[str],
    query_terms: list[str],
    document_frequencies: dict[str, int],
    document_count: int,
    boost_dict: dict[str, float],
) -> float:
    score = 0.0
    for field in text_fields:
        tokens = tokenize(str(doc.get(field, "")))
        if not tokens:
            continue
        counts = Counter(tokens)
        field_length = len(tokens)
        boost = float(boost_dict.get(field, 1.0))
        for term in query_terms:
            term_frequency = counts.get(term, 0)
            if term_frequency == 0:
                continue
            document_frequency = document_frequencies.get(term, 0)
            idf = math.log(1 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5))
            score += boost * idf * (term_frequency / math.sqrt(field_length))
    return score


def compute_document_frequencies(
    docs: list[dict[str, Any]],
    text_fields: list[str],
    query_terms: list[str],
) -> dict[str, int]:
    query_term_set = set(query_terms)
    document_frequencies = dict.fromkeys(query_term_set, 0)
    for doc in docs:
        doc_terms: set[str] = set()
        for field in text_fields:
            doc_terms.update(tokenize(str(doc.get(field, ""))))
        for term in query_term_set & doc_terms:
            document_frequencies[term] += 1
    return document_frequencies


def matches_filter(doc: dict[str, Any], filter_dict: dict[str, str]) -> bool:
    return all(str(doc.get(field, "")) == str(value) for field, value in filter_dict.items())


def tokenize(text: str) -> list[str]:
    tokens = [match.group(0).lower() for match in TOKEN_RE.finditer(text)]
    return [token for token in tokens if len(token) > 1 and token not in STOP_WORDS]
