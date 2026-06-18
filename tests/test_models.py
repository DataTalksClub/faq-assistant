"""Unit tests for the hand-rolled StructuredModel (no pydantic)."""

from faq_assistant.models import QueryRewrite, RagAnswer
from faq_assistant.structured import parse_structured_response


def test_rag_answer_validate_and_defaults():
    answer = RagAnswer.model_validate({"answer": "hi", "found_answer": True, "source_ids": ["a", "b"]})
    assert answer.answer == "hi"
    assert answer.found_answer is True
    assert answer.source_ids == ["a", "b"]

    empty = RagAnswer.model_validate({})
    assert empty.answer == ""
    assert empty.found_answer is False
    assert empty.source_ids == []


def test_rag_answer_coerces_types():
    answer = RagAnswer.model_validate({"answer": 123, "found_answer": "yes", "source_ids": "x"})
    assert answer.answer == "123"
    assert answer.found_answer is True
    assert answer.source_ids == []  # non-list -> empty list


def test_rag_answer_json_schema_is_strict():
    schema = RagAnswer.model_json_schema()
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"answer", "found_answer", "source_ids"}
    assert set(schema["required"]) == {"answer", "found_answer", "source_ids"}
    assert schema["properties"]["source_ids"] == {"type": "array", "items": {"type": "string"}}


def test_query_rewrite_validate():
    assert QueryRewrite.model_validate({"query": "docker compose"}).query == "docker compose"


def test_parse_structured_response_from_chat_content():
    response = {"choices": [{"message": {"content": '{"query": "x"}'}}]}
    assert parse_structured_response(response) == {"query": "x"}
