# FAQ Assistant

Simplified DataTalks.Club Slack FAQ assistant.

Runtime flow:

```text
Slack mention -> scope detection -> OpenAI query rewrite -> zerosearch -> OpenAI RAG answer -> Slack thread reply
```

Course channels use course-scoped FAQ plus course markdown. Other channels use the general
DataTalks.Club docs corpus from `DataTalksClub/docs`.

## Local setup

```bash
uv sync
```

Required environment variables for corpus rebuilds and deployment:

```bash
CLOUDFLARE_ACCOUNT_ID=...
CLOUDFLARE_API_TOKEN=...
OPENAI_API_KEY=...
GITHUB_TOKEN=...
```

For the Lambda-to-Worker `/ask` endpoint, set the same shared secret in both
Cloudflare and au-tomator:

```bash
FAQ_ASSISTANT_SHARED_SECRET=...
```

For local Worker testing, put the same value in `.dev.vars`; Wrangler reads that file
and exposes it to the Worker.

Build the local zerosearch corpus from the configured sources:

```bash
uv run --group ingest python scripts/build_search_corpus.py
```

This writes:

```text
src/faq_assistant/search_corpus.py
artifacts/search/search-corpus.json
```

The generated Python module is committed/deployed with the Worker. The JSON
artifact is ignored by Git and is only for local inspection.

## Deployment

Rebuild the corpus and compile `config.toml` before deploying:

```bash
uv run --group ingest python scripts/build_search_corpus.py
uv run python scripts/compile_config.py
```

Deploy the Worker:

```bash
CLOUDFLARE_ACCOUNT_ID=... \
CLOUDFLARE_API_TOKEN=... \
uv run pywrangler deploy --keep-vars
```

The current production Worker URL is:

```text
https://faq-assistant.cloudflare-ai-agent-de9ca0.workers.dev
```

The au-tomator Lambda calls the `/ask` endpoint:

```text
https://faq-assistant.cloudflare-ai-agent-de9ca0.workers.dev/ask
```

Set the shared secret in Cloudflare:

```bash
printf '%s' "$FAQ_ASSISTANT_SHARED_SECRET" |
  CLOUDFLARE_ACCOUNT_ID=... \
  CLOUDFLARE_API_TOKEN=... \
  uv run pywrangler secret put FAQ_ASSISTANT_SHARED_SECRET
```

Set the OpenAI key in Cloudflare:

```bash
printf '%s' "$OPENAI_API_KEY" |
  CLOUDFLARE_ACCOUNT_ID=... \
  CLOUDFLARE_API_TOKEN=... \
  uv run pywrangler secret put OPENAI_API_KEY
```

Set the same value in the au-tomator automator Lambda:

```text
FAQ_ASSISTANT_URL=https://faq-assistant.cloudflare-ai-agent-de9ca0.workers.dev/ask
FAQ_ASSISTANT_SHARED_SECRET=...
```

Smoke-test the deployed Worker:

```bash
curl https://faq-assistant.cloudflare-ai-agent-de9ca0.workers.dev/health
```

`/ask` must reject requests without the shared secret:

```bash
curl -i -X POST https://faq-assistant.cloudflare-ai-agent-de9ca0.workers.dev/ask \
  -H 'content-type: application/json' \
  -d '{"question":"How do I join Slack?","scope":"docs"}'
```

Expected status:

```text
401 Unauthorized
```

Then test with the shared secret:

```bash
curl -i -X POST https://faq-assistant.cloudflare-ai-agent-de9ca0.workers.dev/ask \
  -H 'content-type: application/json' \
  -H "x-faq-assistant-secret: $FAQ_ASSISTANT_SHARED_SECRET" \
  -d '{"question":"How do I join DataTalks.Club Slack?","scope":"docs"}'
```

This should return a RAG answer.

## Local Worker testing

Compile `config.toml` into the Python module imported by the Worker:

```bash
uv run python scripts/compile_config.py
```

Start the Worker locally:

```bash
uv run pywrangler dev --port 8792
```

The Worker uses OpenAI for query rewrite and structured chat. Search runs in
memory with the generated zerosearch corpus. `/ask` requires `OPENAI_API_KEY`
available to the Worker.

### Health payload

```bash
curl http://localhost:8792/health
```

Expected shape:

```json
{
  "ok": true,
  "app": "faq-assistant"
}
```

### Ask payload: docs scope

Use this when the bot is tagged outside a known course channel.

