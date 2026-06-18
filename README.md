# FAQ Assistant

Simplified DataTalks.Club Slack FAQ assistant, deployed as an AWS Lambda behind a
Function URL.

A lightweight redesign of [aaalexlit/faq-slack-bot](https://github.com/aaalexlit/faq-slack-bot):
it swaps vector search for keyword text search ([`zerosearch`](https://github.com/alexeygrigorev/zerosearch),
a zero-dependency BM25-lite index) and runs on super-minimal infrastructure — a single Lambda
with a prebuilt in-memory index, no vector database, no servers, and effectively no fixed cost.

Runtime flow:

```text
Slack mention -> ack Lambda (acks in <3s, maps channel -> scope/course)
             -> HTTP POST /ask (this Lambda): OpenAI query rewrite -> zerosearch -> OpenAI RAG answer
             -> ack Lambda posts the answer back to the Slack thread
```

This service is just the `/ask` worker: it takes `{question, scope, course}` over HTTP
(authenticated with a shared-secret header) and returns the answer JSON. It is wired into
Slack by the [DataTalksClub/au-tomator-lambda](https://github.com/DataTalksClub/au-tomator-lambda)
bot (the "ack Lambda"), which receives the Slack event, acks within Slack's 3-second window,
maps the channel to a scope/course, calls this endpoint, and posts the answer back to the thread.

Course channels use course-scoped FAQ plus course markdown. Other channels use the general
DataTalks.Club docs corpus from `DataTalksClub/docs`.

## Architecture

Deliberately minimal — no servers, no vector database, effectively no fixed cost:

- **One AWS Lambda** (`python3.14`, arm64) behind a **Function URL** (`AuthType: NONE`);
  requests are authenticated by the `x-faq-assistant-secret` shared-secret header.
- **No runtime dependencies beyond `zerosearch`** — the OpenAI call uses stdlib `urllib`,
  and structured models are hand-rolled (no `pydantic`, no `requests`).
- **Prebuilt search index** baked into the deployment package and loaded into memory on cold
  start in ~15 ms (see below), so there is no database to run or query.
- **Observability** via a structured JSON usage/cost log line per request, captured by
  CloudWatch Logs.
- **Infra as code** with AWS SAM (`template.yaml`); pay-per-request, so an idle bot costs
  nothing.

### How the index is created

The retrieval index is fitted offline and shipped as a packed artifact rather than rebuilt at
runtime:

1. `make corpus` ingests the configured sources (`DataTalksClub/docs`, course FAQ + markdown),
   chunks them, and writes `artifacts/search/search-corpus.json`.
2. `make index` fits a [`zerosearch`](https://github.com/alexeygrigorev/zerosearch) `Index` over
   that corpus and saves the packed, flat-buffer form to `artifacts/search/search-index.zsx`
   (~9 MB).
3. `sam build` bundles that `.zsx` into the Lambda zip; at cold start the handler calls
   `Index.load(...)`, which `memcpy`s the postings arrays instead of re-tokenizing the corpus —
   ~15 ms versus ~520 ms for a fresh `fit()`.

The packed index is tagged with the Python version it was built on and must match the Lambda
runtime (3.14), so CI builds the index and deploys on the same Python. The artifacts are
git-ignored and rebuilt daily by CI.

## Local setup

```bash
uv sync
```

Environment variables:

```bash
OPENAI_API_KEY=...                # query rewrite + RAG answer
FAQ_ASSISTANT_SHARED_SECRET=...   # callers send this in the x-faq-assistant-secret header
GITHUB_TOKEN=...                  # only for corpus rebuilds (the `ingest` group)
```

## Search corpus and index

See [How the index is created](#how-the-index-is-created) above for the design. The commands:

```bash
make corpus   # build the corpus     -> artifacts/search/search-corpus.json (+ search_corpus.py)
make index    # fit + save the index -> artifacts/search/search-index.zsx
```

## Local testing

Offline smoke test of routing, auth and the full pipeline (stubbed OpenAI call, no network):

```bash
make index
uv run python scripts/check_handler.py
```

`make check` runs the config compile, structured-parsing check, an index build, the handler
smoke test, and `compileall`.

To exercise the real handler locally with SAM (needs `OPENAI_API_KEY` in the environment):

```bash
make index
sam build
echo '{"requestContext":{"http":{"method":"POST","path":"/ask"}},"headers":{"x-faq-assistant-secret":"'"$FAQ_ASSISTANT_SHARED_SECRET"'"},"body":"{\"question\":\"How do I join Slack?\",\"scope\":\"docs\"}"}' \
  | sam local invoke FaqWorkerFunction -e -
```

## Deployment (AWS SAM)

See [docs/deployment.md](docs/deployment.md) for the full setup — the one-time
prerequisites (GitHub OIDC provider, bootstrap deploy, repo secrets), the
least-privilege deploy role, and how to port it to a new/production account.
Pushes to `main` then deploy automatically via GitHub Actions.

Install the [SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html),
then first-time deploy interactively (writes `samconfig.toml`):

```bash
make index
sam build
sam deploy --guided \
  --parameter-overrides OpenAIApiKey=$OPENAI_API_KEY SharedSecret=$FAQ_ASSISTANT_SHARED_SECRET
```

Subsequent deploys: `make deploy`. The stack creates the `python3.14` arm64 function and a
Function URL (`AuthType: NONE` — auth is the shared-secret header). The URL is printed as the
`FunctionUrl` stack output.

Secrets are passed as CloudFormation parameters and stored as Lambda environment variables.
For stricter handling, move them to SSM Parameter Store / Secrets Manager and read them at
init time.

### CI

`.github/workflows/deploy.yml` runs on push to `main`, a daily cron, and on demand. Every run
rebuilds the corpus + index from the live sources, smoke-tests the handler, and `sam deploy`s via
GitHub OIDC. It needs these repository secrets: `AWS_DEPLOY_ROLE_ARN`, `AWS_REGION`,
`OPENAI_API_KEY`, `FAQ_ASSISTANT_SHARED_SECRET`. See [docs/deployment.md](docs/deployment.md).

## Smoke-testing the deployed endpoint

```bash
URL=https://<your-function-url>

curl "$URL/health"                                    # {"ok": true, "app": "faq-assistant"}

curl -i -X POST "$URL/ask" \
  -H 'content-type: application/json' \
  -d '{"question":"How do I join Slack?","scope":"docs"}'   # 401 without the secret

curl -X POST "$URL/ask" \
  -H 'content-type: application/json' \
  -H "x-faq-assistant-secret: $FAQ_ASSISTANT_SHARED_SECRET" \
  -d '{"question":"How do I join DataTalks.Club Slack?","scope":"docs"}'
```

Response shape:

```json
{
  "question": "...",
  "rewritten_query": "...",
  "scope": "course",
  "course": "llm-zoomcamp",
  "results": [{"id": "faq:...", "score": 0.78, "source_type": "faq", "title": "...", "text": "...", "url": "..."}],
  "answer": "...",
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
}
```

## Structured-output and RAG checks

```bash
uv run python scripts/check_structured_parsing.py          # local parser + structured models
uv run --group ingest python scripts/check_structured_output.py  # live OpenAI structured output
uv run --group ingest python scripts/check_rag.py          # full RAG path against the corpus
```

## Known course channels

The ack Lambda maps these channels to `scope`/`course`:

| Course | Slack channel | Channel ID |
| --- | --- | --- |
| Data Engineering Zoomcamp | `#course-data-engineering` | `C01FABYF2RG` |
| Machine Learning Zoomcamp | `#course-ml-zoomcamp` | `C0288NJ5XSA` |
| MLOps Zoomcamp | `#course-mlops-zoomcamp` | `C02R98X7DS9` |
| LLM Zoomcamp | `#course-llm-zoomcamp` | `C06TEGTGM3J` |
| AI Dev Tools Zoomcamp | `#course-ai-dev-tools-zoomcamp` | `C09HWT76L95` |
| Stock Markets Analytics Zoomcamp | `#course-stocks-analytics-zoomcamp` | `C06L1RTF10F` |
