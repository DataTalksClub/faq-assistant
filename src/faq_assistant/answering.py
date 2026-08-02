"""Platform-neutral RAG orchestration: rewrite -> search -> answer.

Standard-library only (plus ``zerosearch``). The OpenAI call goes through an
injectable ``chat`` callable so the pipeline can be unit-tested without network
access; the default implementation posts to the OpenAI API with ``urllib``.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from faq_assistant.models import QueryRewrite, RagAnswer, SearchResult
from faq_assistant.structured import parse_structured_response

# A chat call: (messages, output_model, max_tokens, temperature, model) -> response dict.
ChatFn = Callable[..., dict]


@dataclass(frozen=True)
class QueryIntent:
    requested_cohort: str = ""
    current_cohort: str = ""
    temporal: bool = False


@dataclass
class RetrievalOutcome:
    results: list[SearchResult] = field(default_factory=list)
    intent: QueryIntent = field(default_factory=QueryIntent)
    candidate_count: int = 0
    filtered_historical: int = 0
    deduplicated: int = 0


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
    retrieval = retrieve(config, index, rewritten_query, scope, course, original_question=question)
    results = retrieval.results
    answer, found_answer, sources = generate_answer(
        config, chat, question, rewritten_query, scope, course, results
    )

    # When we couldn't answer, say so plainly, point at who to ask (instructors for
    # a course channel, community managers elsewhere), and the resources that help.
    if not found_answer:
        platform_url = (
            config.get("courses", {}).get(course, {}).get("platform_url") if course else None
        )
        if scope == "course" and retrieval.intent.temporal and platform_url:
            answer = (
                "I couldn't find a current value in the course materials. "
                f"Check the [current course-management platform]({platform_url}); "
                "if it isn't listed there, ask the instructors."
            )
        elif scope == "course":
            answer = "I couldn't find this in the course materials — please ask the instructors."
        else:
            answer = "I couldn't find this in the docs — please ask the community managers."
        sources = fallback_sources(config, scope, course)

    latency_ms = (time.time() - started) * 1000.0
    try:
        summary = record_usage(
            config, source, scope, course, usage, latency_ms, len(results),
            retrieval=retrieval,
        )
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
    "course_status": "docs",
}


_COHORT_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_TEMPORAL_RE = re.compile(
    r"\b(?:cohort|current|latest|start|join|register|registration|enroll|enrollment|"
    r"deadline|due|submit|submission|peer review|review assignments?|"
    r"certificate|workshop|leaderboard|dashboard|course management|platform)\b|"
    r"\b(?:where|what|which)\b.{0,30}\blink\b",
    re.IGNORECASE,
)


def detect_query_intent(config: dict[str, Any], question: str, course: str | None) -> QueryIntent:
    cohort_match = _COHORT_YEAR_RE.search(question)
    requested = cohort_match.group(1) if cohort_match else ""
    current = ""
    if course:
        current = str(config.get("courses", {}).get(course, {}).get("current_cohort") or "")
    return QueryIntent(
        requested_cohort=requested,
        current_cohort=current,
        temporal=bool(requested or _TEMPORAL_RE.search(question)),
    )


def search(
    config, index, query: str, scope: str, course: str | None, *, original_question: str | None = None
) -> list[SearchResult]:
    return retrieve(
        config, index, query, scope, course, original_question=original_question
    ).results


def retrieve(
    config, index, query: str, scope: str, course: str | None, *, original_question: str | None = None
) -> RetrievalOutcome:
    retrieval = config["retrieval"]
    # Course channel: the course's own materials (course == X) plus the
    # course-agnostic general docs (course == ""). Elsewhere: general docs only.
    if scope == "course" and course:
        filter_data = {"course": [course, ""]}
    else:
        filter_data = {"course": ""}

    rerank_enabled = bool(retrieval.get("freshness_rerank", False))
    result_limit = int(retrieval["default_limit"])
    candidate_limit = int(retrieval.get("candidate_limit", result_limit)) if rerank_enabled else result_limit
    intent = detect_query_intent(config, original_question or query, course)
    search_query = query
    if rerank_enabled and intent.temporal and not intent.requested_cohort and intent.current_cohort:
        # Stable authority terms ensure the canonical course-status record enters
        # the pool while preserving every term from the rewritten user query.
        search_query += f" current course management platform {intent.current_cohort}"
    records = index.search(
        query=search_query,
        filter_dict=filter_data,
        boost_dict=retrieval.get("boosts", {}),
        num_results=candidate_limit,
    )
    min_score = float(retrieval.get("min_score", 0))
    candidates = [
        _format_record(record) for record in records
        if float(record.get("score", 0)) >= min_score
    ]
    if not rerank_enabled:
        return RetrievalOutcome(
            results=candidates[:result_limit], intent=intent, candidate_count=len(candidates)
        )

    has_current_canonical = any(
        result.current and result.authority == "canonical" for result in candidates
    )
    filtered: list[SearchResult] = []
    filtered_historical = 0
    for result in candidates:
        if intent.requested_cohort and result.cohort and result.cohort != intent.requested_cohort:
            filtered_historical += 1
            continue
        if (
            not intent.requested_cohort
            and intent.temporal
            and has_current_canonical
            and result.cohort
            and result.cohort != intent.current_cohort
        ):
            filtered_historical += 1
            continue
        filtered.append(result)

    current_boost = float(retrieval.get("current_cohort_boost", 1.0))
    canonical_boost = float(retrieval.get("canonical_boost", 1.0))
    historical_boost = float(retrieval.get("historical_boost", 1.0))

    def rank_score(result: SearchResult) -> float:
        multiplier = 1.0
        target = intent.requested_cohort or (intent.current_cohort if intent.temporal else "")
        if target and result.cohort == target:
            multiplier *= current_boost
        if intent.temporal and result.authority == "canonical":
            multiplier *= canonical_boost
        elif intent.temporal and result.authority == "historical":
            multiplier *= historical_boost
        return result.score * multiplier

    filtered.sort(key=lambda result: (-rank_score(result), result.id))

    max_per_source = max(1, int(retrieval.get("max_chunks_per_source", 2)))
    per_source: dict[str, int] = {}
    selected: list[SearchResult] = []
    deduplicated = 0
    for result in filtered:
        source_key = result.source_id or result.url or result.path or result.id
        count = per_source.get(source_key, 0)
        if count >= max_per_source:
            deduplicated += 1
            continue
        per_source[source_key] = count + 1
        selected.append(result)
        if len(selected) >= result_limit:
            break

    return RetrievalOutcome(
        results=selected,
        intent=intent,
        candidate_count=len(candidates),
        filtered_historical=filtered_historical,
        deduplicated=deduplicated,
    )


def generate_answer(
    config, chat: ChatFn, question, rewritten_query, scope, course, results
) -> tuple[str, bool, list[dict]]:
    """Return (answer_text, found_answer, structured_sources)."""
    prompt_key = "course" if scope == "course" else "docs"
    instructions = config["answering"]["prompts"][prompt_key].strip()
    course_config = config.get("courses", {}).get(course, {}) if course else {}
    current_cohort = str(course_config.get("current_cohort") or "")
    platform_url = str(course_config.get("platform_url") or "")
    if scope == "course" and current_cohort:
        instructions += (
            f"\n\nThe configured current cohort for this course is {current_cohort}. "
            "For an unqualified question about enrollment, deadlines, submissions, "
            "projects, peer review, certificates, or other live course state, use only "
            "current canonical context. Use historical cohort facts only when the user "
            "explicitly asks about that cohort. Never combine conflicting cohorts. "
            "If a live date or state is not present in current canonical context, direct "
            "the user to the current course-management platform and include its exact URL "
            "instead of inferring it "
            "from historical material. If the available context conflicts and cannot be "
            "resolved by current/canonical metadata, set found_answer to false."
        )

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
                f"CURRENT_COHORT: {current_cohort}\n"
                f"CURRENT_PLATFORM: {platform_url}\n\n"
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
        platform_url = config.get("courses", {}).get(course, {}).get("platform_url")
        if platform_url:
            links.append({
                "source": "docs",
                "title": "Current course-management platform",
                "url": platform_url,
            })
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

    Course doc pages get a breadcrumb ("Courses › LLM Zoomcamp › Project") so the
    reader can place the page in the course nav. Course repository pages keep
    their title unless the course opts into a path adapter.

    The breadcrumb uses a Unicode separator instead of ``>`` because source
    titles are embedded in Slack's ``<url|label>`` links. A literal ``>`` ends
    the link early and leaves the rest of the title as plain text.
    """
    if result.source_type == "course_docs":
        course_name = config["courses"].get(result.course, {}).get("name") or result.course
        parts = [part for part in ("Courses", course_name, result.title) if part]
        return " › ".join(parts)
    if result.source_type == "github":
        adapter = REPO_TITLE_ADAPTERS.get(result.course)
        if adapter:
            return adapter(result.path, result.title)
    return result.title


