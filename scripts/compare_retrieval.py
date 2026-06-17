"""Compare the dependency-free reimplementation against the real `minsearch`.

Run with the real library available only ephemerally (no project dependency):

    uv run --with minsearch scripts/compare_retrieval.py

It builds both indexes from the same committed corpus, then reports:
  * ranking agreement between the two engines (overlap@k, top-1 match, MRR gap)
  * absolute self-retrieval quality of each engine (recall@1 / recall@k)
on a query set derived from the FAQ questions plus hand-written natural queries.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zerosearch import Index as OursIndex  # noqa: E402

try:
    from minsearch import Index as RealIndex
except ImportError:
    print("real `minsearch` not importable -- run via: uv run --with minsearch scripts/compare_retrieval.py")
    raise

CORPUS = ROOT / "artifacts" / "search" / "search-corpus.json"
TEXT_FIELDS = ["title", "section", "text"]
KEYWORD_FIELDS = ["id", "source_type", "scope", "course", "url", "repo", "path"]
BOOSTS = {"title": 3.0, "section": 1.5, "text": 1.0}
K = 6  # default_limit


def filter_for(rec: dict) -> dict:
    f = {"scope": rec["scope"]}
    if rec["scope"] == "course" and rec.get("course"):
        f["course"] = rec["course"]
    return f


def question_query(rec: dict) -> str:
    """Use the FAQ question as the query, stripping the leading 'Course:' marker."""
    title = re.sub(r"^\s*Course:\s*", "", rec["title"]).strip()
    return title


def build_queries(records: list[dict], sample_every: int = 6) -> list[dict]:
    """Self-retrieval queries: each FAQ question -> its own chunk is ground truth.

    Deterministically samples every Nth eligible FAQ to keep the run fast (the
    reimplementation re-tokenizes the whole corpus per query, so the full ~1300
    queries are slow). Sampling is uniform across courses/sections by position.
    """
    eligible = []
    for rec in records:
        if rec["source_type"] != "faq":
            continue
        q = question_query(rec)
        if len(q.split()) < 3:
            continue
        eligible.append({"query": q, "gold": rec["id"], "filter": filter_for(rec)})
    sampled = eligible[::sample_every]
    print(f"(sampled {len(sampled)} of {len(eligible)} eligible FAQ queries, every {sample_every}th)")
    return sampled


def ids(results: list[dict], k: int) -> list[str]:
    return [str(r.get("id", "")) for r in results[:k]]


def evaluate(records: list[dict], queries: list[dict]) -> None:
    ours = OursIndex(text_fields=TEXT_FIELDS, keyword_fields=KEYWORD_FIELDS).fit(records)
    real = RealIndex(text_fields=TEXT_FIELDS, keyword_fields=KEYWORD_FIELDS).fit(records)

    ours_r1 = ours_rk = real_r1 = real_rk = 0
    top1_match = 0
    overlaps = []
    rank_gaps = []  # MRR-of-gold difference (ours - real); >0 means ours ranks gold better

    def mrr_pos(result_ids: list[str], gold: str) -> float:
        for i, rid in enumerate(result_ids, start=1):
            if rid == gold:
                return 1.0 / i
        return 0.0

    for q in queries:
        o = ids(ours.search(q["query"], filter_dict=q["filter"], boost_dict=BOOSTS, num_results=K * 3), K * 3)
        r = ids(real.search(q["query"], filter_dict=q["filter"], boost_dict=BOOSTS, num_results=K * 3), K * 3)
        gold = q["gold"]

        ours_r1 += o[:1] == [gold]
        real_r1 += r[:1] == [gold]
        ours_rk += gold in o[:K]
        real_rk += gold in r[:K]

        if o[:1] and r[:1]:
            top1_match += o[0] == r[0]
        overlaps.append(len(set(o[:K]) & set(r[:K])) / K)
        rank_gaps.append(mrr_pos(o, gold) - mrr_pos(r, gold))

    n = len(queries)
    print(f"\nQuery set: {n} FAQ self-retrieval queries\n")
    print(f"{'metric':<26}{'ours':>10}{'real minsearch':>18}")
    print("-" * 54)
    print(f"{'recall@1':<26}{ours_r1/n:>10.3f}{real_r1/n:>18.3f}")
    print(f"{'recall@'+str(K):<26}{ours_rk/n:>10.3f}{real_rk/n:>18.3f}")
    print()
    print("Agreement between the two engines:")
    print(f"  top-1 identical:      {top1_match/n:.3f}")
    print(f"  mean overlap@{K}:       {statistics.mean(overlaps):.3f}")
    print(f"  queries w/ full top-{K} overlap: {sum(1 for x in overlaps if x==1.0)}/{n}")
    pos = sum(1 for g in rank_gaps if g > 1e-9)
    neg = sum(1 for g in rank_gaps if g < -1e-9)
    print(f"  ours ranks gold strictly higher: {pos}    real higher: {neg}    tie: {n-pos-neg}")


def show_examples(records: list[dict], queries: list[dict], n: int = 8) -> None:
    ours = OursIndex(text_fields=TEXT_FIELDS, keyword_fields=KEYWORD_FIELDS).fit(records)
    real = RealIndex(text_fields=TEXT_FIELDS, keyword_fields=KEYWORD_FIELDS).fit(records)
    by_id = {r["id"]: r for r in records}
    print("\n\nDisagreement examples (top-1 differs):")
    shown = 0
    for q in queries:
        if shown >= n:
            break
        o = ours.search(q["query"], filter_dict=q["filter"], boost_dict=BOOSTS, num_results=K)
        r = real.search(q["query"], filter_dict=q["filter"], boost_dict=BOOSTS, num_results=K)
        if not o or not r or o[0]["id"] == r[0]["id"]:
            continue
        shown += 1
        gold = q["gold"]
        print(f"\nQ: {q['query']}")
        print(f"   gold: {by_id[gold]['title'][:70]}")
        print(f"   ours #1: {by_id.get(o[0]['id'],{}).get('title','?')[:70]}  {'<<GOLD' if o[0]['id']==gold else ''}")
        print(f"   real #1: {by_id.get(r[0]['id'],{}).get('title','?')[:70]}  {'<<GOLD' if r[0]['id']==gold else ''}")


def main() -> int:
    records = json.loads(CORPUS.read_text(encoding="utf-8"))
    queries = build_queries(records)
    evaluate(records, queries)
    show_examples(records, queries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
