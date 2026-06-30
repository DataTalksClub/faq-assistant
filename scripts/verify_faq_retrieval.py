#!/usr/bin/env python3
"""Verify that proposed FAQ entries are retrievable before they're published.

The production corpus is fetched from the *published* FAQ site, so newly drafted
entries in the `faq` source repo aren't searchable until they ship. This script
closes that loop: it reads the drafted markdown straight from the faq repo, builds
the exact corpus record the ingestion pipeline would, splices it into the current
corpus, and reports production retrieval BEFORE vs AFTER for the real questions
the bot failed on (``evals/data/answer_gaps.jsonl``, matched by ``filled_by``).

    uv run python scripts/verify_faq_retrieval.py
    FAQ_REPO=~/git/faq uv run python scripts/verify_faq_retrieval.py --gaps evals/data/answer_gaps.jsonl

Needs OPENAI_API_KEY (rewrite + judge) and the prod corpus artifact
(`make corpus`); both come from the repo `.env` / `artifacts/`.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))

# Load .env so OPENAI_API_KEY is available to the eval helpers.
for _line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in _line and not _line.startswith("#"):
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))

import lib  # noqa: E402  (evals helpers: corpus, index, prod_search, judge, rewrite)
from build_dataset import judge, POOL_K  # noqa: E402

FAQ_REPO = Path(os.environ.get("FAQ_REPO", ROOT.parent / "faq")).expanduser()


def parse_md(path: Path) -> tuple[str, str]:
    """Return (question, answer_body) from a faq markdown file with frontmatter."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        raise ValueError(f"no frontmatter in {path}")
    fm, body = m.group(1), m.group(2).strip()
    qm = re.search(r"^question:\s*(.+(?:\n\s+.+)*)", fm, re.MULTILINE)
    question = re.sub(r"\s+", " ", qm.group(1)).strip().strip("'\"") if qm else ""
    return question, body


def section_names(course: str) -> dict[str, str]:
    """section_id -> display name, parsed from _questions/<course>/_metadata.yaml."""
    meta = FAQ_REPO / "_questions" / course / "_metadata.yaml"
    names, cur = {}, None
    for line in meta.read_text(encoding="utf-8").splitlines():
        mid = re.match(r"\s*-\s*id:\s*(.+)", line)
        mname = re.match(r"\s*name:\s*(.+)", line)
        if mid:
            cur = mid.group(1).strip().strip("'\"")
        elif mname and cur:
            names[cur] = mname.group(1).strip().strip("'\"")
            cur = None
    return names


def find_entry(doc_id: str) -> Path | None:
    hits = list(FAQ_REPO.glob(f"_questions/*/*/*_{doc_id}_*.md"))
    return hits[0] if hits else None


def faq_record(course: str, section: str, doc_id: str, question: str, answer: str) -> dict:
    """Build the corpus record exactly as faq_assistant.chunking would (faq branch)."""
    digest = hashlib.sha1(f"faq:{course}:{doc_id}".encode()).hexdigest()[:20]
    return {
        "id": f"faq:{digest}:0000", "source_type": "faq", "scope": "course",
        "course": course, "section": section, "title": question,
        "text": f"section: {section}\nquestion: {question}\nanswer: {answer}",
        "url": f"https://datatalks.club/faq/{course}.html#{doc_id}", "repo": "", "path": "",
    }


def rank_of(results: list[dict], target_id: str) -> int | None:
    for i, r in enumerate(results, 1):
        if r["id"] == target_id:
            return i
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gaps", default=str(ROOT / "evals" / "data" / "answer_gaps.jsonl"))
    args = ap.parse_args()

    rows = [r for r in lib.read_jsonl(Path(args.gaps)) if r.get("filled_by")]
    if not rows:
        sys.exit("no rows with a 'filled_by' id in the gaps file")

    corpus = lib.load_corpus()
    existing = {r["id"] for r in corpus}

    # Build records for the drafted entries that aren't in the published corpus yet.
    new_records: dict[str, dict] = {}
    target_by_doc: dict[str, str] = {}
    for doc_id in {r["filled_by"] for r in rows}:
        course = next(r["course"] for r in rows if r["filled_by"] == doc_id)
        path = find_entry(doc_id)
        if not path:
            print(f"! {doc_id}: markdown not found under {FAQ_REPO} (skipping)", file=sys.stderr)
            continue
        section = section_names(course).get(path.parent.name, path.parent.name)
        question, answer = parse_md(path)
        rec = faq_record(course, section, doc_id, question, answer)
        target_by_doc[doc_id] = rec["id"]
        if rec["id"] not in existing:
            new_records[rec["id"]] = rec

    print(f"drafted entries spliced in: {len(new_records)}  "
          f"(already published: {len(target_by_doc) - len(new_records)})\n")

    idx_before = lib.build_index(corpus, "zerosearch")
    idx_after = lib.build_index(corpus + list(new_records.values()), "zerosearch")
    prod_prompt = lib.answering.REWRITE_SYSTEM_PROMPT

    n_ok = 0
    for r in rows:
        course, q = r["course"], r["query"]
        target_id = target_by_doc.get(r["filled_by"])
        rewritten = lib.chat("gpt-4o-mini", [
            {"role": "system", "content": prod_prompt},
            {"role": "user", "content": f"course: {course}\nmessage: {q}"}], max_tokens=60).strip()
        before = rank_of(lib.prod_search(idx_before, rewritten, course), target_id)
        after = rank_of(lib.prod_search(idx_after, rewritten, course), target_id)
        pool = {}
        for c in (lib.search(idx_after, q, course, POOL_K) + lib.search(idx_after, rewritten, course, POOL_K)):
            pool.setdefault(c["id"], c)
        relevant = target_id in judge(q, list(pool.values()))
        hit = after is not None and after <= 5
        n_ok += hit
        flag = "OK " if hit else "-- "
        print(f"{flag}[{r.get('failure','?')}] {q[:58]}")
        print(f"     prod rank  before={before}  after={after}   judged_relevant={relevant}")

    print(f"\n{n_ok}/{len(rows)} questions now retrieve their FAQ entry in the production top-5.")
    print("(Reminder: this splices drafts locally; it lands for real after the faq repo is "
          "committed/published and `make corpus` + reindex runs.)")


if __name__ == "__main__":
    main()