REPO_TITLE_ADAPTERS = {
    "llm-zoomcamp": lambda path, title: _llm_zoomcamp_repo_source_title(path, title),
}


def _llm_zoomcamp_repo_source_title(path: str, title: str) -> str:
    raw_parts = _path_parts(path)
    if len(raw_parts) == 3 and raw_parts[1].lower() == "lessons":
        return _join_title_parts([
            _humanize_path_part(raw_parts[0]),
            _title_with_file_number(raw_parts[2], title.strip()),
        ], title)
    return _generic_repo_source_title(path, title)


def _generic_repo_source_title(path: str, title: str) -> str:
    normalized_path = path.strip("/")
    title = title.strip()
    if not normalized_path:
        return title

    raw_parts = _path_parts(normalized_path)
    if not raw_parts:
        return title

    filename = raw_parts[-1]
    directory_parts = raw_parts[:-1]
    if filename.lower() in {"readme.md", "index.md"}:
        path_parts = [_humanize_path_part(part) for part in directory_parts]
        page_title = title
    else:
        path_parts = [_humanize_path_part(part) for part in directory_parts]
        page_title = _title_with_file_number(filename, title)

    return _join_title_parts([*path_parts, page_title], title)


def _path_parts(path: str) -> list[str]:
    return [part for part in path.strip("/").split("/") if part]


