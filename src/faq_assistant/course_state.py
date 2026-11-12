"""Validate a public CMP snapshot and expose it as fixed answer context.

This module performs no network requests and accepts no student information.
The caller must authenticate the snapshot separately from ordinary questions.
"""

import json
from datetime import date, datetime
from urllib.parse import urlsplit

from faq_assistant.models import SearchResult

MAX_BYTES = 65536
MAX_ITEMS = 100


def _object(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("course_state has missing or unsupported fields")
    return value


def _text(value):
    if not isinstance(value, str) or not value or len(value) > 300:
        raise ValueError("course_state contains an invalid string")


def _date(value):
    if value is None:
        return
    _text(value)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        date.fromisoformat(value)


def _bool(value):
    if type(value) is not bool:
        raise ValueError("course_state contains an invalid boolean")


def _url(value, course_path):
    _text(value)
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.netloc != "courses.datatalks.club"
            or parsed.query or parsed.fragment
            or not (parsed.path == course_path or parsed.path.startswith(course_path))
            or any(part in {".", ".."} for part in parsed.path.split("/"))
            or "%" in parsed.path):
        raise ValueError("course_state source URL must belong to its CMP course")


def validate_course_state(value, config, scope, course):
    """Return only a bounded, public, same-course CMP snapshot, or raise."""
    if len(json.dumps(value).encode("utf-8")) > MAX_BYTES:
        raise ValueError("course_state is too large")
    _object(value, {"schema_version", "fetched_at", "course", "homeworks", "projects"})
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported course_state schema_version")
    if scope != "course" or not course:
        raise ValueError("course_state requires a course scope")
    _date(value["fetched_at"])
    if value["fetched_at"] is None:
        raise ValueError("course_state fetched_at is required")
    info = _object(value["course"], {
        "slug", "title", "source_url", "starts_at", "start_date", "end_date", "finished",
    })
    _text(info["slug"])
    _text(info["title"])
    course_path = f'/{info["slug"]}/'
    _url(info["source_url"], course_path)
    for name in ("starts_at", "start_date", "end_date"):
        _date(info[name])
    _bool(info["finished"])
    common = {"slug", "title", "state", "starts_at", "source_url"}
    for kind, additional in (
        ("homeworks", {"due_date"}),
        ("projects", {"submission_due_date", "peer_review_due_date", "peer_review_open",
                      "peer_review_opens_at", "assignments_created"}),
    ):
        items = value[kind]
        if not isinstance(items, list) or len(items) > MAX_ITEMS:
            raise ValueError("course_state contains too many items")
        for item in items:
            _object(item, common | additional)
            for name in ("slug", "title", "state"):
                _text(item[name])
            _url(item["source_url"], course_path)
            for name in (common | additional) - {"slug", "title", "state", "source_url"}:
                if name in {"peer_review_open", "assignments_created"}:
                    _bool(item[name])
                else:
                    _date(item[name])
    return value


def course_state_result(value, course):
    return SearchResult(
        id="cmp:course-state", source_id="cmp:course-state", source_type="cmp",
        scope="course", course=course, title="Live CMP course state",
        url=value["course"]["source_url"], current=True, authority="canonical",
        text=json.dumps(value, ensure_ascii=False),
    )
