from __future__ import annotations

from faq_assistant.models import AnswerSource, QueryRewrite, RagAnswer
from faq_assistant.structured import parse_structured_response


def main() -> None:
    query_response = {
        "response": {
            "query": "join course after start date",
        }
    }
    rewrite = QueryRewrite.model_validate(parse_structured_response(query_response))
    assert rewrite.query == "join course after start date"

    answer_response = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"answer": "Yes, you can still join.", '
                        '"found_answer": true, '
                        '"sources": [{"id": "faq:1", "title": "Can I still join?", '
                        '"source_type": "faq", "section": "General", "url": ""}]}'
                    )
                }
            }
        ]
    }
    answer = RagAnswer.model_validate(parse_structured_response(answer_response))
    assert answer.found_answer is True
    assert answer.sources == [
        AnswerSource(
            id="faq:1",
            title="Can I still join?",
            source_type="faq",
            section="General",
            url="",
        )
    ]
    print("structured parsing ok")


if __name__ == "__main__":
    main()
