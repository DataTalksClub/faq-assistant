from faq_assistant.models import QueryRewrite, RagAnswer
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
                        '"source_ids": ["faq:1", "faq:2"]}'
                    )
                }
            }
        ]
    }
    answer = RagAnswer.model_validate(parse_structured_response(answer_response))
    assert answer.found_answer is True
    assert answer.source_ids == ["faq:1", "faq:2"]
    print("structured parsing ok")


if __name__ == "__main__":
    main()
