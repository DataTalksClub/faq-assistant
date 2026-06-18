# Retrieval evaluation

This measures how well the FAQ-assistant **retrieval** finds the right answer for
**real questions that users actually asked in Slack**. We use it to answer two
questions:

1. **Which query-rewrite instructions give the best retrieval?**
2. **Is the `zerosearch` engine as good as the original `minsearch`?**

We follow the llm-zoomcamp retrieval-evaluation framework: **hit rate** and
**MRR** over a labeled ground-truth set.

**Decision from this eval:** the `verbatim` rewrite wins (hit@5 ≈ 0.62). It is
wired into the Worker's `rewrite_query`, and `zerosearch` is the engine.

---

## 1. What we compare

Two independent axes, both scored with the same metrics:

**a) Query-rewrite instructions** (`VARIANTS` in `run_eval.py`). Before retrieval,
the raw Slack message is turned into a search query by `gpt-4o-mini` using one of
these system prompts:

| variant     | what its prompt tells the model to do |
|-------------|----------------------------------------|
| `raw`       | *no rewrite at all* — search the literal Slack message (baseline) |
| `current`   | the original production prompt: fix typos, drop mentions/filler, keep technical terms |
| `verbatim`  | `current` **plus** "preserve exact error messages, tool names, commands, file names verbatim" |
| `keywords`  | extract only key terms/nouns/tool names, drop everything else |
| `light`     | only light cleanup, keep most of the original wording |
| `expansion` | rewrite **and** expand abbreviations + add synonyms |
| `minimal`   | "turn it into a short keyword query" with no other guidance |

**b) Search engines** (`--engine`): `zerosearch` (the vendored BM25-lite engine)
vs `minsearch` (the original TF-IDF + cosine library). Both are run over the same
corpus with the same course filter and the same rewrite variants.

**Metrics** (averaged over all ground-truth queries, for k ∈ {1, 3, 5}):

- `hit_rate@k` — fraction of queries with **at least one** relevant doc in the top-k.
- `mrr@k` — mean reciprocal rank of the **first** relevant doc (1/rank, 0 if none in top-k).

---

## 2. How the ground truth was built

Ground truth = `data/ground_truth.jsonl`, built by `build_dataset.py`. We can't
just use the FAQ questions as queries (too clean); we want *real* messy user
phrasing. But real Slack messages have no "correct document" label, so we create
one with **pooled relevance judgments** — the standard IR method.

Step by step:

1. **Collect real queries** (`lib.load_slack_questions`). Read the Slack thread
   exports from the `../faq` repo (`.tmp/slack/course-*.threads.jsonl`). Keep a
   thread only if it is a genuine, answerable question:
   - `n_replies >= 1` (it actually got an answer in Slack),
   - passes `lib.looks_like_question`: contains `?`, 25–400 chars, not an
     announcement/list/link-dump,
   - not posted by staff (DataTalksClub / course instructors),
   - de-duplicated, URLs stripped.
   This yields ~6.8k candidate questions across the courses.

2. **Sample** a fixed, seeded subset per course (`SAMPLE_PER_COURSE`): 60
   data-engineering + 30 ai-dev-tools + 40 stock-markets = **130 queries**.
   Saved to `data/slack_sample.jsonl`.

3. **Pool candidate documents** (`build_pool`). For each sampled query we gather a
   *pool* of documents from the production corpus by unioning the top-20 results of
   several retrievers, so the pool is not biased toward one ranking:
   - `zerosearch` on the raw query,
   - `minsearch` on the raw query,
   - `zerosearch` on a neutral `gpt-4o-mini` rewrite of the query.

4. **Judge relevance** (`judge`, model `gpt-5.4-mini`). The judge sees the question
   and the numbered pool, and returns only the candidates that *directly and
   specifically* answer the question (strict prompt: usually 1–2, rarely >3; return
   `[]` if the question is vague or nothing answers it). Queries the judge can't
   match to any document are **dropped**.

