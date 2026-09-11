#!/usr/bin/env python3
"""Backfill real Slack Q&A history into Opik Cloud as traces.

Scans 1-2 active course channels via the Slack Web API (stdlib urllib only),
finds threads where the Au-Tomator bot answered, and logs one Opik trace per
Q&A pair via ``faq_assistant.opik_lite.send_trace`` (trace name
``slack-answer``, project ``faq-assistant-lambda``).

Bot replies are identified by ALL of: ``user == <bot_uid from auth.test>``,
a present ``bot_id``, ``bot_profile.name == "Au-Tomator"`` (app_id
``A01S395330A``), ``thread_ts`` equal to the parent ts, and ``type ==
message`` with subtype in (None/""/bot_message). Only replies carrying the
FAQ worker's sources footer ("Sources:" / "You can check these" /
couldn't-find fallback) count -- Au-Tomator moderation posts ("Don't ask to
ask", "Please use threads") are skipped. Threads whose parent is
itself from the bot uid (Telegram bridge forwards posted as Au-Tomator),
empty questions/answers, or edits/system subtypes are skipped as ambiguous.

Trace shape:
  input:    {question, scope, course, channel, user, thread_ts}
  output:   {answer (bot's actual reply text), found_answer}
  start:    question ts (ISO Z), end: bot reply ts (ISO Z)
  metadata: {backfilled: true, source: slack-history} (+ tags fallback, see below)
  tags:     [backfill, slack-history, <course>]

``opik_lite.send_trace`` currently has no first-class ``tags`` parameter, so
tags are passed through when supported and otherwise stored under
``metadata["tags"]`` (additive-only constraint: this script must not touch
``opik_lite.py``).

Retrieval regeneration is explicitly OUT of scope: the corpus is not
versioned, so historical retrieval context cannot be faithfully rebuilt;
only the original question and the bot's actual reply are logged.

Usage (stdlib only, --no-project keeps it fast):
    uv run --no-project python scripts/backfill_slack_history.py --dry-run --limit 5
    uv run --no-project python scripts/backfill_slack_history.py --limit 20 --days 60
    OPIK_ENABLED=true OPIK_URL_OVERRIDE=https://www.comet.com/opik/api \\
      OPIK_WORKSPACE=default OPIK_PROJECT_NAME=faq-assistant-lambda \\
      uv run --no-project python scripts/backfill_slack_history.py --limit 20 --days 60
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from faq_assistant.opik_lite import send_trace  # noqa: E402

SLACK_API = "https://slack.com/api"
PROJECT = "faq-assistant-lambda"
TRACE_NAME = "slack-answer"

EXPECTED_APP_ID = "A01S395330A"
EXPECTED_BOT_NAME = "Au-Tomator"

NO_ANSWER_SUBSTRINGS = (
    "couldn't find",
    "could not find",
    "please include a question",
)


def load_token() -> str:
    env_token = __import__("os").environ.get("SLACK_BOT_TOKEN", "").strip()
    if env_token:
        return env_token
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("SLACK_BOT_TOKEN="):
            return line.split("=", 1)[1].strip().strip("'\"")
    sys.exit("SLACK_BOT_TOKEN not found in env or .env")


def load_channel_map(config_path: Path) -> dict[str, dict[str, str]]:
    with config_path.open("rb") as f:
        config = tomllib.load(f)
    channels = config.get("slack", {}).get("channels", {})
    out: dict[str, dict[str, str]] = {}
    for cid, meta in channels.items():
        out[cid] = {
            "name": str(meta.get("name", cid)),
            "scope": str(meta.get("scope", "course")),
            "course": str(meta.get("course", "")),
        }
    return out


class Slack:
    def __init__(self, token: str):
        self.token = token

    def call(self, method: str, **params) -> dict:
        params = {k: v for k, v in params.items() if v != ""}
        url = f"{SLACK_API}/{method}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        for _ in range(8):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    payload = json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(int(e.headers.get("Retry-After", "3")))
                    continue
                raise
            if not payload.get("ok"):
                if payload.get("error") == "ratelimited":
                    time.sleep(2)
                    continue
                raise RuntimeError(f"{method}: {payload.get('error')}")
            return payload
        raise RuntimeError(f"{method}: too many retries")

    def whoami(self) -> tuple[str, str]:
        data = self.call("auth.test")
        return str(data["user_id"]), str(data.get("bot_id", ""))

    def history(self, channel: str, oldest: float) -> list[dict]:
        out, cursor = [], ""
        while True:
            p = self.call(
                "conversations.history",
                channel=channel,
                oldest=f"{oldest:.6f}",
                limit=200,
                cursor=cursor,
            )
            out.extend(p.get("messages", []))
            cursor = p.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                return out

    def replies(self, channel: str, ts: str) -> list[dict]:
        return self.call("conversations.replies", channel=channel, ts=ts, limit=200).get(
            "messages", []
        )


def iso_z(ts: str) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def is_bot_reply(msg: dict, bot_uid: str) -> bool:
    if msg.get("type", "message") != "message":
        return False
    if msg.get("user") != bot_uid:
        return False
    if not msg.get("bot_id"):
        return False
    profile = msg.get("bot_profile") or {}
    if profile and profile.get("name") not in (EXPECTED_BOT_NAME,):
        return False
    if profile and profile.get("app_id") not in ("", EXPECTED_APP_ID):
        return False
    subtype = msg.get("subtype")
    if subtype not in (None, "", "bot_message"):
        return False
    if not msg.get("thread_ts"):
        return False
    if not (msg.get("text") or "").strip():
        return False
    return True


def found_answer(text: str) -> bool:
    lowered = text.lower()
    return not any(marker in lowered for marker in NO_ANSWER_SUBSTRINGS)


def is_faq_answer(text: str) -> bool:
    """True only for worker FAQ answers; skips Au-Tomator moderation/automation.

    The FAQ worker always appends a sources footer ("*Sources:*" on answers,
    "*You can check these for more information:*" on fallbacks). Au-Tomator
    also posts short moderation replies ("Don't ask to ask", "Please use
    threads ...") with neither marker -- those are not RAG answers and must
    not become traces.
    """
    lowered = text.lower()
    return ("sources:" in lowered or "you can check these" in lowered
            or "couldn't find" in lowered or "could not find" in lowered)


def extract_qa(thread: list[dict], bot_uid: str) -> tuple[dict, dict] | None:
    """Return (parent, bot_msg) or None when ambiguous; first FAQ bot reply wins."""
    if not thread:
        return None
    parent = thread[0]
    if parent.get("user") == bot_uid:
        return None  # Telegram bridge forward posted as Au-Tomator; ambiguous
    if parent.get("subtype") not in (None, ""):
        return None
    question = (parent.get("text") or "").strip()
    if not question:
        return None
    for msg in thread[1:]:
        if msg.get("thread_ts") != parent.get("ts"):
            continue
        if is_bot_reply(msg, bot_uid) and is_faq_answer(msg.get("text") or ""):
            return parent, msg
    return None


def collect_pairs(
    api: Slack,
    channel_id: str,
    bot_uid: str,
    oldest: float,
    limit: int,
) -> list[tuple[dict, dict]]:
    pairs: list[tuple[dict, dict]] = []
    roots = api.history(channel_id, oldest)
    # Oldest first so a bounded --limit yields a stable time range.
    roots.sort(key=lambda m: float(m.get("ts", "0") or 0))
    for root in roots:
        if len(pairs) >= limit:
            break
        if root.get("reply_count", 0) == 0 and not root.get("thread_ts"):
            # Top-level message without replies cannot hold a bot answer;
            # still allow bot-authored roots to be skipped cheaply.
            continue
        if root.get("thread_ts") and root.get("thread_ts") != root.get("ts"):
            continue  # a reply, not a parent; parents surface via history once
        try:
            thread = api.replies(channel_id, root["ts"])
        except RuntimeError as e:
            print(f"warn: replies {channel_id} {root.get('ts')}: {e}", file=sys.stderr)
            continue
        qa = extract_qa(thread, bot_uid)
        if qa is None:
            continue
        pairs.append(qa)
    return pairs


def log_trace(
    question: str,
    answer: str,
    scope: str,
    course: str,
    channel_id: str,
    user: str,
    thread_ts: str,
    start: str,
    end: str,
    dry_run: bool,
) -> None:
    trace_input = {
        "question": question,
        "scope": scope,
        "course": course,
        "channel": channel_id,
        "user": user,
        "thread_ts": thread_ts,
    }
    trace_output = {"answer": answer, "found_answer": found_answer(answer)}
    tags = ["backfill", "slack-history", course]
    metadata = {"backfilled": True, "source": "slack-history"}
    if dry_run:
        print(f"DRY question={question[:100]!r} answer={answer[:100]!r}")
        return
    kwargs: dict = {"metadata": {**metadata, "tags": tags}, "project": PROJECT}
    try:
        if "tags" in inspect.signature(send_trace).parameters:
            kwargs["tags"] = tags
    except (TypeError, ValueError):
        pass
    send_trace(
        TRACE_NAME,
        trace_input,
        trace_output,
        start=start,
        end=end,
        **kwargs,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="scan and print, send nothing")
    ap.add_argument("--limit", type=int, default=20, help="max traces to log (total)")
    ap.add_argument("--channel", default=None, help="limit to one channel id")
    ap.add_argument("--days", type=int, default=60, help="how far back to scan")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    args = ap.parse_args()

    channel_map = load_channel_map(Path(args.config))
    if args.channel:
        if args.channel not in channel_map:
            sys.exit(f"unknown channel {args.channel}; known: {sorted(channel_map)}")
        channel_map = {args.channel: channel_map[args.channel]}

    api = Slack(load_token())
    bot_uid, _ = api.whoami()
    oldest = time.time() - args.days * 86400

    remaining = args.limit
    logged = 0
    first_start = ""
    last_end = ""
    for cid, meta in channel_map.items():
        if remaining <= 0:
            break
        pairs = collect_pairs(api, cid, bot_uid, oldest, remaining)
        print(f"[{meta['name']}] {len(pairs)} Q&A thread(s)", file=sys.stderr)
        for parent, bot_msg in pairs:
            question = (parent.get("text") or "").strip()
            answer = (bot_msg.get("text") or "").strip()
            start = iso_z(parent["ts"])
            end = iso_z(bot_msg["ts"])
            log_trace(
                question,
                answer,
                meta["scope"],
                meta["course"],
                cid,
                str(parent.get("user", "")),
                str(parent.get("ts", "")),
                start,
                end,
                args.dry_run,
            )
            print(f"{'DRY ' if args.dry_run else ''}logged {cid} {parent.get('ts')} -> "
                  f"{bot_msg.get('ts')} found_answer={found_answer(answer)}")
            if not first_start:
                first_start = start
            last_end = end
            logged += 1
            remaining -= 1
            if remaining <= 0:
                break
            time.sleep(0.2)  # stay well under Opik/Cloud rate limits

    print(f"\n{'would log' if args.dry_run else 'logged'} {logged} trace(s)"
          + (f" range {first_start} .. {last_end}" if logged else ""))


if __name__ == "__main__":
    main()
