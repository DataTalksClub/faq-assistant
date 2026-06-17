from __future__ import annotations

import json
import os
import sys

from faq_assistant.config import load_config
from faq_assistant.models import QueryRewrite, RagAnswer
from faq_assistant.openai import OpenAIClient
from faq_assistant.structured import parse_structured_response


def main() -> int:
    config = load_config()
    token_env = config["openai"]["api_key_env"]

    if not os.environ.get(token_env):
        print(f"skipped: set {token_env} to test the real OpenAI model")
        return 0

    client = OpenAIClient(config)
    model = config["chat"]["model"]

    rewrite_response = client.chat_structured(
        model,
        [
            {
                "role": "system",
                "content": "Rewrite the user's message into a concise search query.",
            },
            {
                "role": "user",
                "content": "Can I still join the course after it started?",
            },
        ],
        output_model=QueryRewrite,
        temperature=0,
        max_tokens=120,
    )
    rewrite = QueryRewrite.model_validate(parse_structured_response(rewrite_response))
    assert rewrite.query.strip(), rewrite_response
    print("query rewrite:", rewrite.model_dump())

    answer_response = client.chat_structured(
        model,
        [
            {
                "role": "system",
                "content": "Answer using only the provided context. Return structured JSON.",
            },
            {
                "role": "user",
                "content": (
                    "QUESTION: Can I still join?\n\n"
                    "CONTEXT:\n"
                    "[1]\n"
                    "id: faq:test\n"
                    "source_type: faq\n"
                    "section: General\n"
                    "title: Can I still join after the start date?\n"
                    "text: Yes, you can still join after the start date, but watch deadlines.\n"
                ),
            },
        ],
        output_model=RagAnswer,
        temperature=0,
        max_tokens=400,
    )
    answer = RagAnswer.model_validate(parse_structured_response(answer_response))
    print("rag answer:", json.dumps(answer.model_dump(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
