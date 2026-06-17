from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import base64
import zlib
from asyncio import create_task
from urllib.parse import urlparse

from js import fetch
from pyodide.ffi import create_proxy
from workers import Response, WorkerEntrypoint, wait_until

from faq_assistant.generated_config import CONFIG
from faq_assistant.minsearch import Index
from faq_assistant.models import QueryRewrite, RagAnswer, SearchResult
from faq_assistant.search_corpus import SEARCH_CORPUS_B64
from faq_assistant.structured import parse_structured_response
from faq_assistant.worker_interop import env_value, parse_json, to_js


SEARCH_INDEX: Index | None = None


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        if request.method == "OPTIONS":
            return empty_response()

        try:
            return await route_request(self.env, request)
        except BaseException as error:
            return json_response({"error": str(error) or "Unknown error"}, 500)


async def route_request(env, request) -> Response:
    path = urlparse(str(request.url)).path
    method = str(request.method)

    if path == "/health" and method == "GET":
        return json_response({"ok": True, "app": CONFIG["app"]["name"]})

    if path == "/ask" and method == "POST":
        if not verify_shared_secret(env, request):
            return json_response({"error": "Unauthorized"}, 401)

        body = parse_json(str(await request.text()))
        question = clean_question(str(body.get("question", "")))
        if not question:
            return json_response({"error": "`question` is required"}, 400)
        scope = str(body.get("scope") or "docs")
        course = body.get("course")
        result = await answer_question(env, question, scope, course)
        return json_response(result)

    if path == CONFIG["slack"]["events_path"] and method == "POST":
        raw_body = str(await request.text())
        if not verify_slack_signature(env, request, raw_body):
            return json_response({"error": "Invalid Slack signature"}, 401)

        payload = parse_json(raw_body)
        if payload.get("type") == "url_verification":
            return json_response({"challenge": payload.get("challenge", "")})

        if payload.get("type") == "event_callback":
            event = payload.get("event", {})
            if isinstance(event, dict) and event.get("type") == "app_mention":
                wait_until(create_proxy(create_task(handle_app_mention(env, event))))
            return json_response({"ok": True})

    return json_response({"error": "Not found"}, 404)


async def handle_app_mention(env, event: dict) -> None:
    if event.get("bot_id"):
        return

    channel_id = str(event.get("channel") or "")
    thread_ts = str(event.get("thread_ts") or event.get("ts") or "")
    question = clean_question(str(event.get("text") or ""))
    if not channel_id or not thread_ts or not question:
        return

    scope, course = scope_for_channel(channel_id)
    result = await answer_question(env, question, scope, course)
    await post_slack_message(env, channel_id, thread_ts, result["answer"])


def scope_for_channel(channel_id: str) -> tuple[str, str | None]:
    channel = CONFIG["slack"]["channels"].get(channel_id)
    if channel:
        return str(channel.get("scope", "docs")), channel.get("course")
    return str(CONFIG["slack"].get("default_scope", "docs")), None


async def answer_question(env, question: str, scope: str, course: str | None) -> dict:
    rewritten_query = await rewrite_query(env, question, scope, course)
    results = await search(env, rewritten_query, scope, course)
    answer = await generate_answer(env, question, rewritten_query, scope, course, results)
    return {
        "question": question,
        "rewritten_query": rewritten_query,
        "scope": scope,
        "course": course,
        "results": results,
        "answer": answer,
    }


async def rewrite_query(env, question: str, scope: str, course: str | None) -> str:
    if not CONFIG["retrieval"].get("rewrite_query", True):
        return question

    course_name = ""
    if course:
        course_name = CONFIG["courses"].get(course, {}).get("name", course)

    messages = [
        {
            "role": "system",
            "content": (
                "Rewrite the user's Slack message into one concise keyword search query. "
                "Fix typos, remove mentions and filler, preserve technical terms, and do not answer. "
                "Do not include the course name or DataTalks.Club when they are already provided "
                "as scope metadata. Keep only the words useful for keyword search. "
                "Return structured JSON matching the requested schema."
            ),
        },
        {
            "role": "user",
            "content": f"scope: {scope}\ncourse: {course_name}\nmessage: {question}",
        },
    ]
    rewritten = await run_structured_chat(
        env,
        messages,
        output_model=QueryRewrite,
        max_tokens=120,
        temperature=0.0,
    )
    return rewritten.query.strip() or question


