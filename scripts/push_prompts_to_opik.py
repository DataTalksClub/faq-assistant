#!/usr/bin/env python3
"""Version assistant prompts in the Opik Prompt Library (without loading them).

Single source of truth stays in code (answering.py + generated config) — this
script only mirrors them into Opik so versions sit next to traces/experiments.
Re-running with unchanged templates creates nothing (Opik versions on diff).

Respects OPIK_URL_OVERRIDE / OPIK_WORKSPACE / OPIK_PROJECT_NAME / OPIK_API_KEY.

Usage:
  source .env  # OPIK_API_KEY for Cloud
  # Cloud:
  OPIK_URL_OVERRIDE=https://www.comet.com/opik/api OPIK_WORKSPACE=default \\
    OPIK_PROJECT_NAME=faq-assistant-lambda \\
    uv run --with opik python scripts/push_prompts_to_opik.py
  # Local (http://localhost:5173):
  OPIK_PROJECT_NAME=faq-assistant uv run --with opik python scripts/push_prompts_to_opik.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("OPIK_URL_OVERRIDE", "http://localhost:5173/api")
os.environ.setdefault("OPIK_WORKSPACE", "default")
os.environ.setdefault("OPIK_PROJECT_NAME", "faq-assistant-lambda")


def main():
    import opik  # noqa: E402  (test/dev only; Lambda never runs this script)
    from faq_assistant.answering import REWRITE_SYSTEM_PROMPT  # noqa: E402
    from faq_assistant.generated_config import CONFIG  # noqa: E402

    prompts = CONFIG["answering"]["prompts"]
    items = [
        ("faq-assistant-rewrite",
         "Keyword query rewrite: Slack message -> search query.",
         REWRITE_SYSTEM_PROMPT),
        ("faq-assistant-answer-course",
         "RAG answer instructions for course channels.",
         prompts["course"].strip()),
        ("faq-assistant-answer-docs",
         "RAG answer instructions for general docs channels.",
         prompts["docs"].strip()),
    ]
    project = os.environ.get("OPIK_PROJECT_NAME", "faq-assistant-lambda")
    client = opik.Opik(project_name=project)
    for name, description, template in items:
        prompt = client.create_prompt(
            name=name, prompt=template, description=description,
            metadata={"source": "faq-assistant",
                      "model": CONFIG["chat"]["model"],
                      "rewrite_model": CONFIG["chat"].get("rewrite_model", "")},
            tags=["faq-assistant", "rag"],
            project_name=project,
        )
        print(f"{name}: commit {prompt.commit} ({project})")


if __name__ == "__main__":
    main()
