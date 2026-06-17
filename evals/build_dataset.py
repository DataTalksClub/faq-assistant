"""Build a ground-truth retrieval eval from real Slack questions.

Methodology (pooled relevance judgments, the standard IR approach):
  1. Sample answered, question-shaped Slack threads per course.
  2. For each query, build a candidate POOL = union of what several retrievers
     surface (zerosearch + minsearch on the raw query, plus zerosearch on a
     neutral LLM rewrite) so the pool is not biased to a single ranking.
  3. An LLM judge (gpt-5.4-mini) marks which pooled candidates actually answer
     the question. Queries with >=1 relevant candidate become ground truth.

Run with the real minsearch available for pooling:
    uv run --with minsearch python evals/build_dataset.py

Output: evals/data/ground_truth.jsonl
"""

from __future__ import annotations

import random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import lib

random.seed(20260617)
WORKERS = 8

# How many queries to *judge* per course (kept small to bound API cost).
SAMPLE_PER_COURSE = {
    "data-engineering-zoomcamp": 60,
    "ai-dev-tools-zoomcamp": 30,
    "stock-markets-analytics-zoomcamp": 40,
}
POOL_K = 20
JUDGE_MODEL = "gpt-5.4-mini"
POOL_REWRITE_MODEL = "gpt-4o-mini"

JUDGE_SYS = (
    "You judge which FAQ entries DIRECTLY answer a student's question from a course Slack "
    "channel. You are given the question and a numbered list of candidate FAQ entries.\n"
    "Be strict and conservative:\n"
    "- Include an entry ONLY if, on its own, it specifically answers THIS question.\n"
    "- Most questions have 1-2 correct entries. Rarely more than 3.\n"
    "- Exclude entries that are merely about the same topic, tool, or module.\n"
    "- If the question is vague, conversational, or no entry actually answers it, return [].\n"
    "Return strict JSON: {\"relevant\": [numbers]}. Do not invent numbers."
)

POOL_REWRITE_SYS = (
    "Rewrite the student's Slack message into a concise keyword search query. Fix typos, drop "
    "mentions and filler, keep technical terms. Return only the query text."
)


def neutral_rewrite(query: str) -> str:
    try:
        out = lib.chat(POOL_REWRITE_MODEL, [
            {"role": "system", "content": POOL_REWRITE_SYS},
            {"role": "user", "content": query},
        ], max_tokens=60)
        return out.strip() or query
    except Exception:
        return query


def build_pool(zs, ms, query: str, course: str) -> list[dict]:
    rewrite = neutral_rewrite(query)
    pooled: dict[str, dict] = {}
    for idx in (
        lib.search(zs, query, course, POOL_K)
        + lib.search(ms, query, course, POOL_K)
        + lib.search(zs, rewrite, course, POOL_K)
    ):
        pooled.setdefault(idx["id"], idx)
    return list(pooled.values())


def judge(query: str, pool: list[dict]) -> list[str]:
    lines = []
    for i, c in enumerate(pool, 1):
        answer = str(c.get("text", "")).replace("\n", " ")[:300]
        lines.append(f"{i}. [{c['title']}] {answer}")
    user = f"QUESTION:\n{query}\n\nCANDIDATES:\n" + "\n".join(lines)
    try:
        data = lib.chat_json(JUDGE_MODEL, [
            {"role": "system", "content": JUDGE_SYS},
            {"role": "user", "content": user},
        ], max_tokens=200)
        nums = data.get("relevant", []) if isinstance(data, dict) else []
        ids = []
        for n in nums:
            try:
                ids.append(pool[int(n) - 1]["id"])
            except (ValueError, IndexError, TypeError):
                continue
        return ids
    except Exception as e:
        print(f"  judge error: {e}")
        return []


def main() -> None:
    corpus = lib.load_corpus()
    zs = lib.build_index(corpus, "zerosearch")
    ms = lib.build_index(corpus, "minsearch")

    all_questions = lib.load_slack_questions()
    by_course: dict[str, list[dict]] = defaultdict(list)
    for q in all_questions:
        by_course[q["course"]].append(q)

    sample: list[dict] = []
    for course, n in SAMPLE_PER_COURSE.items():
        pool = by_course.get(course, [])
        random.shuffle(pool)
        sample.extend(pool[:n])
    print(f"judging {len(sample)} sampled queries across {len(SAMPLE_PER_COURSE)} courses")
    lib.write_jsonl(lib.DATA_DIR / "slack_sample.jsonl", sample)

    def process(item: dict) -> dict | None:
        pool = build_pool(zs, ms, item["query"], item["course"])
        relevant = judge(item["query"], pool)
        if not relevant:
            return None
        return {
            "query": item["query"],
            "course": item["course"],
            "scope": "course",
            "relevant_ids": relevant,
            "pool_size": len(pool),
            "channel": item["channel"],
            "thread_ts": item["thread_ts"],
        }

    ground_truth = []
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool_exec:
        for result in pool_exec.map(process, sample):
            done += 1
            if result:
                ground_truth.append(result)
            if done % 20 == 0 or done == len(sample):
                print(f"  {done}/{len(sample)} judged, {len(ground_truth)} kept")

    lib.write_jsonl(lib.DATA_DIR / "ground_truth.jsonl", ground_truth)
    kept = len(ground_truth)
    print(f"\nground truth: {kept}/{len(sample)} queries answerable from the FAQ corpus")
    by = defaultdict(int)
    for g in ground_truth:
        by[g["course"]] += 1
    for c, n in sorted(by.items()):
        print(f"  {c}: {n}")
    print(f"written -> {lib.DATA_DIR / 'ground_truth.jsonl'}")


if __name__ == "__main__":
    main()
