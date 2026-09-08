"""Send one faq-assistant pipeline trace to local Opik (no OpenAI key needed).

Local Opik must be running (../opik/./opik.sh, UI at http://localhost:5173):

  export OPIK_URL_OVERRIDE=http://localhost:5173/api
  export OPIK_WORKSPACE=default
  export OPIK_PROJECT_NAME=faq-assistant

Run:
  uv run --group test python scripts/trace_demo_opik.py
  uv run --group test python scripts/trace_demo_opik.py --question "how do I start docker compose"

The demo uses a stubbed chat + stubbed index (same as scripts/check_handler.py),
so it costs nothing and still shows the full span hierarchy in Opik:
  answer_question -> rewrite_query -> retrieve -> generate_answer
View at http://localhost:5173, project faq-assistant.
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("OPIK_URL_OVERRIDE", "http://localhost:5173/api")
os.environ.setdefault("OPIK_WORKSPACE", "default")
os.environ.setdefault("OPIK_PROJECT_NAME", "faq-assistant")

from faq_assistant.answering import answer_question  # noqa: E402
from faq_assistant.generated_config import CONFIG  # noqa: E402
from faq_assistant.models import QueryRewrite  # noqa: E402
from faq_assistant.opik_tracing import flush  # noqa: E402


def stub_chat(messages, output_model, max_tokens, temperature, model=None):
    if output_model is QueryRewrite:
        content = {"query": "docker compose start services"}
    else:
        content = {
            "answer": "Run `docker compose up` to start the services.",
            "found_answer": True,
            "source_ids": ["doc-1"],
        }
    return {
        "choices": [{"message": {"content": json.dumps(content)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


class StubIndex:
    def search(self, query, filter_dict=None, boost_dict=None, num_results=6):
        return [
            {
                "id": "doc-1",
                "score": 1.0,
                "source_type": "docs",
                "course": "",
                "section": "Environment",
                "title": "Docker Compose",
                "text": "Run docker compose up to start the services.",
                "url": "https://datatalks.club/docs/docker.html",
            }
        ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--question", default="how do I start docker compose")
    parser.add_argument("--scope", default="docs")
    args = parser.parse_args()

    result = answer_question(
        CONFIG, StubIndex(), stub_chat, args.question, args.scope, None, source="opik-demo"
    )
    flush()
    print(f"trace sent: question={result['question']!r}")
    print(f"rewritten_query={result['rewritten_query']!r}")
    print(f"answer={result['answer']!r}")
    print("view at http://localhost:5173, project faq-assistant")
    return 0


if __name__ == "__main__":
    sys.exit(main())