async def search(env, query: str, scope: str, course: str | None) -> list[dict]:
    retrieval = CONFIG["retrieval"]
    index = get_search_index()

    filter_data = {"scope": scope}
    if scope == "course" and course:
        filter_data["course"] = course

    records = index.search(
        query=query,
        filter_dict=filter_data,
        boost_dict=retrieval.get("boosts", {}),
        num_results=int(retrieval["default_limit"]),
    )

    min_score = float(retrieval.get("min_score", 0))
    results = [format_search_record(record).model_dump() for record in records]
    return [result for result in results if result["score"] >= min_score]


def get_search_index() -> Index:
    global SEARCH_INDEX
    if SEARCH_INDEX is None:
        records = json.loads(zlib.decompress(base64.b64decode(SEARCH_CORPUS_B64)).decode("utf-8"))
        SEARCH_INDEX = Index(
            text_fields=["title", "section", "text"],
            keyword_fields=["id", "source_type", "scope", "course", "url", "repo", "path"],
        ).fit(records)
    return SEARCH_INDEX


async def generate_answer(
    env,
    question: str,
    rewritten_query: str,
    scope: str,
    course: str | None,
    results: list[dict],
) -> str:
    prompt_key = "course" if scope == "course" else "docs"
    instructions = CONFIG["answering"]["prompts"][prompt_key].strip()

    context = build_context(results)
    if not context:
        if scope == "course":
            return "I couldn't find the answer in the course materials."
        return "I couldn't find the answer in the docs."

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
    rag_answer = await run_structured_chat(
        env,
        messages,
        output_model=RagAnswer,
        max_tokens=int(CONFIG["answering"]["max_output_tokens"]),
        temperature=float(CONFIG["answering"]["temperature"]),
    )
    return format_structured_answer(rag_answer, results)


def format_structured_answer(rag_answer: RagAnswer, results: list[dict]) -> str:
    answer = rag_answer.answer.strip()
    if not CONFIG["answering"].get("include_sources", True):
        return answer

    allowed_ids = {result["id"] for result in results}
    valid_sources = [source for source in rag_answer.sources if source.id in allowed_ids]
    if not rag_answer.found_answer or not valid_sources:
        return answer

    source_lines = []
    for source in valid_sources[: int(CONFIG["answering"]["max_sources"])]:
        parts = [source.source_type, source.section, source.title]
        source_lines.append("- " + " > ".join(part for part in parts if part))

    return f"{answer}\n\nSources:\n" + "\n".join(source_lines)


async def run_chat(env, messages: list[dict], max_tokens: int, temperature: float) -> str:
    response = await openai_chat(env, messages, None, max_tokens, temperature)
    return parse_chat_response(response)


async def run_structured_chat(env, messages: list[dict], output_model, max_tokens: int, temperature: float):
    response = await openai_chat(env, messages, output_model, max_tokens, temperature)
    return output_model.model_validate(parse_structured_response(response))


