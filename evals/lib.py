"""Shared helpers for the retrieval evals.

Ground truth is built from *real* Slack questions asked in the DataTalksClub
course channels (vendored under ../faq), judged for relevance against the same
search corpus production uses (``artifacts/search/search-corpus.json``, built by
``make corpus``). Retrieval definitions are imported from ``faq_assistant`` so
the eval exercises the production code path, not a copy of it.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Reuse the production retrieval definitions so the eval can't silently drift.
from faq_assistant import answering  # noqa: E402
from faq_assistant.generated_config import CONFIG  # noqa: E402
from faq_assistant.search_index import KEYWORD_FIELDS, TEXT_FIELDS  # noqa: E402

FAQ_REPO = Path(os.environ.get("FAQ_REPO", ROOT.parent / "faq"))
DATA_DIR = Path(__file__).resolve().parent / "data"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
CORPUS_ARTIFACT = ROOT / "artifacts" / "search" / "search-corpus.json"

BOOSTS = dict(CONFIG["retrieval"].get("boosts", {}))

# Slack channel (file stem in ../faq/.tmp/slack) -> corpus course id.
CHANNEL_TO_COURSE = {
    "course-ai-dev-tools-zoomcamp": "ai-dev-tools-zoomcamp",
    "course-data-engineering": "data-engineering-zoomcamp",
    "course-stocks-analytics-zoomcamp": "stock-markets-analytics-zoomcamp",
    "course-llm-zoomcamp": "llm-zoomcamp",
    "course-ml-zoomcamp": "machine-learning-zoomcamp",
    "course-mlops-zoomcamp": "mlops-zoomcamp",
}

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


# --------------------------------------------------------------------------- #
# Corpus + index
# --------------------------------------------------------------------------- #
def load_corpus() -> list[dict[str, Any]]:
    """Load the production corpus artifact (built by ``make corpus``)."""
    if not CORPUS_ARTIFACT.exists():
        raise FileNotFoundError(
            f"corpus artifact not found: {CORPUS_ARTIFACT}. Run `make corpus` "
            "(uv run --group ingest python scripts/build_search_corpus.py) first."
        )
    return json.loads(CORPUS_ARTIFACT.read_text(encoding="utf-8"))


def build_index(records: list[dict[str, Any]], engine: str = "zerosearch"):
    if engine == "zerosearch":
        from zerosearch import Index
    elif engine == "minsearch":
        from minsearch import Index  # type: ignore
    else:
        raise ValueError(f"unknown engine {engine!r}")
    return Index(text_fields=TEXT_FIELDS, keyword_fields=KEYWORD_FIELDS).fit(records)


def search(index, query: str, course: str, num_results: int = 10) -> list[dict[str, Any]]:
    """Wide course-scoped retrieval used for *pooling* candidates in build_dataset.

    Recall-oriented (no min_score floor, caller picks num_results); for measuring
    production behaviour use :func:`prod_search` instead.
    """
    return index.search(
        query,
        filter_dict={"scope": "course", "course": course},
        boost_dict=BOOSTS,
        num_results=num_results,
    )


def prod_search(index, query: str, course: str) -> list[dict[str, Any]]:
    """Exactly the production retrieval path: prod scope filter, boosts, min_score
    and result limit from config. Returns plain dicts with at least ``id``."""
    results = answering.search(CONFIG, index, query, "course", course)
    return [
        {"id": r.id, "score": r.score, "source_type": r.source_type, "title": r.title, "url": r.url}
        for r in results
    ]


# --------------------------------------------------------------------------- #
# Slack questions
# --------------------------------------------------------------------------- #
STAFF_MARKERS = ("datatalksclub", "alexey grigorev", "valeriia kuka")
_URL_RE = re.compile(r"https?://\S+")


def looks_like_question(text: str) -> bool:
    t = text.strip()
    if "?" not in t:
        return False
    n = len(t)
    if n < 25 or n > 400:
        return False
    # Drop announcement-ish / list posts and link dumps.
    if t.startswith(("*", "#", ">", "-", "•")):
        return False
    if len(_URL_RE.findall(t)) >= 2:
        return False
    lowered = t.lower()
    if any(p in lowered for p in ("most popular questions", "summarized", "@here", "@channel")):
        return False
    return True


def clean_question(text: str) -> str:
    text = _URL_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def load_slack_questions(channels: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """Return answered, question-shaped Slack threads as eval query candidates."""
    slack_dir = FAQ_REPO / ".tmp" / "slack"
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(slack_dir.glob("*.threads.jsonl")):
        channel = path.name[: -len(".threads.jsonl")]
        course = CHANNEL_TO_COURSE.get(channel)
        if course is None or (channels and channel not in channels):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            text = str(rec.get("question", ""))
            if int(rec.get("n_replies", 0)) < 1:  # needs an answer to be answerable
                continue
            if any(m in str(rec.get("user", "")).lower() for m in STAFF_MARKERS):
                continue
            if not looks_like_question(text):
                continue
            q = clean_question(text)
            key = q.lower()[:120]
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "query": q,
                    "course": course,
                    "channel": channel,
                    "thread_ts": rec.get("thread_ts"),
                    "slack_answer": clean_question(str(rec.get("answer", "")))[:600],
                }
            )
    return out


# --------------------------------------------------------------------------- #
# OpenAI (stdlib only, to keep parity with the dependency-free spirit)
# --------------------------------------------------------------------------- #
def chat(model: str, messages: list[dict], *, max_tokens: int = 400, temperature: float = 0.0,
         json_object: bool = False) -> str:
    key = os.environ["OPENAI_API_KEY"]
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": max_tokens,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if json_object:
        payload["response_format"] = {"type": "json_object"}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OPENAI_URL, data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.load(resp)
            return data["choices"][0]["message"]["content"] or ""
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < 3:
                continue
            raise RuntimeError(f"OpenAI HTTP {e.code}: {e.read().decode()[:300]}") from e
    raise RuntimeError("unreachable")


def chat_json(model: str, messages: list[dict], *, max_tokens: int = 400) -> Any:
    text = chat(model, messages, max_tokens=max_tokens, json_object=True)
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text).strip()
    return json.loads(text)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
