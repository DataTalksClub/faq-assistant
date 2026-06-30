#!/usr/bin/env python3
"""Review how the automator FAQ bot did in the DataTalks.Club Slack.

Scans the course channels configured in ``config.toml`` and reports, for threads
where the bot was triggered (an @mention of the bot, or a ``faq`` reaction):

  no-answer   - the bot replied that it couldn't find an answer
  corrected   - the bot answered, then Alexey Grigorev replied afterwards
                (i.e. he had to correct it or add more info)

Both are signals for FAQ/retrieval gaps worth feeding back into the corpus and
the eval set.

Usage (stdlib only — no project deps, so --no-project keeps it fast):
    # token is read from .env (SLACK_BOT_TOKEN), same as the rest of the repo
    uv run --no-project python scripts/slack_faq_review.py --days 30
    uv run --no-project python scripts/slack_faq_review.py --days 60 --mode no-answer
    uv run --no-project python scripts/slack_faq_review.py --channel C06TEGTGM3J --json out.json

Channels: every [slack.channels.*] entry in config.toml is scanned by default
(the six course channels). Use --channel <id> to limit to one.

Needs a bot token with channels:history (+ groups:history for private channels)
and reactions:read. The token here belongs to the @automator bot itself, so its
own replies are visible without extra scopes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SLACK_API = "https://slack.com/api"
ROOT = Path(__file__).resolve().parents[1]

# The automator's "I couldn't find it" fallbacks (see answering.generate fallback).
NO_ANSWER_MARKERS = (
    "I couldn't find this in the course materials",
    "I couldn't find this in the docs",
)
# Alexey Grigorev (resolved via users.lookupByEmail alexey.s.grigoriev@gmail.com).
ALEXEY_UID = "U01AXE0P5M3"


# --------------------------------------------------------------------------- #
# Config + token
# --------------------------------------------------------------------------- #
def load_token() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("SLACK_BOT_TOKEN="):
            return line.split("=", 1)[1].strip().strip("'\"")
    sys.exit("SLACK_BOT_TOKEN not found in .env")


def load_channels(config_path: Path) -> dict[str, str]:
    """channel_id -> human name, from [slack.channels.*] in config.toml."""
    with config_path.open("rb") as f:
        config = tomllib.load(f)
    channels = config.get("slack", {}).get("channels", {})
    return {cid: meta.get("name", cid) for cid, meta in channels.items()}


# --------------------------------------------------------------------------- #
# Slack API (stdlib only, with rate-limit backoff)
# --------------------------------------------------------------------------- #
class Slack:
    def __init__(self, token: str):
        self.token = token
        self._names: dict[str, str] = {}

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

    def whoami(self) -> str:
        return self.call("auth.test")["user_id"]

    def history(self, channel: str, oldest: float) -> list[dict]:
        out, cursor = [], ""
        while True:
            p = self.call("conversations.history", channel=channel, oldest=oldest,
                          limit=200, cursor=cursor)
            out.extend(p.get("messages", []))
            cursor = p.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                return out

    def replies(self, channel: str, ts: str) -> list[dict]:
        return self.call("conversations.replies", channel=channel, ts=ts, limit=200).get("messages", [])

    def permalink(self, channel: str, ts: str) -> str | None:
        try:
            return self.call("chat.getPermalink", channel=channel, message_ts=ts).get("permalink")
        except RuntimeError:
            return None

    def name(self, uid: str | None) -> str:
        """Resolve a user id to a display name, cached (few unique ids per run)."""
        if not uid:
            return "unknown"
        if uid not in self._names:
            try:
                prof = self.call("users.info", user=uid)["user"]
                self._names[uid] = prof.get("real_name") or prof.get("name") or uid
            except RuntimeError:
                self._names[uid] = uid
        return self._names[uid]


# --------------------------------------------------------------------------- #
# Scan
# --------------------------------------------------------------------------- #
@dataclass
class Finding:
    kind: str            # "no-answer" | "corrected"
    channel: str
    when: str
    trigger: list[str]
    question_uid: str
    question: str
    bot_answer: str
    followups: list[dict] = field(default_factory=list)
    link: str | None = None


def clip(text: str, n: int = 320) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def iso(ts: str) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def reactions_of(msg: dict) -> set[str]:
    return {r["name"] for r in msg.get("reactions", [])}


def triggers(thread: list[dict], bot_uid: str) -> list[str]:
    found = []
    if thread and f"<@{bot_uid}>" in thread[0].get("text", ""):
        found.append("@mention")
    if any("faq" in reactions_of(m) for m in thread):
        found.append("faq-reaction")
    return found


def scan_channel(api: Slack, channel_id: str, channel_name: str, bot_uid: str,
                 oldest: float, mode: str) -> list[Finding]:
    findings: list[Finding] = []
    roots = api.history(channel_id, oldest)
    for root in roots:
        # Only threads can contain a bot answer; the bot answers in-thread.
        if root.get("reply_count", 0) == 0 and root.get("user") != bot_uid:
            continue
        thread = api.replies(channel_id, root["ts"])
        if not thread:
            continue
        parent = thread[0]
        # Locate the bot's answer in this thread.
        bot_idx = next((i for i, m in enumerate(thread) if m.get("user") == bot_uid), None)
        if bot_idx is None:
            continue
        bot_msg = thread[bot_idx]
        bot_text = bot_msg.get("text", "")
        trig = triggers(thread, bot_uid)

        is_no_answer = any(mk in bot_text for mk in NO_ANSWER_MARKERS)
        # Human replies posted *after* the bot answered.
        after = [m for m in thread[bot_idx + 1:]
                 if m.get("user") and m.get("user") != bot_uid]
        alexey_after = [m for m in after if m.get("user") == ALEXEY_UID]

        if mode in ("all", "no-answer") and is_no_answer:
            findings.append(Finding(
                kind="no-answer", channel=channel_name, when=iso(bot_msg["ts"]),
                trigger=trig or ["(bot replied)"], question_uid=parent.get("user", "?"),
                question=clip(parent.get("text", "")), bot_answer=clip(bot_text, 200),
                link=api.permalink(channel_id, parent["ts"]),
            ))
        if mode in ("all", "corrected") and alexey_after and not is_no_answer:
            findings.append(Finding(
                kind="corrected", channel=channel_name, when=iso(bot_msg["ts"]),
                trigger=trig or ["(bot replied)"], question_uid=parent.get("user", "?"),
                question=clip(parent.get("text", "")), bot_answer=clip(bot_text),
                followups=[{"user": m.get("user"), "text": clip(m.get("text", ""))}
                           for m in alexey_after],
                link=api.permalink(channel_id, parent["ts"]),
            ))
    return findings


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def render(api: Slack, findings: list[Finding]) -> None:
    if not findings:
        print("No matching threads found in the window.")
        return
    by_kind: dict[str, list[Finding]] = {}
    for f in findings:
        by_kind.setdefault(f.kind, []).append(f)
    for kind, items in by_kind.items():
        print(f"\n{'=' * 70}\n{kind.upper()}  ({len(items)})\n{'=' * 70}")
        for f in items:
            print(f"\n[{f.channel}] {f.when}  trigger={', '.join(f.trigger)}")
            print(f"  Q ({api.name(f.question_uid)}): {f.question}")
            print(f"  bot: {f.bot_answer}")
            for fu in f.followups:
                print(f"  -> {api.name(fu['user'])}: {fu['text']}")
            if f.link:
                print(f"  {f.link}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30, help="how far back to scan")
    ap.add_argument("--mode", choices=["all", "no-answer", "corrected"], default="all")
    ap.add_argument("--channel", default=None, help="limit to one channel id")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--json", default=None, help="also write findings as JSON here")
    args = ap.parse_args()

    api = Slack(load_token())
    bot_uid = api.whoami()
    channels = load_channels(Path(args.config))
    if args.channel:
        channels = {args.channel: channels.get(args.channel, args.channel)}
    oldest = time.time() - args.days * 86400

    all_findings: list[Finding] = []
    for cid, cname in channels.items():
        found = scan_channel(api, cid, cname, bot_uid, oldest, args.mode)
        all_findings.extend(found)
        print(f"[{cname}] {len(found)} finding(s)", file=sys.stderr)

    all_findings.sort(key=lambda f: (f.kind, f.when))
    render(api, all_findings)

    if args.json:
        rows = [dict(vars(f), question_user=api.name(f.question_uid)) for f in all_findings]
        Path(args.json).write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nwrote {len(rows)} finding(s) -> {args.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
