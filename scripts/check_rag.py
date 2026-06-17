from __future__ import annotations

import json
import os
import sys
from typing import Any

from faq_assistant.cloudflare import CloudflareClient
from faq_assistant.config import load_config
from faq_assistant.ingest import embedding_dimensions_parameter
from faq_assistant.models import QueryRewrite, RagAnswer, SearchResult
from faq_assistant.openai import OpenAIClient
from faq_assistant.structured import parse_structured_response


def main() -> int:
    config = load_config()
    account_env = config["cloudflare"]["account_id_env"]
    token_env = config["cloudflare"]["api_token_env"]

    if not os.environ.get(account_env) or not os.environ.get(token_env):
        print(f"skipped: set {account_env} and {token_env} to test RAG")
        return 0

    cloudflare = CloudflareClient(config)
    openai = OpenAIClient(config)

    cases = [
        {
            "name": "llm-course",
            "question": "Can I still join the course after it started?",
            "scope": "course",
            "course": "llm-zoomcamp",
        },
        {
            "name": "docs",
            "question": "How do I join DataTalks.Club Slack?",
            "scope": "docs",
            "course": None,
        },
    ]

    for case in cases:
        print(f"\n== {case['name']} ==")
        result = run_rag_case(cloudflare, openai, config, case)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        assert result["answer"]["answer"].strip()
        assert result["answer"]["found_answer"] is True
        assert result["results"], "expected at least one retrieved source"

    return 0


def run_rag_case(
    cloudflare: CloudflareClient,
    openai: OpenAIClient,
    config: dict[str, Any],
    case: dict[str, Any],
) -> dict[str, Any]:
    question = case["question"]
    scope = case["scope"]
    course = case["course"]

    rewritten = rewrite_query(openai, config, question, scope, course)
    results = vector_search(cloudflare, openai, config, rewritten.query, scope, course)
    answer = answer_question(openai, config, question, rewritten.query, scope, course, results)

    allowed_ids = {result.id for result in results}
    cited_ids = {source.id for source in answer.sources}
    assert cited_ids <= allowed_ids, f"model cited unknown IDs: {cited_ids - allowed_ids}"

    return {
        "question": question,
        "rewritten_query": rewritten.model_dump(),
        "filter": {"scope": scope, **({"course": course} if course else {})},
        "results": [result.model_dump() for result in results],
        "answer": answer.model_dump(),
    }


def rewrite_query(
    openai: OpenAIClient,
    config: dict[str, Any],
    question: str,
    scope: str,
    course: str | None,
) -> QueryRewrite:
    course_name = config["courses"].get(course, {}).get("name", course or "")
    response = openai.chat_structured(
        config["chat"]["model"],
        [
            {
                "role": "system",
                "content": (
                    "Rewrite the user's Slack message into one concise semantic search query. "
                    "Fix typos, remove mentions and filler, preserve technical terms, and do not answer. "
                    "Return structured JSON matching the requested schema."
                ),
            },
            {
                "role": "user",
                "content": f"scope: {scope}\ncourse: {course_name}\nmessage: {question}",
            },
        ],
        output_model=QueryRewrite,
        temperature=0,
        max_tokens=120,
    )
    return QueryRewrite.model_validate(parse_structured_response(response))


def vector_search(
    cloudflare: CloudflareClient,
    openai: OpenAIClient,
    config: dict[str, Any],
    query: str,
    scope: str,
    course: str | None,
) -> list[SearchResult]:
    embedding = openai.embed_texts(
        config["embeddings"]["model"],
        [query],
        dimensions=embedding_dimensions_parameter(config["embeddings"]),
    )[0]
    filter_data = {"scope": scope}
    if scope == "course" and course:
        filter_data["course"] = course

    response = cloudflare.query_vectors(
        embedding,
        {
            "topK": int(config["retrieval"]["default_limit"]),
            "returnMetadata": "all",
            "returnValues": False,
            "filter": filter_data,
        },
    )
    min_score = float(config["retrieval"]["min_score"])
    matches = response.get("matches", [])
    return [
        format_match(match)
        for match in matches
        if float(match.get("score", 0)) >= min_score
    ]


def answer_question(
    openai: OpenAIClient,
    config: dict[str, Any],
    question: str,
    rewritten_query: str,
    scope: str,
    course: str | None,
    results: list[SearchResult],
) -> RagAnswer:
    prompt_key = "course" if scope == "course" else "docs"
    response = openai.chat_structured(
        config["chat"]["model"],
        [
            {
                "role": "system",
                "content": config["answering"]["prompts"][prompt_key].strip(),
            },
            {
                "role": "user",
                "content": (
                    f"QUESTION: {question}\n\n"
                    f"SEARCH QUERY: {rewritten_query}\n\n"
                    f"SCOPE: {scope}\n"
                    f"COURSE: {course or ''}\n\n"
                    f"CONTEXT:\n{build_context(results)}"
                ),
            },
        ],
        output_model=RagAnswer,
        temperature=float(config["answering"]["temperature"]),
        max_tokens=int(config["answering"]["max_output_tokens"]),
    )
    return RagAnswer.model_validate(parse_structured_response(response))


def build_context(results: list[SearchResult]) -> str:
    lines: list[str] = []
    for index, result in enumerate(results, start=1):
        lines.append(f"[{index}]")
        lines.append(f"id: {result.id}")
        lines.append(f"source_type: {result.source_type}")
        lines.append(f"url: {result.url}")
        lines.append(f"section: {result.section}")
        lines.append(f"title: {result.title}")
        lines.append(f"text: {result.text}")
        lines.append("")
    return "\n".join(lines).strip()


def format_match(match: dict[str, Any]) -> SearchResult:
    metadata = match.get("metadata", {}) if isinstance(match, dict) else {}
    return SearchResult(
        id=str(metadata.get("id") or match.get("id", "")),
        score=float(match.get("score", 0)),
        source_type=str(metadata.get("source_type", "")),
        scope=str(metadata.get("scope", "")),
        course=str(metadata.get("course", "")),
        section=str(metadata.get("section", "")),
        title=str(metadata.get("title", "")),
        text=str(metadata.get("text", "")),
        url=str(metadata.get("url", "")),
        repo=str(metadata.get("repo", "")),
        path=str(metadata.get("path", "")),
    )


if __name__ == "__main__":
    sys.exit(main())
