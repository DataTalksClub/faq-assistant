#!/usr/bin/env python3
"""End-to-end answer-gap regression, tracked as an Opik Experiment (Comet Cloud).

Same checks as ``run_answer_gaps.py --answers`` -- retrieval sources and the
generated answer against instructor-backed constraints from
``data/answer_gaps.jsonl`` -- but each run uploads the rows as an Opik Dataset
and scores them through ``opik.evaluate()``, so results land as a comparable
Experiment in the "faq-assistant" Opik project instead of only a terminal
report. Requires ``OPENAI_API_KEY`` and ``OPIK_API_KEY`` (see ``.env``).

    uv run --group evals python evals/opik_answer_gaps.py
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import opik  # noqa: E402
from opik.evaluation.metrics import base_metric, score_result  # noqa: E402

import run_answer_gaps as gaps  # noqa: E402 -- shares corpus/draft-splicing/env loading

DATASET_NAME = "faq-assistant-answer-gaps"
PROJECT_NAME = "faq-assistant-lambda"

SOURCE_FIELDS = ("id", "source_id", "source_type", "title", "url", "repo", "path", "cohort", "authority")


def source_haystack(source: dict[str, Any]) -> str:
    return "\n".join(str(source.get(field, "")) for field in SOURCE_FIELDS).lower()


class SourceConstraints(base_metric.BaseMetric):
    """Pass iff retrieval includes a required source and excludes forbidden ones."""

    def __init__(self, name: str = "source_constraints") -> None:
        super().__init__(name=name)

    def score(
        self,
        sources: list[dict[str, Any]] | None = None,
        required_source_any: list[str] | None = None,
        forbidden_source_any: list[str] | None = None,
        **_: Any,
    ) -> score_result.ScoreResult:
        texts = [source_haystack(source) for source in (sources or [])]
        required = list(required_source_any or [])
        forbidden = list(forbidden_source_any or [])
        failures: list[str] = []
        if required and not gaps.contains_any(texts, required):
            failures.append("missing required source: " + " OR ".join(required))
        found_forbidden = [pattern for pattern in forbidden if gaps.contains_any(texts, [pattern])]
        if found_forbidden:
            failures.append("forbidden source: " + ", ".join(found_forbidden))
        return score_result.ScoreResult(
            name=self.name, value=0.0 if failures else 1.0, reason="; ".join(failures) or None
        )


class AnswerConstraints(base_metric.BaseMetric):
    """Pass iff the generated answer contains required text and avoids forbidden text."""

    def __init__(self, name: str = "answer_constraints") -> None:
        super().__init__(name=name)

    def score(
        self,
        answer: str = "",
        answer_must_contain_any: list[str] | None = None,
        answer_must_not_contain: list[str] | None = None,
        **_: Any,
    ) -> score_result.ScoreResult:
        lowered = (answer or "").lower()
        required = list(answer_must_contain_any or [])
        forbidden = list(answer_must_not_contain or [])
        failures: list[str] = []
        if required and not any(pattern.lower() in lowered for pattern in required):
            failures.append("answer missing: " + " OR ".join(required))
        found_forbidden = [pattern for pattern in forbidden if pattern.lower() in lowered]
        if found_forbidden:
            failures.append("answer contains: " + ", ".join(found_forbidden))
        return score_result.ScoreResult(
            name=self.name, value=0.0 if failures else 1.0, reason="; ".join(failures) or None
        )


def build_dataset(rows: list[dict[str, Any]]) -> Any:
    client = opik.Opik(project_name=PROJECT_NAME)
    dataset = client.get_or_create_dataset(DATASET_NAME, project_name=PROJECT_NAME)
    dataset.insert(rows)
    return dataset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gaps", type=Path, default=gaps.DEFAULT_GAPS)
    parser.add_argument("--corpus", type=Path, default=gaps.DEFAULT_CORPUS)
    parser.add_argument("--faq-repo", type=Path, default=gaps.ROOT.parent / "faq")
    parser.add_argument("--no-local-drafts", action="store_true")
    parser.add_argument("--experiment-name", default=None)
    args = parser.parse_args()

    gaps.load_env()
    rows = [row for row in gaps.read_jsonl(args.gaps) if row.get("required_source_any")]
    corpus = gaps.json.loads(args.corpus.read_text(encoding="utf-8"))
    draft_count = 0
    if not args.no_local_drafts:
        corpus, draft_count = gaps.splice_local_faq_drafts(corpus, rows, args.faq_repo.expanduser())
    index = gaps.Index(text_fields=gaps.TEXT_FIELDS, keyword_fields=gaps.KEYWORD_FIELDS).fit(corpus)
    chat = gaps.make_openai_chat(gaps.CONFIG)

    dataset = build_dataset(rows)

    def task(item: dict[str, Any]) -> dict[str, Any]:
        query, course, scope = item["query"], item["course"], item.get("scope", "course")
        retrieval_query = item.get("retrieval_query") or query
        results = gaps.search(gaps.CONFIG, index, retrieval_query, scope, course, original_question=query)
        payload = gaps.answer_question(gaps.CONFIG, index, chat, query, scope, course, source="eval")
        return {
            "answer": payload["answer"],
            "rewritten_query": payload["rewritten_query"],
            "sources": [dataclasses.asdict(result) for result in results],
        }

    result = opik.evaluate(
        dataset=dataset,
        task=task,
        scoring_metrics=[SourceConstraints(), AnswerConstraints()],
        experiment_name=args.experiment_name,
        experiment_name_prefix=None if args.experiment_name else "answer-gaps",
        task_threads=4,
    )
    print(f"local FAQ drafts spliced: {draft_count}")
    if result.experiment_url:
        print(f"Opik experiment: {result.experiment_url}")


if __name__ == "__main__":
    main()
