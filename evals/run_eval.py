"""Retrieval evaluation following the llm-zoomcamp framework: hit rate + MRR.

Ground truth = real user Slack questions judged against the production corpus
(see build_dataset.py). For each query-rewrite variant we rewrite the question
(gpt-4o-mini), retrieve with the configured engine, and score the ranking.

    uv run python evals/run_eval.py                 # zerosearch, all variants
    uv run --with minsearch python evals/run_eval.py --engine minsearch --variant best

Metrics (averaged over queries), llm-zoomcamp style:
    hit_rate@k : fraction of queries with a relevant doc in the top-k
    mrr@k      : mean reciprocal rank of the first relevant doc in the top-k
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor

import lib

WORKERS = 8

REWRITE_MODEL = "gpt-4o-mini"
K_VALUES = (1, 3, 5)
MAIN_K = 5

# Query-rewrite instruction variants to compare. "raw" = no rewrite (baseline).
VARIANTS: dict[str, str | None] = {
    "raw": None,
    "current": (
        "Rewrite the user's Slack message into one concise keyword search query. "
        "Fix typos, remove mentions and filler, preserve technical terms, and do not answer. "
        "Do not include the course name or DataTalks.Club when they are already provided as "
        "scope metadata. Keep only the words useful for keyword search."
    ),
    "minimal": (
        "Turn the student's message into a short keyword search query. Return only the query."
    ),
    "keywords": (
        "Extract the key technical terms and concepts from the student's message as a search "
        "query: nouns, tool names, error names, commands. Drop greetings, filler and pronouns. "
        "Fix obvious typos. Return only the keywords, space-separated."
    ),
    "expansion": (
        "Rewrite the student's Slack message into a keyword search query for an FAQ index. "
        "Fix typos, remove greetings/mentions/filler, keep technical terms, and expand common "
        "abbreviations (e.g. 'hw' -> 'homework', 'de' -> 'data engineering'). Add one or two "
        "synonyms for the main technical term if helpful. Return only the query."
    ),
    "light": (
        "Lightly clean the student's Slack message into a search query: fix typos, remove "
        "greetings, mentions and filler, and expand obvious abbreviations (hw -> homework). "
        "Keep the rest of the wording and ALL technical terms. Do not shorten aggressively. "
        "Return only the query."
    ),
    "verbatim": (
        "Rewrite the user's Slack message into one concise keyword search query. Fix typos, "
        "remove mentions and filler, preserve technical terms, and do not answer. Do not include "
        "the course name when provided as metadata. Preserve exact error messages, tool names, "
        "commands, and file names verbatim. Return only the query."
    ),
}


def rewrite(query: str, course: str, system: str | None) -> str:
    if system is None:
        return query
    try:
        out = lib.chat(REWRITE_MODEL, [
            {"role": "system", "content": system},
            {"role": "user", "content": f"course: {course}\nmessage: {query}"},
        ], max_tokens=60)
        return out.strip() or query
    except Exception:
        return query


def hit_rate(ranked_ids: list[str], relevant: set[str], k: int) -> float:
    return 1.0 if any(rid in relevant for rid in ranked_ids[:k]) else 0.0


def mrr(ranked_ids: list[str], relevant: set[str], k: int) -> float:
    for i, rid in enumerate(ranked_ids[:k], 1):
        if rid in relevant:
            return 1.0 / i
    return 0.0


def evaluate(index, gt: list[dict], system: str | None) -> dict:
    # Rewrites hit the API, so run them concurrently; retrieval/scoring is local.
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        rewritten = list(ex.map(lambda it: rewrite(it["query"], it["course"], system), gt))

    rows = []
    agg = {f"hit@{k}": 0.0 for k in K_VALUES}
    agg.update({f"mrr@{k}": 0.0 for k in K_VALUES})
    for item, q in zip(gt, rewritten):
        results = lib.search(index, q, item["course"], num_results=max(K_VALUES))
        ranked = [r["id"] for r in results]
        relevant = set(item["relevant_ids"])
        for k in K_VALUES:
            agg[f"hit@{k}"] += hit_rate(ranked, relevant, k)
            agg[f"mrr@{k}"] += mrr(ranked, relevant, k)
        rows.append({"query": item["query"], "rewritten": q, "course": item["course"],
                     "ranked": ranked[:MAIN_K], "relevant": item["relevant_ids"]})
    n = len(gt)
    metrics = {key: round(val / n, 4) for key, val in agg.items()}
    metrics["n"] = n
    return {"metrics": metrics, "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="zerosearch", choices=["zerosearch", "minsearch"])
    ap.add_argument("--variant", default="all", help="variant name, or 'all'")
    args = ap.parse_args()

    gt = lib.read_jsonl(lib.DATA_DIR / "ground_truth.jsonl")
    corpus = lib.load_corpus()
    index = lib.build_index(corpus, args.engine)
    names = list(VARIANTS) if args.variant == "all" else [args.variant]

    print(f"engine={args.engine}  ground-truth queries={len(gt)}\n")
    header = f"{'variant':<11}" + "".join(f"hit@{k:<5}" for k in K_VALUES) + "".join(f"mrr@{k:<5}" for k in K_VALUES)
    print(header); print("-" * len(header))
    summary = {}
    for name in names:
        result = evaluate(index, gt, VARIANTS[name])
        m = result["metrics"]
        summary[name] = m
        line = f"{name:<11}" + "".join(f"{m[f'hit@{k}']:<9.3f}" for k in K_VALUES) + "".join(f"{m[f'mrr@{k}']:<9.3f}" for k in K_VALUES)
        print(line)
        lib.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        (lib.RESULTS_DIR / f"{args.engine}__{name}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    best = max(summary, key=lambda nm: (summary[nm][f"hit@{MAIN_K}"], summary[nm][f"mrr@{MAIN_K}"]))
    print(f"\nbest variant by hit@{MAIN_K}: {best}  -> {summary[best]}")
    (lib.RESULTS_DIR / f"summary__{args.engine}.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
