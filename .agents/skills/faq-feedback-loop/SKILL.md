---
name: faq-feedback-loop
description: Close the loop on the @automator FAQ bot — find Slack questions it couldn't answer or got corrected on, add them to the eval set, fill the FAQ content gaps in the faq source repo, verify retrieval improves, then publish. Use when the user wants to review how the bot did in Slack, mine bot failures/corrections, fix FAQ gaps, or refresh the answer-gap eval set.
---

# FAQ feedback loop

Turn real @automator failures in the DataTalks.Club Slack into FAQ fixes, with an
eval trail. Spans two repos:

- **`faq-assistant`** (this repo) — the `/ask` worker + retrieval evals. Holds the
  scanner, the verifier, and the answer-gap eval set.
- **`faq`** (sibling, `~/git/faq` / `$FAQ_REPO`) — the FAQ **source** (`_questions/<course>/<section>/*.md`).
  This is where content gaps get fixed. The bot reads the *published* FAQ
  (`datatalks.club/faq/`), so source edits aren't live until pushed.

The `SLACK_BOT_TOKEN` lives in `~/git/faq/.env`; copy it into this repo's `.env`
(already gitignored — **never print it**). The token belongs to the @automator bot
itself, so its own replies are visible. Everything runs through **`uv run`** (the
repo convention; the Slack scanner is stdlib-only, so it uses `uv run --no-project`).

### Which channels to pull

The scanner reads its channel list from `[slack.channels.*]` in `config.toml` — the
six course channels — and scans all of them by default:

| channel id | name | course |
|---|---|---|
| `C01FABYF2RG` | course-data-engineering | Data Engineering Zoomcamp |
| `C02R98X7DS9` | course-mlops | MLOps Zoomcamp |
| `C06TEGTGM3J` | course-llm | LLM Zoomcamp |
| `C0288NJ5XSA` | course-ml | Machine Learning Zoomcamp |
| `C09HWT76L95` | course-ai-dev-tools | AI Dev Tools Zoomcamp |
| `C06L1RTF10F` | course-stocks-analytics | Stock Markets Analytics Zoomcamp |

Pull all of them (default), or narrow to one with `--channel <id>` — e.g. when only
one course is actively running, scan just its channel. Activity concentrates in the
course that's live; expect most/all findings from that one.

## Steps

### 1. Fetch — what the bot got wrong

```bash
uv run --no-project python scripts/slack_faq_review.py --days 60 --json .tmp/slack-faq-review.json
```

Scans every course channel in `config.toml` for threads where the bot was triggered
(`@mention` or `faq` reaction) and reports two kinds of finding:

- **no-answer** — the bot replied "I couldn't find this in the course materials/docs".
- **corrected** — the bot answered, then **Alexey Grigorev** (`U01AXE0P5M3`) replied
  afterwards (i.e. he corrected it or added info).

Flags: `--mode no-answer|corrected|all`, `--channel <id>`, `--days N`. Read the
instructor follow-ups in the corrected threads — that text is usually the *correct
answer* you'll encode into the FAQ.

### 2. Add to the eval set

Curate the genuine gaps into `evals/data/answer_gaps.jsonl` (hand-written, one JSON
object per line). Skip noise (meta/testing comments, canned guideline nudges). Each row:

```json
{"query": "...", "course": "llm-zoomcamp", "scope": "course", "channel": "course-llm",
 "thread_ts": "...", "trigger": "faq-reaction", "failure": "no_answer|incorrect|incomplete",
 "bot_answer": "...", "expected_answer": "<the instructor's correct answer>",
 "answer_source": "alexey-grigorev", "permalink": "...", "filled_by": null}
```

This is **separate from `ground_truth.jsonl`** on purpose: these are FAQ *content*
gaps (the answer isn't in the corpus), so they fail the pooled-judgment filter and
don't belong in the retrieval ground truth. Leave `filled_by` null until step 3.
See `evals/README.md` §4.

### 3. Fix the FAQ (in the `faq` repo)

For each gap, first check it isn't already covered:

```bash
grep -ri "<keyword>" $FAQ_REPO/_questions/<course>/
```

Add new entries with the faq repo's own conventions (see its `slack-faq-fetch` skill):
file `_questions/<course>/<section>/<NNN>_<docid>_<slug>.md`, where `NNN` = next
`sort_order` in the section, `<docid>` = MD5(question + " " + answer)[:10], frontmatter
`{id, question, sort_order}`. The repo's `faq_automation.core` helpers generate these
exactly (`generate_document_id`, `find_largest_sort_order`, `write_frontmatter`,
`_slugify`); run them with `PYTHONPATH=$FAQ_REPO faq_automation/.venv/bin/python`.
Then back-fill each eval row's `filled_by` with the new entry's `<docid>`.

### 4. Verify retrieval improves

```bash
uv run python scripts/verify_faq_retrieval.py
```

Reads the drafted markdown straight from `$FAQ_REPO`, builds the exact corpus record
the pipeline would, splices it into the current corpus, and reports production
retrieval **before vs after** for every `answer_gaps.jsonl` row with a `filled_by`
id (plus an LLM relevance judgment). Confirm the previously-failing questions now
retrieve their entry in the top-5. A residual miss is fine to note (e.g. a two-part
question whose other half is already covered).

### 5. Re-run the retrieval eval (optional sanity check)

After the FAQ is published and the corpus rebuilt (step 6), confirm no regression on
the existing ground truth:

```bash
make corpus            # re-fetch published FAQ into artifacts/search/search-corpus.json
uv run python evals/run_eval.py --engine zerosearch --variant production
```

### 6. Publish — **ask the user before pushing**

The corpus only sees the new entries once the FAQ source is pushed and the site
republishes. Confirm with the user first, then push **faq first, this repo second**:

```bash
# a) FAQ content goes live
cd $FAQ_REPO && git add _questions/ && git commit -m "Add FAQ: <summary>" && git push   # commits straight to main

# b) eval/script changes in this repo
cd <faq-assistant> && git add evals/data/answer_gaps.jsonl scripts/ evals/README.md .claude/ \
  && git commit -m "Track answer gaps + curation tooling" && git push
```

**Deploy is automatic — no `make deploy` needed.** Both pushes trigger CI:
- `faq` push → `build-website.yml` rebuilds the FAQ site to GitHub Pages, so
  `datatalks.club/faq/` republishes with the new entries.
- `faq-assistant` push → `deploy.yml` runs **when changed paths match its filter**
  (`src/**`, `config.toml`, `scripts/**`, `Makefile`, … — note `evals/**` and
  `.claude/**` do **not** match, so an evals-only commit won't deploy by itself). It
  reruns tests, rebuilds the corpus from the *live* FAQ, refits the index, and
  SAM-deploys the Lambda. A daily 08:00 UTC cron in `deploy.yml` does the same
  regardless of pushes.

Ordering caveat: the faq-assistant deploy rebuilds from the *published* FAQ, so if it
runs before GitHub Pages finishes republishing, that build sees the old FAQ. The
daily cron (or any later deploy / `workflow_dispatch`) reconciles it — to force it
sooner, re-run the Deploy workflow once the site is live.

## Notes

- `.tmp/` and `.env` are gitignored — the Slack export and token never get committed.
- Corpus/index artifacts under `artifacts/` are gitignored too; they're rebuilt by
  `make corpus` / `make index`, not pushed.
- Scope the bot uses: course channels → course FAQ + course markdown; other channels
  → general docs. Channel→course mapping is in `config.toml`.
