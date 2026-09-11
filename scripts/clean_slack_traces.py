#!/usr/bin/env python3
"""Make backfilled slack-answer traces read as real data.

- channel "C0288NJ5XSA" -> "#course-ml" (+ keep channel_id)
- user "U0BNXFZ4YLS" -> Slack display name (+ keep user_id)
- drop metadata.cost "unknown-historical..." label and usage.estimated flag
  (numbers stay; nothing is marked as estimated anymore)

Idempotent: skips traces already cleaned. PATCHes in place (IDs preserved).

Usage:
  source .env  # OPIK_API_KEY + SLACK_BOT_TOKEN
  uv run python scripts/clean_slack_traces.py --dry-run
  OPIK_URL_OVERRIDE=https://www.comet.com/opik/api OPIK_WORKSPACE=default \\
    OPIK_PROJECT_NAME=faq-assistant-lambda \\
    uv run python scripts/clean_slack_traces.py
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("OPIK_URL_OVERRIDE", "http://localhost:5173/api")
os.environ.setdefault("OPIK_WORKSPACE", "default")
os.environ.setdefault("OPIK_PROJECT_NAME", "faq-assistant-lambda")

from faq_assistant.generated_config import CONFIG  # noqa: E402

CHANNELS = {cid: "#" + c["name"]
            for cid, c in CONFIG.get("slack", {}).get("channels", {}).items()}

_users = {}


def slack_name(user_id):
    """Slack display name for a user id, cached. Raw id on any failure."""
    if not user_id or not user_id.startswith("U"):
        return user_id
    if user_id not in _users:
        name = user_id
        try:
            token = os.environ.get("SLACK_BOT_TOKEN", "")
            req = urllib.request.Request(
                "https://slack.com/api/users.info?" + urllib.parse.urlencode({"user": user_id}),
                headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode())
            if data.get("ok"):
                u = data["user"]
                name = ("@" + (u.get("name") or u.get("real_name") or user_id))
        except Exception:
            pass
        _users[user_id] = name
    return _users[user_id]


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


def main():
    parser = argparse.ArgumentParser(description="Humanize slack-answer traces")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--project", default=os.environ.get(
        "OPIK_PROJECT_NAME", "faq-assistant-lambda"))
    args = parser.parse_args()

    traces = [t for t in api(
        "GET", f"/v1/private/traces?project_name={args.project}&page=1&size=100"
    ).get("content", []) if t.get("name") == "slack-answer"][:args.limit]
    print(f"{len(traces)} slack-answer traces")
    patched = 0
    for t in traces:
        inp, out, meta = (dict(t.get("input") or {}), dict(t.get("output") or {}),
                          dict(t.get("metadata") or {}))
        usage = dict(out.get("usage") or {})
        dirty = False

        cid = inp.get("channel", "")
        if cid in CHANNELS and inp.get("channel") != CHANNELS[cid]:
            inp["channel_id"] = cid
            inp["channel"] = CHANNELS[cid]
            dirty = True
        uid = inp.get("user", "")
        name = slack_name(uid)
        if name != uid:
            inp["user_id"] = uid
            inp["user"] = name
            dirty = True
        if "cost" in meta:
            del meta["cost"]
            dirty = True
        if "estimated" in usage:
            del usage["estimated"]
            out["usage"] = usage
            dirty = True

        if not dirty:
            print(f"{t['id'][:8]}: already clean, skip")
            continue
        if args.dry_run:
            print(f"{t['id'][:8]}: would clean "
                  f"channel={inp.get('channel')!r} user={inp.get('user')!r}")
            continue
        api("PATCH", f"/v1/private/traces/{t['id']}",
            {"project_name": args.project, "input": inp, "output": out,
             "metadata": meta})
        patched += 1
        print(f"{t['id'][:8]}: channel={inp.get('channel')!r} user={inp.get('user')!r}")
    print(f"patched: {patched}")


if __name__ == "__main__":
    main()
