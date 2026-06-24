"""Platform-neutral RAG orchestration: rewrite -> search -> answer.

Standard-library only (plus ``zerosearch``). The OpenAI call goes through an
injectable ``chat`` callable so the pipeline can be unit-tested without network
access; the default implementation posts to the OpenAI API with ``urllib``.
"""

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from faq_assistant.models import QueryRewrite, RagAnswer, SearchResult
from faq_assistant.structured import parse_structured_response

# A chat call: (messages, output_model, max_tokens, temperature, model) -> response dict.
ChatFn = Callable[..., dict]


def make_openai_chat(config: dict[str, Any], usage: list[dict] | None = None) -> ChatFn:
    """Build a ``urllib``-based OpenAI chat callable bound to ``config``."""
    openai = config["openai"]
    api_key_env = openai["api_key_env"]
    base_url = str(openai.get("base_url", "https://api.openai.com/v1")).rstrip("/")
    default_model = config["chat"]["model"]
    timeout = float(config["chat"].get("timeout_seconds", 120))

    def chat(messages, output_model, max_tokens, temperature, model=None) -> dict:
        token = os.environ.get(api_key_env)
        if not token:
            raise RuntimeError(f"Missing OpenAI API key ({api_key_env})")
        used_model = model or default_model
        payload: dict[str, Any] = {
            "model": used_model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
        }
        if output_model is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": output_model.__name__,
                    "strict": True,
                    "schema": output_model.model_json_schema(),
                },
            }
        data = _post_json(
            f"{base_url}/chat/completions",
            payload,
            headers={"authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        if usage is not None:
            tokens = data.get("usage") or {}
            usage.append(
                {
                    "model": used_model,
                    "prompt_tokens": int(tokens.get("prompt_tokens", 0) or 0),
                    "completion_tokens": int(tokens.get("completion_tokens", 0) or 0),
                }
            )
        return data

    return chat


def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("content-type", "application/json; charset=utf-8")
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:1000]
        raise RuntimeError(f"OpenAI API request failed ({error.code}): {detail}") from error
    parsed = json.loads(text)
    return parsed if isinstance(parsed, dict) else {}


def answer_question(
    config: dict[str, Any],
    index,
    chat: ChatFn,
    question: str,
    scope: str,
    course: str | None,
    *,
    source: str = "api",
    usage: list[dict] | None = None,
) -> dict[str, Any]:
    """Run the full pipeline and return the response payload."""
    started = time.time()
    usage = [] if usage is None else usage

    rewritten_query = rewrite_query(config, chat, question, scope, course)
    results = search(config, index, rewritten_query, scope, course)
    answer, found_answer, sources = generate_answer(
        config, chat, question, rewritten_query, scope, course, results
    )

    # When we couldn't answer, say so plainly, point at who to ask (instructors for
    # a course channel, community managers elsewhere), and the resources that help.
    if not found_answer:
        if scope == "course":
            answer = "I couldn't find this in the course materials — please ask the instructors."
        else:
            answer = "I couldn't find this in the docs — please ask the community managers."
        sources = fallback_sources(config, scope, course)

    latency_ms = (time.time() - started) * 1000.0
    try:
        summary = record_usage(config, source, scope, course, usage, latency_ms, len(results))
    except Exception:  # observability must never break answering
        summary = {}

    return {
        "question": question,
        "rewritten_query": rewritten_query,
        "scope": scope,
        "course": course,
        "found_answer": found_answer,
        "answer": answer,
        "sources": sources,
        "usage": summary,
    }


# The production query-rewrite instruction. Kept as a module constant so the
# retrieval evals can rewrite with the *exact* prompt prod uses, instead of a
# hand-copied paraphrase that silently drifts.
REWRITE_SYSTEM_PROMPT = (
    "Rewrite the user's Slack message into a concise keyword search query. "
    "Focus on the underlying problem or topic the user needs information about, and "
    "drop conversational meta such as 'can someone help', 'any ideas', 'please help', "
    "or 'I'm stuck' - keep the words that describe what they actually want to find. "
    "Fix typos, preserve technical terms, and do not answer. "
    "Expand common abbreviations to their full words (e.g. 'hw' -> 'homework', "
    "'q' -> 'question', 'env' -> 'environment'). "
    "Capture the user's intent in a few keywords - do not reduce the query to a single "
    "vague token. "
    "When the user names a specific instance of something (a language, library, tool, "
    "platform, or error), keep that exact term and also add the general category it "
    "belongs to, so the query matches entries that are phrased generically as well as "
    "ones that name the specific instance. "
    "Preserve exact error messages, tool names, commands, and file names verbatim. "
    "Do not include the course name or DataTalks.Club when they are already provided "
    "as scope metadata. Keep only the words useful for keyword search."
)


def rewrite_query(config, chat: ChatFn, question: str, scope: str, course: str | None) -> str:
    if not config["retrieval"].get("rewrite_query", True):
        return question

    course_name = config["courses"].get(course, {}).get("name", course or "") if course else ""
    messages = [
        {
            "role": "system",
            "content": REWRITE_SYSTEM_PROMPT + " Return structured JSON matching the requested schema.",
        },
        {"role": "user", "content": f"scope: {scope}\ncourse: {course_name}\nmessage: {question}"},
    ]
    response = chat(
        messages,
        QueryRewrite,
        120,
        0.0,
        config["chat"].get("rewrite_model") or config["chat"]["model"],
    )
    rewritten = QueryRewrite.model_validate(parse_structured_response(response))
    return rewritten.query.strip() or question


# Raw corpus source_type -> the source label returned to the automator.
SOURCE_LABELS = {
    "faq": "faq",
    "github": "course-repo",
    "course_docs": "docs",  # course-specific pages, served from the docs repo
    "docs": "docs",
}


def search(config, index, query: str, scope: str, course: str | None) -> list[SearchResult]:
    retrieval = config["retrieval"]
    # Course channel: the course's own materials (course == X) plus the
    # course-agnostic general docs (course == ""). Elsewhere: general docs only.
    if scope == "course" and course:
        filter_data = {"course": [course, ""]}
    else:
        filter_data = {"course": ""}

    records = index.search(
        query=query,
        filter_dict=filter_data,
        boost_dict=retrieval.get("boosts", {}),
        num_results=int(retrieval["default_limit"]),
    )
    min_score = float(retrieval.get("min_score", 0))
    return [_format_record(record) for record in records if float(record.get("score", 0)) >= min_score]


def generate_answer(
    config, chat: ChatFn, question, rewritten_query, scope, course, results
) -> tuple[str, bool, list[dict]]:
    """Return (answer_text, found_answer, structured_sources)."""
    prompt_key = "course" if scope == "course" else "docs"
    instructions = config["answering"]["prompts"][prompt_key].strip()

    context = build_context(results)
    if not context:
        message = (
            "I couldn't find the answer in the course materials."
            if scope == "course"
            else "I couldn't find the answer in the docs."
        )
        return message, False, []

    messages = [
        {"role": "system", "content": instructions},
        {
            "role": "user",
            "content": (
                f"QUESTION: {question}\n\n"
                f"SEARCH QUERY: {rewritten_query}\n\n"
                f"SCOPE: {scope}\n"
                f"COURSE: {course or ''}\n\n"
                f"CONTEXT:\n{context}"
            ),
        },
    ]
    response = chat(
        messages,
        RagAnswer,
        int(config["answering"]["max_output_tokens"]),
        float(config["answering"]["temperature"]),
        None,
    )
    rag_answer = RagAnswer.model_validate(parse_structured_response(response))
    sources = resolve_sources(config, rag_answer, results)
    return rag_answer.answer.strip(), bool(rag_answer.found_answer), sources


def fallback_sources(config, scope: str, course: str | None) -> list[dict]:
    """General resources to suggest when no specific answer was found."""
    if scope == "course" and course:
        links = [
            {"source": "faq", "title": "Course FAQ", "url": f"https://datatalks.club/faq/{course}.html"},
            {"source": "docs", "title": "Course page", "url": f"https://datatalks.club/docs/courses/{course}/"},
        ]
        repos = config.get("courses", {}).get(course, {}).get("github_repositories", [])
        if repos:
            links.append({
                "source": "course-repo",
                "title": "Course repository",
                "url": f"https://github.com/{repos[0]['repo']}",
            })
        return links
    return [{"source": "docs", "title": "DataTalks.Club docs", "url": "https://datatalks.club/docs/"}]


def resolve_sources(config, rag_answer: RagAnswer, results: list[SearchResult]) -> list[dict]:
    """Map the model's cited ids to authoritative source metadata from results."""
    if not config["answering"].get("include_sources", True) or not rag_answer.found_answer:
        return []

    by_id = {result.id: result for result in results}
    seen: set[str] = set()
    sources: list[dict] = []
    for source_id in rag_answer.source_ids:
        result = by_id.get(source_id)
        if result is None:
            continue
        # Collapse multiple cited chunks of the same page/entry: they share a URL,
        # so without this the same doc shows up several times in the source list.
        dedup_key = result.url or result.id
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        sources.append(
            {
                "id": result.id,
                "source": SOURCE_LABELS.get(result.source_type, result.source_type),
                "title": _source_title(config, result),
                "url": result.url,
            }
        )
    return sources[: int(config["answering"]["max_sources"])]


def _source_title(config, result: SearchResult) -> str:
    """Display title for a cited source.

    Course doc pages get a breadcrumb ("Courses > LLM Zoomcamp > Project") so the
    reader can place the page in the course nav; other sources keep their own title.
    """
    if result.source_type == "course_docs":
        course_name = config["courses"].get(result.course, {}).get("name") or result.course
        parts = [part for part in ("Courses", course_name, result.title) if part]
        return " > ".join(parts)
    return result.title


def build_context(results: list[SearchResult]) -> str:
    lines: list[str] = []
    for position, result in enumerate(results, start=1):
        lines.append(f"[{position}]")
        lines.append(f"id: {result.id}")
        lines.append(f"source_type: {result.source_type}")
        lines.append(f"url: {result.url}")
        lines.append(f"section: {result.section}")
        lines.append(f"title: {result.title}")
        lines.append(f"text: {result.text}")
        lines.append("")
    return "\n".join(lines).strip()


def call_cost(config, call: dict) -> float:
    prices = config.get("observability", {}).get("prices", {})
    price = prices.get(call.get("model"))
    if not price:
        return 0.0
    return (
        call["prompt_tokens"] * float(price["input"])
        + call["completion_tokens"] * float(price["output"])
    ) / 1_000_000.0


def record_usage(config, source, scope, course, usage, latency_ms, num_results) -> dict:
    """Aggregate token usage + cost for one request and emit a structured log line."""
    prompt_tokens = sum(c["prompt_tokens"] for c in usage)
    completion_tokens = sum(c["completion_tokens"] for c in usage)
    cost = sum(call_cost(config, c) for c in usage)
    summary = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": round(cost, 6),
    }

    obs = config.get("observability", {})
    if not obs.get("enabled", False) or not usage:
        return summary

    models = ",".join(sorted({c["model"] for c in usage}))
    # Structured log line, captured by CloudWatch Logs (query with Logs Insights).
    try:
        print(json.dumps({
            "type": "usage", "source": source, "scope": scope, "course": course or "",
            "models": models, "calls": len(usage), "num_results": num_results,
            "latency_ms": round(latency_ms, 1), **summary,
        }))
    except Exception:
        pass

    return summary


def _format_record(record: dict[str, Any]) -> SearchResult:
    return SearchResult(
        id=str(record.get("id", "")),
        score=float(record.get("score", 0)),
        source_type=str(record.get("source_type", "")),
        scope=str(record.get("scope", "")),
        course=str(record.get("course", "")),
        section=str(record.get("section", "")),
        title=str(record.get("title", "")),
        text=str(record.get("text", "")),
        url=str(record.get("url", "")),
        repo=str(record.get("repo", "")),
        path=str(record.get("path", "")),
    )
