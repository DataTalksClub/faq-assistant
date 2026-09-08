"""One trace to local Opik, zero dependencies. UI: http://localhost:5173.

  OPIK_ENABLED=true OPIK_URL_OVERRIDE=http://localhost:5173/api \\
    OPIK_PROJECT_NAME=faq-assistant uv run python scripts/trace_demo_opik.py
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("OPIK_ENABLED", "true")
os.environ.setdefault("OPIK_URL_OVERRIDE", "http://localhost:5173/api")
os.environ.setdefault("OPIK_PROJECT_NAME", "faq-assistant")

from faq_assistant.answering import answer_question  # noqa: E402
from faq_assistant.generated_config import CONFIG  # noqa: E402
from faq_assistant.models import QueryRewrite  # noqa: E402


def chat(messages, output_model, *a, **k):
    content = (
        {"query": "docker compose"}
        if output_model is QueryRewrite
        else {"answer": "Run `docker compose up`.", "found_answer": True, "source_ids": []}
    )
    return {"choices": [{"message": {"content": json.dumps(content)}}]}


class Index:
    def search(self, *a, **k):
        return [{"id": "doc-1", "score": 1.0, "source_type": "docs", "course": "",
                 "section": "Env", "title": "Docker", "text": "Run docker compose up.",
                 "url": "https://datatalks.club/docs/"}]


if __name__ == "__main__":
    r = answer_question(CONFIG, Index(), chat, "how do I start docker compose", "docs", None)
    print("answer:", r["answer"])
    print("view at http://localhost:5173, project faq-assistant")
