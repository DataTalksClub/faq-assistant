# OpenAI Batch Processing

This project uses the OpenAI Batch API for offline corpus embeddings. Runtime
Slack questions still use synchronous API calls because users need an immediate
answer.

Sources checked on 2026-06-17:

- OpenAI Batch API guide: https://developers.openai.com/api/docs/guides/batch
- `text-embedding-3-small` model page: https://developers.openai.com/api/docs/models/text-embedding-3-small
- OpenAI pricing page: https://openai.com/api/pricing/

## Why Batch

Batch is useful when the result is not needed immediately. For this bot, the
daily index rebuild is the right use case:

- collect all FAQ and markdown chunks;
- embed them asynchronously;
- upsert the completed vectors into Cloudflare Vectorize.

OpenAI documents three practical benefits compared with synchronous requests:

- 50% lower cost for Batch processing;
- separate Batch rate limits;
- completion within a 24-hour window, often faster.

The tradeoff is latency. Batch is not appropriate for live Slack questions, so
the Worker embeds the rewritten user query synchronously.

## API Flow

The Batch API wraps normal API requests in a `.jsonl` file. Every line is one
request.

For embeddings, each line targets `/v1/embeddings`:

```jsonl
{"custom_id":"faq:course:llm-zoomcamp:123","method":"POST","url":"/v1/embeddings","body":{"model":"text-embedding-3-small","input":"chunk text","encoding_format":"float"}}
```

Each `custom_id` must be unique. We use the chunk ID, which lets us map the
returned vector back to the original document chunk even though OpenAI does not
guarantee output order.

The full flow is:

1. Write a JSONL request file.
2. Upload it to `/v1/files` with `purpose=batch`.
3. Create a batch with:

```json
{
  "input_file_id": "file-...",
  "endpoint": "/v1/embeddings",
  "completion_window": "24h"
}
```

4. Poll `/v1/batches/{batch_id}` until the status is terminal.
5. If the status is `completed`, download `/v1/files/{output_file_id}/content`.
6. Parse each JSONL output line and extract the embedding from
   `response.body.data[0].embedding`.
7. Upsert the vectors to Cloudflare Vectorize.

OpenAI output files are deleted automatically after 30 days.

## Statuses

OpenAI Batch statuses:

- `validating`: input file is being checked;
- `failed`: input validation failed;
- `in_progress`: requests are running;
- `finalizing`: outputs are being prepared;
- `completed`: output is ready;
- `expired`: the 24-hour window ended before all work completed;
- `cancelling`: cancellation requested;
- `cancelled`: batch was cancelled.

If a batch expires, completed requests are still available in the output file,
and expired requests are written to the error file. OpenAI charges for completed
work.

## Limits

OpenAI documents these Batch limits:

- one batch can contain up to 50,000 requests;
- the input file can be up to 200 MB;
- `/v1/embeddings` batches can contain up to 50,000 embedding inputs total;
- Batch API rate limits are separate from synchronous rate limits;
- each model also has an enqueued-token Batch limit visible in the OpenAI
  Platform settings.

Our current corpus dry run produced 3,616 chunks, so it fits comfortably in one
embedding batch.

## Cost

For `text-embedding-3-small`, the model page lists a cost of `$0.02` per
1 million tokens. The OpenAI Batch guide and pricing page say Batch processing
saves 50%, so the expected Batch rate is approximately `$0.01` per 1 million
tokens for this embedding job.

Cost formula:

```text
embedding_cost = input_tokens / 1,000,000 * batch_price_per_1m_tokens
```

Example estimates:

```text
1,000,000 tokens  * $0.01 / 1M = $0.01
5,000,000 tokens  * $0.01 / 1M = $0.05
10,000,000 tokens * $0.01 / 1M = $0.10
```

The actual daily rebuild cost depends on the token count of the current docs and
course material. The completed Batch object includes usage data, but the current
CLI does not print it. Check the OpenAI dashboard for the authoritative billed
usage.

## Local Implementation

The implementation lives in:

- `src/faq_assistant/openai.py`
- `src/faq_assistant/ingest.py`

Config:

```toml
[embeddings]
provider = "openai"
model = "text-embedding-3-small"
dimensions = 1536
use_dimensions_parameter = false
batch_enabled = true
batch_completion_window = "24h"
batch_poll_interval_seconds = 30
```

We intentionally do not pass the OpenAI `dimensions` parameter. The project uses
the native/default `text-embedding-3-small` size: 1536 dimensions. The Cloudflare
Vectorize index must therefore also be 1536-dimensional.

Run a dry run:

```bash
uv run --group ingest faq-assistant ingest --mode rebuild --dry-run
```

Run the full rebuild:

```bash
set -a
source /home/alexey/tmp/cloudflare-ai-agent/.env
source .env
set +a

uv run --group ingest faq-assistant ingest --mode rebuild
```

The rebuild writes a JSON embedding artifact before uploading vectors to
Cloudflare:

```text
artifacts/embeddings/openai-text-embedding-3-small-1536/<timestamp>.json
artifacts/embeddings/openai-text-embedding-3-small-1536/latest.json
```

The artifact is intentionally ignored by Git because it is large and contains
the full vector payload. It is useful if we later move from Vectorize to another
vector database.

To save an already completed OpenAI Batch output without recomputing
embeddings:

```bash
uv run --group ingest python scripts/save_batch_embeddings.py <batch_id>
```

To load a saved artifact into the configured Vectorize index:

```bash
uv run --group ingest python scripts/load_embedding_artifact.py \
  artifacts/embeddings/openai-text-embedding-3-small-1536/latest.json
```

## Operational Notes

The current CLI submits a batch and waits for completion in the same process.
That is simple and works locally, but a daily CI rebuild can run for a while.
If this becomes inconvenient, split the job into two commands:

- submit a batch and store the `batch_id`;
- poll a previous `batch_id`, download results, and upsert when complete.

That split would make GitHub Actions more resilient because the submit job could
finish quickly and a later scheduled job could do the polling/upsert phase.