async def openai_chat(
    env,
    messages: list[dict],
    output_model,
    max_tokens: int,
    temperature: float,
) -> dict:
    config = CONFIG["chat"]
    if config["provider"] != "openai":
        raise RuntimeError(f"Unsupported chat provider: {config['provider']}")

    payload: dict = {
        "model": config["model"],
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
    return await openai_fetch(env, "/chat/completions", payload)


async def openai_fetch(env, path: str, payload: dict) -> dict:
    token = env_value(env, CONFIG["openai"]["api_key_env"])
    if not token:
        raise RuntimeError("Missing OpenAI API key")

    base_url = str(CONFIG["openai"].get("base_url", "https://api.openai.com/v1")).rstrip("/")
    response = await fetch(
        f"{base_url}{path}",
        to_js(
            {
                "method": "POST",
                "headers": {
                    "authorization": f"Bearer {token}",
                    "content-type": "application/json; charset=utf-8",
                },
                "body": json.dumps(payload),
            }
        ),
    )
    text = str(await response.text())
    data = parse_json(text)
    if int(response.status) >= 400:
        raise RuntimeError(f"OpenAI API request failed ({response.status}): {text[:1000]}")
    return data


def parse_chat_response(response) -> str:
    if not isinstance(response, dict):
        return ""

    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        if isinstance(message, dict) and message.get("content"):
            return str(message["content"])

    return str(response.get("response") or "")


def build_context(results: list[dict]) -> str:
    lines: list[str] = []
    for index, result in enumerate(results, start=1):
        lines.append(f"[{index}]")
        lines.append(f"id: {result['id']}")
        lines.append(f"source_type: {result['source_type']}")
        lines.append(f"url: {result['url']}")
        lines.append(f"section: {result['section']}")
        lines.append(f"title: {result['title']}")
        lines.append(f"text: {result['text']}")
        lines.append("")
    return "\n".join(lines).strip()


def format_search_record(record: dict) -> SearchResult:
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


async def post_slack_message(env, channel: str, thread_ts: str, text: str) -> None:
    token = env_value(env, CONFIG["slack"]["bot_token_env"])
    if not token:
        raise RuntimeError("Missing Slack bot token")

    payload = {
        "channel": channel,
        "thread_ts": thread_ts,
        "text": text[:39000],
        "unfurl_links": bool(CONFIG["slack"]["reply"]["unfurl_links"]),
        "unfurl_media": bool(CONFIG["slack"]["reply"]["unfurl_media"]),
    }

    response = await fetch(
        "https://slack.com/api/chat.postMessage",
        to_js(
            {
                "method": "POST",
                "headers": {
                    "authorization": f"Bearer {token}",
                    "content-type": "application/json; charset=utf-8",
                },
                "body": json.dumps(payload),
            }
        ),
    )
    data = parse_json(str(await response.text()))
    if not data.get("ok"):
        raise RuntimeError(f"Slack post failed: {data}")


def verify_slack_signature(env, request, raw_body: str) -> bool:
    secret = env_value(env, CONFIG["slack"]["signing_secret_env"])
    if not secret:
        return False

    timestamp = str(request.headers.get("x-slack-request-timestamp") or "")
    signature = str(request.headers.get("x-slack-signature") or "")
    if not timestamp or not signature:
        return False

    try:
        if abs(time.time() - int(timestamp)) > 60 * 5:
            return False
    except ValueError:
        return False

    base = f"v0:{timestamp}:{raw_body}".encode("utf-8")
    expected = "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_shared_secret(env, request) -> bool:
    secret_env = CONFIG.get("api", {}).get("shared_secret_env", "")
    secret = env_value(env, secret_env)
    if not secret:
        return True

    received = str(request.headers.get("x-faq-assistant-secret") or "")
    return hmac.compare_digest(secret, received)


def clean_question(text: str) -> str:
    text = re.sub(r"<@[A-Z0-9]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def json_response(data: object, status: int = 200) -> Response:
    return Response(
        json.dumps(data),
        status=status,
        headers={
            "content-type": "application/json; charset=utf-8",
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,POST,OPTIONS",
            "access-control-allow-headers": "content-type,x-faq-assistant-secret,x-slack-request-timestamp,x-slack-signature",
        },
    )


def empty_response() -> Response:
    return Response(
        "",
        status=204,
        headers={
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,POST,OPTIONS",
            "access-control-allow-headers": "content-type,x-faq-assistant-secret,x-slack-request-timestamp,x-slack-signature",
        },
    )
