#!/usr/bin/env python3
"""Enrich backfilled slack-answer traces: rewritten query + retrieved docs.

Backfilled traces have real Q&A but no rewritten_query (the bot reply doesn't
include it) and no structured sources. This script regenerates both:
  - rewritten_query: runs the REAL rewrite prompt (REWRITE_SYSTEM_PROMPT)
    through gpt-4o-mini, i.e. what the system would have generated
  - retrieved_documents: parses the sources the bot actually cited
    (Slack <url|title> links in the answer footer)

Cost is unknowable historically, so we estimate it deterministically from the
answer length + typical prompt sizes at current model prices (metadata says
estimated); live traces carry measured cost_usd. Updates traces in place via
PATCH (IDs preserved).

Usage:
  source .env  # OPIK_API_KEY + OPENAI_API_KEY
  uv run python scripts/enrich_slack_traces.py --dry-run
  OPIK_URL_OVERRIDE=https://www.comet.com/opik/api OPIK_WORKSPACE=default \\
    OPIK_PROJECT_NAME=faq-assistant-lambda \\
    uv run python scripts/enrich_slack_traces.py --limit 20
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("OPIK_URL_OVERRIDE", "http://localhost:5173/api")
os.environ.setdefault("OPIK_WORKSPACE", "default")
os.environ.setdefault("OPIK_PROJECT_NAME", "faq-assistant-lambda")

from faq_assistant.answering import REWRITE_SYSTEM_PROMPT  # noqa: E402

REWRITE_MODEL = "gpt-4o-mini"
MRKDWN_LINK = re.compile(r"<(https?://[^|>]+)\|([^>]+)>")
MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def api(method, path, payload=None):
    base = os.environ.get("OPIK_URL_OVERRIDE", "http://localhost:5173/api").rstrip("/")
    headers = {"content-type": "application/json",
               "Comet-Workspace": os.environ.get("OPIK_WORKSPACE", "default")}
    if os.environ.get("OPIK_API_KEY"):
        headers["authorization"] = os.environ["OPIK_API_KEY"]
    req = urllib.request.Request(base + path,
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


def list_traces(project, size=100):
    d = api("GET", f"/v1/private/traces?project_name={project}&page=1&size={size}")
    return d.get("content", [])


def rewrite_query(question, scope, course):
    """What the system would have generated: real prompt, cheap model."""
    from faq_assistant.models import QueryRewrite  # noqa: E402
    from faq_assistant.structured import parse_structured_response  # noqa: E402

    token = os.environ.get("OPENAI_API_KEY")
    if not token:
        raise RuntimeError("OPENAI_API_KEY not set")
    schema = QueryRewrite.model_json_schema()
    payload = {
        "model": REWRITE_MODEL,
        "messages": [
            {"role": "system",
             "content": REWRITE_SYSTEM_PROMPT + " Return structured JSON matching the requested schema."},
            {"role": "user", "content": f"scope: {scope}\ncourse: {course}\nmessage: {question}"},
        ],
        "temperature": 0.0,
        "max_completion_tokens": 120,
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "QueryRewrite", "strict": True,
                                            "schema": schema}},
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode(), method="POST",
        headers={"content-type": "application/json",
                 "authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode())
    return QueryRewrite.model_validate(
        parse_structured_response(data)).query.strip() or question


# Current prices (same table as config observability.prices, $/1M tokens).
PRICES = {"rewrite": (0.15, 0.6), "answer": (0.75, 4.5)}


def estimate_usage(question, answer):
    """Deterministic plausible cost from answer length + typical prompt sizes."""
    rw_p = 250 + len(question) // 4
    rw_c = 25
    a_p = 2600 + len(question) // 4
    a_c = max(20, len(answer) // 4)
    prompt, completion = rw_p + a_p, rw_c + a_c
    cost = (rw_p * PRICES["rewrite"][0] + rw_c * PRICES["rewrite"][1]
            + a_p * PRICES["answer"][0] + a_c * PRICES["answer"][1]) / 1_000_000
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": prompt + completion, "cost_usd": round(cost, 6),
            "estimated": True}


def cited_sources(answer):
    """Sources the bot actually cited (Slack <url|title> footer links)."""
    seen, out = set(), []
    for url, title in MRKDWN_LINK.findall(answer or ""):
        if url not in seen:
            seen.add(url)
            out.append({"title": title.strip(), "url": url})
    for title, url in MD_LINK.findall(answer or ""):
        if url not in seen:
            seen.add(url)
            out.append({"title": title.strip(), "url": url})
    return out


def main():
    parser = argparse.ArgumentParser(description="Enrich slack-answer traces")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--project", default=os.environ.get(
        "OPIK_PROJECT_NAME", "faq-assistant-lambda"))
    args = parser.parse_args()

    traces = [t for t in list_traces(args.project)
              if t.get("name") == "slack-answer"][:args.limit]
    print(f"{len(traces)} slack-answer traces")
    patched = 0
    for t in traces:
        inp, out = dict(t.get("input") or {}), dict(t.get("output") or {})
        if inp.get("rewritten_query") and out.get("usage"):
            print(f"{t['id'][:8]}: already enriched, skip")
            continue
        question, scope, course = (inp.get("question", ""), inp.get("scope", ""),
                                   inp.get("course") or "")
        if args.dry_run:
            print(f"{t['id'][:8]}: would enrich Q={question[:60]!r}")
            continue
        rw = rewrite_query(question, scope, course)
        docs = cited_sources(out.get("answer", ""))
        inp["rewritten_query"] = rw
        out["retrieved_documents"] = docs
        if "usage" not in out:
            out["usage"] = estimate_usage(question, out.get("answer", ""))
        meta = dict(t.get("metadata") or {})
        meta.update({"enriched": True, "rewrite_model": REWRITE_MODEL,
                     "cost_basis": "estimated"})
        api("PATCH", f"/v1/private/traces/{t['id']}",
            {"project_name": args.project, "input": inp, "output": out,
             "metadata": meta})
        patched += 1
        print(f"{t['id'][:8]}: rewritten={rw[:70]!r} docs={len(docs)}")
    print(f"patched: {patched}")


if __name__ == "__main__":
    main()
