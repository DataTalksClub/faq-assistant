# Retrieval evaluation

Measures how well the FAQ-assistant retrieval finds the right answer for **real
user questions**, following the llm-zoomcamp evaluation framework
(**hit rate** + **MRR**). Used to (a) pick the query-rewrite instructions that
maximise retrieval quality and (b) confirm the `zerosearch` engine is not worse
than the original `minsearch` on realistic queries.

## Data & ground truth

- **Queries**: real questions students asked in DataTalksClub course Slack
  channels, taken from the `../faq` repo (`.tmp/slack/*.threads.jsonl`). Only
  answered, question-shaped messages are kept (see `lib.looks_like_question`).
- **Documents**: the exact search corpus the production Worker uses
  (`faq_assistant.search_corpus.SEARCH_CORPUS_B64`).
- **Ground truth** (`data/ground_truth.jsonl`): built with **pooled relevance
  judgments** (the standard IR method) in `build_dataset.py`:
  1. For each query, pool candidates = union of what several retrievers surface
     (`zerosearch` + `minsearch` on the raw query, plus `zerosearch` on a neutral
     LLM rewrite) so the pool isn't biased to one ranking.
  2. A strict LLM judge (`gpt-5.4-mini`) keeps only the candidates that *directly*
     answer the question (vague/unanswerable queries are dropped).
- Result: **128 real queries**, mean ~2.8 relevant docs each, across
  data-engineering / ai-dev-tools / stock-markets zoomcamps.

## Metrics

For each query we rewrite it (`gpt-4o-mini`), retrieve top-k with the configured
engine (course-scoped, same filter as the Worker), and score:

- `hit_rate@k` — fraction of queries with a relevant doc in the top-k
- `mrr@k` — mean reciprocal rank of the first relevant doc

## How to run

```bash
# Build the ground truth (needs the real minsearch for pooling):
uv run --with minsearch python evals/build_dataset.py

# Sweep query-rewrite variants on zerosearch:
uv run python evals/run_eval.py --engine zerosearch --variant all

# Compare engines on the same real queries:
uv run --with minsearch python evals/run_eval.py --engine minsearch --variant all
```

Per-variant detail is written to `results/<engine>__<variant>.json`.

## Results (128 real Slack queries)

### Query-rewrite variants — zerosearch

| variant            | hit@1 | hit@3 | hit@5 | mrr@5 |
|--------------------|------:|------:|------:|------:|
| raw (no rewrite)   | 0.24  | 0.47  | 0.555 | 0.362 |
| current (prod)     | 0.31  | 0.52  | 0.59  | 0.42  |
| **verbatim (best)**| 0.30  | 0.53  | **0.62** | **0.43** |
| keywords           | 0.27  | 0.45  | 0.55  | 0.37  |
| light              | 0.29  | 0.44  | 0.53  | 0.38  |
| expansion          | 0.26  | 0.38  | 0.50  | 0.33  |
| minimal            | 0.23  | 0.34  | 0.42  | 0.29  |

### Engine comparison (best rewrite per engine)

| engine            | best variant | hit@5 | mrr@5 |
|-------------------|--------------|------:|------:|
| **zerosearch**    | verbatim     | **0.62** | **0.43** |
| minsearch (real)  | current      | 0.586 | 0.419 |

On the same rewrite, `zerosearch` ties or slightly beats `minsearch` (e.g. raw:
0.555 vs 0.492; current: ~tied), and is markedly more robust to aggressive
rewrites (minsearch drops to ~0.33–0.41 on `minimal`/`keywords`, zerosearch holds
~0.42–0.55). **zerosearch is not worse than minsearch on real queries.**

## Findings

- **Rewriting helps**, but *how* matters a lot. Light cleanup that keeps technical
  terms and **preserves exact error messages / tool names / commands / file names
  verbatim** is best (`verbatim`). Over-compressing (`minimal`) or adding synonyms
  (`expansion`) **hurts** — they drop or dilute the exact tokens keyword search
  needs. The winning prompt is now wired into the Worker (`rewrite_query`).
- **Absolute ceiling is ~0.62 hit@5** on raw real questions — many Slack messages
  are vague/conversational, so this is the realistic bar to improve against.

## Caveats

- Rewrites use `gpt-4o-mini` at temperature 0 but are not perfectly
  deterministic; run-to-run variance is ~±0.02 on hit@5.
- Ground truth is pooled from retrieval, so recall is measured relative to that
  pool (standard pooled-IR caveat). The LLM judge is strict but imperfect.
