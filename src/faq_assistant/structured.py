from __future__ import annotations

import json
from typing import Any


def parse_structured_response(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise RuntimeError("Model returned a non-object structured response")

    direct = response.get("response")
    if isinstance(direct, dict):
        return direct
    if isinstance(direct, str):
        parsed = parse_json_object(direct)
        if parsed:
            return parsed

    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        if isinstance(message, dict):
            parsed = message.get("parsed")
            if isinstance(parsed, dict):
                return parsed
            content = message.get("content")
            if isinstance(content, str):
                parsed_content = parse_json_object(content)
                if parsed_content:
                    return parsed_content

    raise RuntimeError("Model did not return valid structured JSON")


def parse_json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
