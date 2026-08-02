#!/usr/bin/env python3
"""Run machine-checkable regressions from recent Slack bot answer gaps.

The default mode is deterministic and checks production retrieval. With
``--answers`` it also calls the configured models and checks answer text.
Locally, unpublished FAQ drafts from ``../faq`` are spliced into the corpus so
source corrections can be tested before the FAQ site is published.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from faq_assistant.answering import answer_question, make_openai_chat, search  # noqa: E402
from faq_assistant.chunking import chunk_documents  # noqa: E402
from faq_assistant.generated_config import CONFIG  # noqa: E402
from faq_assistant.models import SourceDocument  # noqa: E402
from faq_assistant.search_index import KEYWORD_FIELDS, TEXT_FIELDS  # noqa: E402
from zerosearch import Index  # noqa: E402

DEFAULT_GAPS = ROOT / "evals" / "data" / "answer_gaps.jsonl"
DEFAULT_CORPUS = ROOT / "artifacts" / "search" / "search-corpus.json"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def parse_faq(path: Path) -> tuple[str, str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not match:
        raise ValueError(f"invalid FAQ frontmatter: {path}")
    frontmatter, body = match.groups()
    question_match = re.search(r"^question:\s*(.+(?:\n\s+.+)*)", frontmatter, re.MULTILINE)
    id_match = re.search(r"^id:\s*(\S+)", frontmatter, re.MULTILINE)
    if not question_match or not id_match:
        raise ValueError(f"FAQ needs id and question: {path}")
    question = re.sub(r"\s+", " ", question_match.group(1)).strip().strip("'\"")
    return id_match.group(1).strip(), question, body.strip()


def splice_local_faq_drafts(corpus: list[dict], rows: list[dict], faq_repo: Path) -> tuple[list[dict], int]:
    wanted = {str(row["filled_by"]): str(row["course"]) for row in rows if row.get("filled_by")}
    if not wanted or not faq_repo.is_dir():
        return corpus, 0

    drafts: dict[str, dict] = {}
    for doc_id, course in wanted.items():
        paths = list(faq_repo.glob(f"_questions/{course}/*/*_{doc_id}_*.md"))
        if not paths:
            continue
        path = paths[0]
        parsed_id, question, answer = parse_faq(path)
        document = SourceDocument(
            source_type="faq", scope="course", course=course,
            course_name=str(CONFIG["courses"][course]["name"]),
            section=path.parent.name, title=question,
            text=f"section: {path.parent.name}\nquestion: {question}\nanswer: {answer}",
            url=f"https://datatalks.club/faq/{course}.html#{parsed_id}",
            repo=None, path=None, source_id=parsed_id,
        )
        chunk = chunk_documents([document], CONFIG)[0]
        drafts[parsed_id] = dict(chunk.metadata)

    if not drafts:
        return corpus, 0
    corpus = [record for record in corpus if str(record.get("source_id")) not in drafts]
    corpus.extend(drafts.values())
    return corpus, len(drafts)


def source_haystack(result) -> str:
    return "\n".join((
        result.id, result.source_id, result.source_type, result.title,
        result.url, result.repo, result.path, result.cohort, result.authority,
    )).lower()


def contains_any(texts: list[str], patterns: list[str]) -> bool:
    return any(pattern.lower() in text for pattern in patterns for text in texts)


def check_sources(row: dict, results) -> list[str]:
    failures: list[str] = []
    texts = [source_haystack(result) for result in results]
    required = list(row.get("required_source_any") or [])
    forbidden = list(row.get("forbidden_source_any") or [])
    if required and not contains_any(texts, required):
        failures.append("missing required source: " + " OR ".join(required))
    found_forbidden = [pattern for pattern in forbidden if contains_any(texts, [pattern])]
    if found_forbidden:
        failures.append("forbidden source: " + ", ".join(found_forbidden))
    return failures


def check_answer(row: dict, answer: str) -> list[str]:
    failures: list[str] = []
    lowered = answer.lower()
    required = list(row.get("answer_must_contain_any") or [])
    forbidden = list(row.get("answer_must_not_contain") or [])
    if required and not any(pattern.lower() in lowered for pattern in required):
        failures.append("answer missing: " + " OR ".join(required))
    found_forbidden = [pattern for pattern in forbidden if pattern.lower() in lowered]
    if found_forbidden:
        failures.append("answer contains: " + ", ".join(found_forbidden))
    return failures


def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gaps", type=Path, default=DEFAULT_GAPS)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--faq-repo", type=Path, default=ROOT.parent / "faq")
    parser.add_argument("--no-local-drafts", action="store_true")
    parser.add_argument("--answers", action="store_true", help="also call OpenAI and check answer text")
    parser.add_argument("--report-only", action="store_true", help="return success even when checks fail")
    args = parser.parse_args()

    rows = [row for row in read_jsonl(args.gaps) if row.get("required_source_any")]
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    draft_count = 0
    if not args.no_local_drafts:
        corpus, draft_count = splice_local_faq_drafts(corpus, rows, args.faq_repo.expanduser())
    index = Index(text_fields=TEXT_FIELDS, keyword_fields=KEYWORD_FIELDS).fit(corpus)

    chat = None
    if args.answers:
        load_env()
        chat = make_openai_chat(CONFIG)

    failures = 0
    answer_cache: dict[tuple[str, str, str], str] = {}
    for row in rows:
        query, course, scope = row["query"], row["course"], row.get("scope", "course")
        retrieval_query = row.get("retrieval_query") or query
        results = search(CONFIG, index, retrieval_query, scope, course, original_question=query)
        problems = check_sources(row, results)
        if chat is not None:
            cache_key = (query, course, scope)
            if cache_key not in answer_cache:
                payload = answer_question(CONFIG, index, chat, query, scope, course, source="eval")
                answer_cache[cache_key] = payload["answer"]
            problems.extend(check_answer(row, answer_cache[cache_key]))
        status = "PASS" if not problems else "FAIL"
        failures += bool(problems)
        print(f"{status} [{row.get('observed_bot', '?')}] {query}")
        for problem in problems:
            print(f"  - {problem}")
        if problems and chat is not None:
            print(f"  answer: {answer_cache[cache_key]}")

    mode = "retrieval + answer" if args.answers else "retrieval"
    print(f"\n{len(rows) - failures}/{len(rows)} passed ({mode}); local FAQ drafts: {draft_count}")
    if failures and not args.report_only:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
