from __future__ import annotations

import os
from typing import Any

import requests


class OpenAIClient:
    def __init__(self, config: dict[str, Any]):
        openai = config["openai"]
        self.api_key = required_env(openai["api_key_env"])
        self.base_url = str(openai.get("base_url", "https://api.openai.com/v1")).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.api_key}"})

    def chat_structured(
        self,
        model: str,
        messages: list[dict],
        output_model,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": output_model.__name__,
                    "strict": True,
                    "schema": output_model.model_json_schema(),
                },
            },
        }
        response = self.session.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            timeout=120,
        )
        return unwrap_response(response)


def unwrap_response(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as e:
        raise RuntimeError(f"OpenAI returned non-JSON response: {response.text[:500]}") from e

    if not response.ok:
        raise RuntimeError(f"OpenAI API request failed ({response.status_code}): {data}")

    return data if isinstance(data, dict) else {"result": data}


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
