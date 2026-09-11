# Running the evals

Two independent eval suites live under `evals/` — see `evals/README.md` for the
full methodology (how ground truth is built, metric definitions, caveats).
This doc is just the terminal commands.

## 1. Retrieval eval (hit rate / MRR)

Compares query-rewrite prompts and search engines against real Slack questions,
judged with pooled relevance labels. Writes JSON to `evals/results/`, prints a
metrics table.

```bash
# Rebuild ground truth (only needed after the corpus or Slack export changes)
uv run --with minsearch python evals/build_dataset.py

# Sweep all rewrite variants
uv run python evals/run_eval.py --engine zerosearch --variant all
```

Needs `OPENAI_API_KEY` (`.env`).

## 2. Answer-gap regression (end-to-end)

Replays real bot failures/corrections from Slack (`evals/data/answer_gaps.jsonl`)
through the full rewrite → retrieve → answer pipeline and checks the output
against instructor-backed constraints (required/forbidden sources, required/
forbidden answer text).

**Plain version** — deterministic retrieval check, prints PASS/FAIL to the
terminal, exits nonzero on failure (usable as a CI gate):

```bash
uv run python evals/run_answer_gaps.py               # retrieval only, no API key needed
uv run python evals/run_answer_gaps.py --answers      # also calls OpenAI, checks answer text
```

**Opik-tracked version** — same checks as `--answers` above, but uploads the
rows as an Opik Dataset and scores them through `opik.evaluate()`, so every run
becomes a comparable Experiment in the `faq-assistant-lambda` project on
[Comet Opik Cloud](https://www.comet.com/opik) instead of only a terminal
report:

```bash
uv run --group evals python evals/opik_answer_gaps.py
```

Needs `OPENAI_API_KEY` and `OPIK_API_KEY` (`.env`).

Flags (same for both versions): `--no-local-drafts` (skip splicing unpublished
FAQ drafts from `../faq`, test only the published corpus), `--gaps` / `--corpus`
/ `--faq-repo` (override default paths). The Opik version also takes
`--experiment-name NAME` to pick an explicit name instead of an
auto-generated `answer-gaps-<random>` one — useful when comparing a specific
before/after change.

### Where to see the results

- **Terminal**: a summary line/table prints after the run; the Opik version
  also prints an `Opik experiment: https://...` link.
- **Comet Opik Cloud**: https://www.comet.com/opik → workspace → project
  `faq-assistant-lambda` → **Datasets** tab (`faq-assistant-answer-gaps`) for
  the raw rows, or **Experiments** tab to select multiple runs and diff their
  `source_constraints` / `answer_constraints` scores side by side — each run
  reuses the same dataset, so this is how you compare before/after a prompt or
  retrieval change over time.
