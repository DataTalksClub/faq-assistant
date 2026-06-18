# FAQ Assistant

Simplified DataTalks.Club Slack FAQ assistant, deployed as an AWS Lambda behind a
Function URL.

Runtime flow:

```text
Slack mention -> ack Lambda (acks in <3s, maps channel -> scope/course)
             -> HTTP POST /ask (this Lambda): OpenAI query rewrite -> zerosearch -> OpenAI RAG answer
             -> ack Lambda posts the answer back to the Slack thread
```

This service is just the `/ask` worker: it takes `{question, scope, course}` over HTTP
(authenticated with a shared-secret header) and returns the answer JSON. Slack signature
verification and posting live in the separate ack Lambda.

Course channels use course-scoped FAQ plus course markdown. Other channels use the general
DataTalks.Club docs corpus from `DataTalksClub/docs`.

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

The corpus is built from the configured sources, then fitted into a packed
[`zerosearch`](https://github.com/alexeygrigorev/zerosearch) index that the Lambda loads
in ~15 ms (instead of re-tokenizing on every cold start).

```bash
make corpus   # build the corpus     -> artifacts/search/search-corpus.json (+ search_corpus.py)
make index    # fit + save the index -> artifacts/search/search-index.zsx
```

Both artifacts are git-ignored and rebuilt in CI before each deploy. The packed index is
tagged with the build Python version; it must be built on the same Python as the Lambda
runtime (**3.14**), or loading fails loudly. CI pins both.

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

`.github/workflows/rebuild-index.yml` rebuilds the corpus + index daily and on demand, smoke-tests
the handler, and runs `sam deploy`. It needs these repository secrets: `AWS_DEPLOY_ROLE_ARN`,
`AWS_REGION`, `OPENAI_API_KEY`, `FAQ_ASSISTANT_SHARED_SECRET`.

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
