from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import requests


class OpenAIClient:
    def __init__(self, config: dict[str, Any]):
        openai = config["openai"]
        self.api_key = required_env(openai["api_key_env"])
        self.base_url = str(openai.get("base_url", "https://api.openai.com/v1")).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.api_key}"})

    def embed_texts(
        self,
        model: str,
        texts: list[str],
        dimensions: int | None = None,
    ) -> list[list[float]]:
        payload: dict[str, Any] = {
            "model": model,
            "input": texts,
            "encoding_format": "float",
        }
        if dimensions:
            payload["dimensions"] = dimensions

        response = self.session.post(
            f"{self.base_url}/embeddings",
            json=payload,
            timeout=120,
        )
        data = unwrap_response(response)
        return parse_embeddings_response(data, len(texts))

    def embed_texts_batch(
        self,
        model: str,
        items: Iterable[tuple[str, str]],
        dimensions: int | None,
        completion_window: str,
        poll_interval_seconds: int,
    ) -> dict[str, list[float]]:
        input_file = self._create_batch_input_file(model, items, dimensions)
        batch = self.create_batch(input_file["id"], "/v1/embeddings", completion_window)
        batch = self.wait_for_batch(batch["id"], poll_interval_seconds)

        output_file_id = batch.get("output_file_id")
        if not output_file_id:
            error_file_id = batch.get("error_file_id")
            error_text = self.download_file(error_file_id) if error_file_id else ""
            raise RuntimeError(f"OpenAI batch did not produce output_file_id: {error_text[:1000]}")

        return parse_batch_embeddings_output(self.download_file(output_file_id))

    def chat_structured(
        self,
        model: str,
        messages: list[dict],
        output_model,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        schema = output_model.model_json_schema()
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
                    "schema": schema,
                },
            },
        }
        response = self.session.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            timeout=120,
        )
        return unwrap_response(response)

    def create_batch(
        self,
        input_file_id: str,
        endpoint: str,
        completion_window: str,
    ) -> dict[str, Any]:
        response = self.session.post(
            f"{self.base_url}/batches",
            json={
                "input_file_id": input_file_id,
                "endpoint": endpoint,
                "completion_window": completion_window,
            },
            timeout=60,
        )
        return unwrap_response(response)

    def wait_for_batch(self, batch_id: str, poll_interval_seconds: int) -> dict[str, Any]:
        terminal_statuses = {"completed", "failed", "expired", "cancelled"}
        while True:
            batch = self.retrieve_batch(batch_id)
            status = str(batch.get("status", ""))
            if status in terminal_statuses:
                if status != "completed":
                    raise RuntimeError(f"OpenAI batch {batch_id} ended with status {status}: {batch}")
                return batch
            time.sleep(poll_interval_seconds)

    def retrieve_batch(self, batch_id: str) -> dict[str, Any]:
        response = self.session.get(f"{self.base_url}/batches/{batch_id}", timeout=60)
        return unwrap_response(response)

    def download_file(self, file_id: str) -> str:
        response = self.session.get(f"{self.base_url}/files/{file_id}/content", timeout=120)
        if not response.ok:
            raise RuntimeError(f"OpenAI file download failed ({response.status_code}): {response.text}")
        return response.text

    def _create_batch_input_file(
        self,
        model: str,
        items: Iterable[tuple[str, str]],
        dimensions: int | None,
    ) -> dict[str, Any]:
        with tempfile.NamedTemporaryFile("w+b", suffix=".jsonl") as f:
            for custom_id, text in items:
                body: dict[str, Any] = {
                    "model": model,
                    "input": text,
                    "encoding_format": "float",
                }
                if dimensions:
                    body["dimensions"] = dimensions
                request = {
                    "custom_id": custom_id,
                    "method": "POST",
                    "url": "/v1/embeddings",
                    "body": body,
                }
                f.write(json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n")
            f.flush()
            f.seek(0)
            response = self.session.post(
                f"{self.base_url}/files",
                data={"purpose": "batch"},
                files={"file": (Path(f.name).name, f.read(), "application/jsonl")},
                timeout=120,
            )
        return unwrap_response(response)


def parse_embeddings_response(data: dict[str, Any], expected_count: int) -> list[list[float]]:
    items = data.get("data")
    if not isinstance(items, list) or len(items) != expected_count:
        raise RuntimeError("OpenAI returned an unexpected embedding response")
    items = sorted(items, key=lambda item: int(item.get("index", 0)))
    embeddings = [item.get("embedding") for item in items]
    if not all(isinstance(embedding, list) for embedding in embeddings):
        raise RuntimeError("OpenAI embedding response is missing vectors")
    return embeddings


def parse_batch_embeddings_output(output: str) -> dict[str, list[float]]:
    embeddings: dict[str, list[float]] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        custom_id = str(item.get("custom_id", ""))
        error = item.get("error")
        if error:
            raise RuntimeError(f"OpenAI batch request failed for {custom_id}: {error}")

        response = item.get("response") or {}
        if int(response.get("status_code", 0)) >= 400:
            raise RuntimeError(f"OpenAI batch request failed for {custom_id}: {response}")

        body = response.get("body") or {}
        vectors = parse_embeddings_response(body, 1)
        embeddings[custom_id] = vectors[0]
    return embeddings


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