def _join_title_parts(parts: list[str], fallback: str) -> str:
    return " › ".join(part for part in parts if part) or fallback


def _title_with_file_number(filename: str, title: str) -> str:
    match = re.match(r"^(\d+)[-_](.+)$", _strip_markdown_extension(filename))
    if not match:
        return title or _humanize_path_part(filename)
    number, _ = match.groups()
    return f"{number}. {title}" if title else _humanize_path_part(filename)


def _humanize_path_part(part: str) -> str:
    stem = _strip_markdown_extension(part)
    match = re.match(r"^(\d+)[-_](.+)$", stem)
    if match:
        number, name = match.groups()
        return f"{number}. {_humanize_words(name)}"
    return _humanize_words(stem)


def _strip_markdown_extension(value: str) -> str:
    return value[:-3] if value.lower().endswith(".md") else value


def _humanize_words(value: str) -> str:
    text = re.sub(r"[-_]+", " ", value).strip()
    return text.title() if text else ""


def build_context(results: list[SearchResult]) -> str:
    lines: list[str] = []
    for position, result in enumerate(results, start=1):
        lines.append(f"[{position}]")
        lines.append(f"id: {result.id}")
        lines.append(f"source_type: {result.source_type}")
        lines.append(f"source_id: {result.source_id}")
        lines.append(f"cohort: {result.cohort}")
        lines.append(f"current: {str(result.current).lower()}")
        lines.append(f"authority: {result.authority}")
        lines.append(f"path: {result.path}")
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


def record_usage(
    config, source, scope, course, usage, latency_ms, num_results, *, retrieval=None
) -> dict:
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
        retrieval_fields = {}
        if retrieval is not None:
            retrieval_fields = {
                "candidate_count": retrieval.candidate_count,
                "filtered_historical": retrieval.filtered_historical,
                "deduplicated": retrieval.deduplicated,
                "requested_cohort": retrieval.intent.requested_cohort,
                "current_cohort": retrieval.intent.current_cohort,
                "temporal": retrieval.intent.temporal,
                "selected_source_ids": [result.source_id or result.id for result in retrieval.results],
                "selected_cohorts": [result.cohort for result in retrieval.results],
                "selected_authorities": [result.authority for result in retrieval.results],
            }
        print(json.dumps({
            "type": "usage", "source": source, "scope": scope, "course": course or "",
            "models": models, "calls": len(usage), "num_results": num_results,
            "latency_ms": round(latency_ms, 1), **retrieval_fields, **summary,
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
        source_id=str(record.get("source_id", "")),
        cohort=str(record.get("cohort", "")),
        current=bool(record.get("current", False)),
        authority=str(record.get("authority", "reference")),
    )
