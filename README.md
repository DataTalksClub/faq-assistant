# FAQ Assistant

Simplified DataTalks.Club Slack FAQ assistant.

Runtime flow:

```text
Slack mention -> scope detection -> LLM query rewrite -> Vectorize search -> RAG answer -> Slack thread reply
```

Course channels use course-scoped FAQ plus course markdown. Other channels use the general
DataTalks.Club docs corpus from `DataTalksClub/docs`.

## Local setup

```bash
uv sync
```

Required environment variables for ingestion:

```bash
CLOUDFLARE_ACCOUNT_ID=...
CLOUDFLARE_API_TOKEN=...
GITHUB_TOKEN=...
```

Create the Vectorize index:

```bash
uv run faq-assistant index create
```

Run a full rebuild:

```bash
uv run faq-assistant ingest --mode rebuild
```

The rebuild lists existing vector IDs before ingestion, upserts the current chunks, and deletes
stale IDs that were not produced by the current run.