```bash
curl -X POST http://localhost:8792/ask \
  -H 'content-type: application/json' \
  -H "x-faq-assistant-secret: $FAQ_ASSISTANT_SHARED_SECRET" \
  -d '{
    "question": "How do I join DataTalks.Club Slack?",
    "scope": "docs"
  }'
```

### Ask payload: course scope

Use this to simulate a mention in `#course-llm-zoomcamp`.

```bash
curl -X POST http://localhost:8792/ask \
  -H 'content-type: application/json' \
  -H "x-faq-assistant-secret: $FAQ_ASSISTANT_SHARED_SECRET" \
  -d '{
    "question": "Can I still join after the course started?",
    "scope": "course",
    "course": "llm-zoomcamp"
  }'
```

Expected response shape:

```json
{
  "question": "Can I still join after the course started?",
  "rewritten_query": "join course after start date",
  "scope": "course",
  "course": "llm-zoomcamp",
  "results": [
    {
      "id": "faq:...",
      "score": 0.78,
      "source_type": "faq",
      "scope": "course",
      "course": "llm-zoomcamp",
      "section": "General Course-Related Questions",
      "title": "Course: Can I still join the course after the start date?",
      "text": "...",
      "url": "https://datatalks.club/faq/",
      "repo": "",
      "path": ""
    }
  ],
  "answer": "..."
}
```

### Slack URL verification payload

Slack sends this when configuring Event Subscriptions:

```json
{
  "type": "url_verification",
  "token": "deprecated-verification-token",
  "challenge": "challenge-string-from-slack"
}
```

The Worker verifies the Slack signature before returning:

```json
{
  "challenge": "challenge-string-from-slack"
}
```

### Slack app mention payload

This simulates a mention in the real LLM Zoomcamp channel, `C06TEGTGM3J`.

```json
{
  "token": "deprecated-verification-token",
  "team_id": "T01ATQK62F8",
  "api_app_id": "A1234567890",
  "type": "event_callback",
  "event_id": "Ev1234567890",
  "event_time": 1790000000,
  "event": {
    "type": "app_mention",
    "user": "U1234567890",
    "text": "<@UFAQBOT> Can I still join after the course started?",
    "ts": "1790000000.000100",
    "channel": "C06TEGTGM3J",
    "event_ts": "1790000000.000100"
  }
}
```

The Worker immediately returns:

```json
{
  "ok": true
}
```

Then it posts the answer to the same Slack thread with `chat.postMessage`.

To send a signed local request, set the same signing secret used by the Worker:

```bash
export SLACK_SIGNING_SECRET='your-local-signing-secret'
payload="$(
  cat <<'JSON'
{"type":"event_callback","event":{"type":"app_mention","user":"U1234567890","text":"<@UFAQBOT> Can I still join after the course started?","ts":"1790000000.000100","channel":"C06TEGTGM3J","event_ts":"1790000000.000100"}}
JSON
)"
timestamp="$(date +%s)"
signature="$(
  printf 'v0:%s:%s' "$timestamp" "$payload" |
  openssl dgst -sha256 -hmac "$SLACK_SIGNING_SECRET" -hex |
  sed 's/^.* //'
)"

curl -X POST http://localhost:8792/slack/events \
  -H 'content-type: application/json' \
  -H "x-slack-request-timestamp: $timestamp" \
  -H "x-slack-signature: v0=$signature" \
  -d "$payload"
```

### Structured output check

Validate the local parser and structured models:

```bash
uv run python scripts/check_structured_parsing.py
```

Validate that the configured OpenAI model returns structured output that parses into our
structured models:

```bash
uv run --group ingest python scripts/check_structured_output.py
```

Validate the full RAG path against the generated zerosearch corpus:

```bash
uv run --group ingest python scripts/check_rag.py
```

Known course channel IDs:

| Course | Slack channel | Channel ID |
| --- | --- | --- |
| Data Engineering Zoomcamp | `#course-data-engineering` | `C01FABYF2RG` |
| Machine Learning Zoomcamp | `#course-ml-zoomcamp` | `C0288NJ5XSA` |
| MLOps Zoomcamp | `#course-mlops-zoomcamp` | `C02R98X7DS9` |
| LLM Zoomcamp | `#course-llm-zoomcamp` | `C06TEGTGM3J` |
| AI Dev Tools Zoomcamp | `#course-ai-dev-tools-zoomcamp` | `C09HWT76L95` |
| Stock Markets Analytics Zoomcamp | `#course-stocks-analytics-zoomcamp` | `C06L1RTF10F` |
