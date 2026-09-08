"""Tiny Opik exporter, stdlib only. No `opik` package needed.

Sends one trace per call via POST {base}/v1/private/traces/batch.
Best-effort: never raises. Disabled unless OPIK_ENABLED=true.

Env:
  OPIK_ENABLED=true
  OPIK_URL_OVERRIDE=http://localhost:5173/api (default)
  OPIK_PROJECT_NAME=faq-assistant (default)
  OPIK_API_KEY=... (optional, for cloud; local needs none)
"""

from __future__ import annotations

import datetime
import json
import os
import random
import time
import urllib.request
import uuid


def enabled() -> bool:
    return os.environ.get("OPIK_ENABLED", "false").lower() in ("1", "true", "yes", "on")


def _base_url() -> str:
    return os.environ.get("OPIK_URL_OVERRIDE", "http://localhost:5173/api").rstrip("/")


def _project() -> str:
    return os.environ.get("OPIK_PROJECT_NAME", "faq-assistant")


def _new_id() -> str:
    try:
        return str(uuid.uuid7())  # type: ignore[attr-defined]  # py3.14+
    except AttributeError:
        ms = time.time_ns() // 1_000_000
        v = (ms << 80) | (7 << 76) | (random.getrandbits(12) << 64) | (0b10 << 62) | random.getrandbits(62)
        return str(uuid.UUID(int=v))


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def send_trace(name: str, input: dict, output: dict, metadata: dict | None = None) -> None:
    """POST one trace. Never raises."""
    if not enabled():
        return
    try:
        now = _now()
        payload = {
            "traces": [
                {
                    "id": _new_id(),
                    "project_name": _project(),
                    "name": name,
                    "start_time": now,
                    "end_time": now,
                    "input": input,
                    "output": output,
                    "metadata": metadata or {},
                }
            ]
        }
        headers = {"content-type": "application/json"}
        if os.environ.get("OPIK_API_KEY"):
            headers["authorization"] = os.environ["OPIK_API_KEY"]
        req = urllib.request.Request(
            _base_url() + "/v1/private/traces/batch",
            data=json.dumps(payload).encode(),
            method="POST",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=2):
            pass
    except Exception:
        pass
