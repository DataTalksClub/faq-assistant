from __future__ import annotations

import json
import os
import sys

from faq_assistant.cloudflare import CloudflareClient
from faq_assistant.config import load_config
from faq_assistant.models import QueryRewrite, RagAnswer
from faq_assistant.structured import parse_structured_response


def main() -> int:
    config = load_config()
    account_env = config["cloudflare"]["account_id_env"]
    token_env = config["cloudflare"]["api_token_env"]

    if not os.environ.get(account_env) or not os.environ.get(token_env):
        print(f"skipped: set {account_env} and {token_env} to test the real Workers AI model")
        return 0

    client = CloudflareClient(config)
    model = config["cloudflare"]["ai"]["chat_model"]

    rewrite_response = client.run_ai(
        model,
        {
            "messages": [
                {
                    "role": "system",
                    "content": "Rewrite the user's message into a concise search query.",
                },
                {
                    "role": "user",
                    "content": "Can I still join the course after it started?",
                },
            ],
            "temperature": 0,
            "max_tokens": 120,
            "response_format": {
                "type": "json_schema",
                "json_schema": QueryRewrite.model_json_schema(),
            },
        },
    )
    rewrite = QueryRewrite.model_validate(parse_structured_response(rewrite_response))
    assert rewrite.query.strip(), rewrite_response
    print("query rewrite:", rewrite.model_dump())

    answer_response = client.run_ai(
        model,
        {
            "messages": [
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
            "temperature": 0,
            "max_tokens": 400,
            "response_format": {
                "type": "json_schema",
                "json_schema": RagAnswer.model_json_schema(),
            },
        },
    )
    answer = RagAnswer.model_validate(parse_structured_response(answer_response))
    print("rag answer:", json.dumps(answer.model_dump(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
