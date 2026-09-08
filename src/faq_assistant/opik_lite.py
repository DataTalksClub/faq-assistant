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
import functools
import inspect
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


def send_trace(name: str, input: dict, output: dict, metadata: dict | None = None,
               project: str | None = None, start: str | None = None,
               end: str | None = None) -> None:
    """POST one trace. Never raises."""
    if not enabled():
        return
    try:
        payload = {
            "traces": [
                {
                    "id": _new_id(),
                    "project_name": project or _project(),
                    "name": name,
                    "start_time": start or _now(),
                    "end_time": end or _now(),
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


def _safe(value, limit: int = 4000):
    """JSON-safe snapshot; never raises, truncates big values."""
    try:
        text = json.dumps(value, default=str)
    except Exception:
        return str(value)[:limit]
    if len(text) > 8000:
        return {"_truncated": text[:8000]}
    try:
        return json.loads(text)
    except Exception:
        return text[:limit]


def _input(func, args, kwargs) -> dict:
    """Minimal input: only small named params, never config/index/chat."""
    try:
        bound = inspect.signature(func).bind_partial(*args, **kwargs)
        values = bound.arguments
    except Exception:
        return {}
    picked = {k: _safe(values[k]) for k in ("question", "scope", "course", "source") if k in values}
    return picked


def _output(result) -> dict:
    if isinstance(result, dict):
        return {k: _safe(result[k]) for k in (
            "answer", "found_answer", "rewritten_query", "usage") if k in result}
    return {"result": _safe(result)}


def track(func=None, project_name: str | None = None, **_):
    """Drop-in for `opik.track`: same `@track` annotation, stdlib only."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not enabled():
                return fn(*args, **kwargs)
            start = _now()
            try:
                result = fn(*args, **kwargs)
            except Exception as error:
                send_trace(fn.__name__,
                           _input(fn, args, kwargs),
                           {"error": f"{type(error).__name__}: {error}"},
                           project=project_name, start=start)
                raise
            send_trace(fn.__name__,
                       _input(fn, args, kwargs),
                       _output(result),
                       project=project_name, start=start)
            return result
        return wrapper
    return decorator(func) if callable(func) else decorator
