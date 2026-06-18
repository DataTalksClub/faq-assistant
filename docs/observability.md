# Observability: usage, cost & logs

The Worker captures **structured per-request usage and cost** for every answer it
produces (both the `/ask` API and Slack mentions), so you can see how much you
spend per request / week / month and run analytics later.

Three layers, all enabled in this repo:

1. **The `/ask` response** includes a `usage` object for that single request:
   ```json
   "usage": {"prompt_tokens": 2810, "completion_tokens": 156,
             "total_tokens": 2966, "cost_usd": 0.002699}
   ```
2. **Workers Logs** — a structured JSON line is logged per request (visible in the
   dashboard *Workers › Logs* and `npx wrangler tail`):
   ```json
   {"type":"usage","source":"api","scope":"docs","course":"",
    "models":"gpt-4o-mini,gpt-5.4-mini","calls":2,"num_results":6,
    "latency_ms":4248.0,"prompt_tokens":2810,"completion_tokens":156,
    "total_tokens":2966,"cost_usd":0.002699}
   ```
   Enabled via `"observability": { "enabled": true }` in `wrangler.jsonc`.
3. **Workers Analytics Engine** — one queryable data point per request in the
   `faq_usage` dataset (binding `USAGE`). This is the one to run SQL analytics on.
   *Requires the Workers Paid plan.*

## How cost is computed

Each OpenAI response returns a `usage` block (`prompt_tokens` /
`completion_tokens`). The Worker multiplies those by the per-model prices in
`config.toml` (`[observability.prices."<model>"]`, USD per 1M tokens) and sums the
rewrite + answer calls. **Raw token counts are stored too**, so if a price is
wrong you can always recompute cost from tokens in SQL.

Update prices in `config.toml` when OpenAI changes them, then `make config` and
redeploy. Current values:

| model         | input $/1M | output $/1M |
|---------------|-----------:|------------:|
| gpt-4o-mini   | 0.15       | 0.60        |
| gpt-5.4-mini  | 0.75       | 4.50        |

## Analytics Engine schema (`faq_usage`)

| column   | meaning                         |
|----------|---------------------------------|
| `index1` | course (or scope if no course)  |
| `blob1`  | source — `api` or `slack`       |
| `blob2`  | scope — `course` or `docs`      |
| `blob3`  | course id                       |
| `blob4`  | models used (comma-separated)   |
| `double1`| prompt tokens                   |
| `double2`| completion tokens               |
| `double3`| total tokens                    |
| `double4`| **cost (USD)**                  |
| `double5`| latency (ms)                    |
| `double6`| num retrieved results           |
| `double7`| num OpenAI calls                |
| `timestamp` | request time (automatic)     |

## Querying

Analytics Engine is queried with SQL over HTTP. Multiply by `_sample_interval`
for accurate totals (Analytics Engine samples at high volume; the weight is 1 at
low volume).

```bash
curl "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/analytics_engine/sql" \
  -H "Authorization: Bearer $CF_API_TOKEN" \
  -d "SELECT sum(double4 * _sample_interval) AS cost_usd
      FROM faq_usage
      WHERE timestamp >= now() - INTERVAL '7' DAY"
```

Useful queries:

```sql
-- Spend & request count per day (last 30 days)
SELECT toStartOfDay(timestamp) AS day,
       sum(double4 * _sample_interval) AS cost_usd,
       sum(_sample_interval)           AS requests
FROM faq_usage
WHERE timestamp >= now() - INTERVAL '30' DAY
GROUP BY day ORDER BY day;

-- Spend per week
SELECT toStartOfWeek(timestamp) AS week,
       sum(double4 * _sample_interval) AS cost_usd
FROM faq_usage GROUP BY week ORDER BY week;

-- Spend per month
SELECT toStartOfMonth(timestamp) AS month,
       sum(double4 * _sample_interval) AS cost_usd
FROM faq_usage GROUP BY month ORDER BY month;

-- Cost by course and by source
SELECT blob3 AS course, blob1 AS source,
       sum(double4 * _sample_interval) AS cost_usd,
       sum(_sample_interval)           AS requests
FROM faq_usage GROUP BY course, source ORDER BY cost_usd DESC;

-- Average cost & latency per request (last 7 days)
SELECT avg(double4) AS avg_cost_usd, avg(double5) AS avg_latency_ms
FROM faq_usage WHERE timestamp >= now() - INTERVAL '7' DAY;
```

You can also build these into a Grafana dashboard (Cloudflare Analytics Engine
has a Grafana data source) or export to your own store for deeper analysis.