5. **Result:** **128 queries** survive (2 dropped as unanswerable), with a mean of
   ~2.8 relevant documents each. Each row:
   ```json
   {"query": "...", "course": "data-engineering-zoomcamp", "scope": "course",
    "relevant_ids": ["faq:...:0000", "..."], "pool_size": 41,
    "channel": "course-data-engineering", "thread_ts": "..."}
   ```

The documents being judged are the *exact* chunks the production Worker retrieves
(`faq_assistant.search_corpus.SEARCH_CORPUS_B64`), so the ids in `relevant_ids`
line up with what the Worker would return.

---

## 3. How to run

```bash
# (1) Build / rebuild the ground truth. Needs the real minsearch for pooling.
#     Writes data/slack_sample.jsonl and data/ground_truth.jsonl.
uv run --with minsearch python evals/build_dataset.py

# (2) Sweep all rewrite variants on zerosearch.
#     Prints the metrics table; writes results/zerosearch__<variant>.json.
uv run python evals/run_eval.py --engine zerosearch --variant all

# (3) Same sweep on the real minsearch, to compare engines on identical queries.
uv run --with minsearch python evals/run_eval.py --engine minsearch --variant all

# Run a single variant only:
uv run python evals/run_eval.py --engine zerosearch --variant verbatim
```

Files:
- `lib.py` — shared helpers (corpus loader, both engines, Slack loading/filtering,
  a stdlib-only OpenAI client).
- `build_dataset.py` — builds the ground truth (section 2).
- `run_eval.py` — runs the metrics (sections 1 & 3); per-query detail and a
  `summary__<engine>.json` land in `results/`.

Both scripts call OpenAI and need `OPENAI_API_KEY` (loaded from the repo `.env`).
`build_dataset.py` and the per-query rewrites run concurrently (8 workers).

---

## Results (128 real Slack queries)

### Query-rewrite variants — zerosearch

| variant            | hit@1 | hit@3 | hit@5 | mrr@5 |
|--------------------|------:|------:|------:|------:|
| raw (no rewrite)   | 0.24  | 0.47  | 0.555 | 0.362 |
| current (prod)     | 0.31  | 0.52  | 0.59  | 0.42  |
| **verbatim (best)**| 0.30  | 0.54  | **0.62** | **0.43** |
| keywords           | 0.27  | 0.46  | 0.55  | 0.37  |
| light              | 0.29  | 0.44  | 0.54  | 0.37  |
| expansion          | 0.25  | 0.36  | 0.49  | 0.33  |
| minimal            | 0.22  | 0.34  | 0.44  | 0.29  |

### Engine comparison (best rewrite per engine)

| engine            | best variant | hit@5 | mrr@5 |
|-------------------|--------------|------:|------:|
| **zerosearch**    | verbatim     | **0.62** | **0.43** |
| minsearch (real)  | current      | 0.586 | 0.419 |

On the same rewrite, `zerosearch` ties or slightly beats `minsearch` (raw: 0.555
vs 0.492; current: ~tied), and is markedly more robust to aggressive rewrites
(minsearch drops to ~0.33–0.41 on `minimal`/`keywords`, zerosearch holds
~0.42–0.55). **zerosearch is not worse than minsearch on real queries.**

## Findings

- **Rewriting helps, but *how* matters.** The winner distills the chatty Slack
  message to keywords while **preserving exact error messages / tool / command /
  file names** (`verbatim`). Over-compressing (`minimal`) or paraphrasing/adding
  synonyms (`expansion`) **hurts** — they drop the exact tokens keyword search
  relies on. Sending the message untouched (`raw`) leaves filler in the query and
  costs ~6 pts hit@5.
- **Realistic ceiling ≈ 0.62 hit@5.** Many Slack messages are vague or
  conversational, so this is the bar to improve against — gains will come from
  handling underspecified questions, not from swapping the engine.

## Caveats

- Rewrites use `gpt-4o-mini` at temperature 0 but are not perfectly
  deterministic; run-to-run variance is ~±0.02 on hit@5.
- Ground truth is pooled from retrieval, so recall is measured relative to that
  pool (standard pooled-IR caveat), and the `gpt-5.4-mini` judge is strict but
  imperfect.
