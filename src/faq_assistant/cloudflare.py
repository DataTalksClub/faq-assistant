from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import requests


class CloudflareClient:
    def __init__(self, config: dict[str, Any]):
        cloudflare = config["cloudflare"]
        self.account_id = required_env(cloudflare["account_id_env"])
        self.api_token = required_env(cloudflare["api_token_env"])
        self.index_name = cloudflare["vectorize"]["index_name"]
        self.base_url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}"
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.api_token}"})

    def run_ai(self, model: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(f"{self.base_url}/ai/run/{model}", json=payload, timeout=120)
        return unwrap_response(response)

    def embed_texts(self, model: str, texts: list[str]) -> list[list[float]]:
        result = self.run_ai(model, {"text": texts})
        data = result.get("data")
        if not isinstance(data, list) or len(data) != len(texts):
            raise RuntimeError("Workers AI returned an unexpected embedding response")
        return data

    def create_vectorize_index(self, dimensions: int, metric: str) -> dict[str, Any]:
        payload = {
            "name": self.index_name,
            "config": {"dimensions": dimensions, "metric": metric},
        }
        response = self.session.post(f"{self.base_url}/vectorize/v2/indexes", json=payload, timeout=60)
        if response.status_code == 409 or is_cloudflare_duplicate(response, "duplicate_name"):
            return {"already_exists": True, "name": self.index_name}
        return unwrap_response(response)

    def create_metadata_index(self, property_name: str, index_type: str) -> dict[str, Any]:
        payload = {"propertyName": property_name, "indexType": index_type}
        response = self.session.post(
            f"{self.base_url}/vectorize/v2/indexes/{self.index_name}/metadata_index/create",
            json=payload,
            timeout=60,
        )
        if response.status_code == 409 or is_cloudflare_duplicate(response, "already exists"):
            return {"already_exists": True, "property_name": property_name}
        return unwrap_response(response)

    def list_vector_ids(self, count: int = 1000) -> set[str]:
        ids: set[str] = set()
        cursor: str | None = None

        while True:
            params: dict[str, Any] = {"count": count}
            if cursor:
                params["cursor"] = cursor
            response = self.session.get(
                f"{self.base_url}/vectorize/v2/indexes/{self.index_name}/list",
                params=params,
                timeout=60,
            )
            result = unwrap_response(response)
            vectors = result.get("vectors") or result.get("items") or []

            for item in vectors:
                if isinstance(item, str):
                    ids.add(item)
                elif isinstance(item, dict) and item.get("id"):
                    ids.add(str(item["id"]))

            cursor = result.get("cursor") or result.get("next_cursor")
            if not cursor:
                return ids

    def upsert_vectors(self, vectors: Iterable[dict[str, Any]]) -> dict[str, Any]:
        return self._upload_ndjson("upsert", vectors)

    def delete_vectors(self, ids: list[str]) -> dict[str, Any]:
        if not ids:
            return {"count": 0, "ids": []}

        merged: dict[str, Any] = {"count": 0, "ids": []}
        for batch in batched(ids, 1000):
            response = self.session.post(
                f"{self.base_url}/vectorize/v2/indexes/{self.index_name}/delete_by_ids",
                json={"ids": batch},
                timeout=120,
            )
            result = unwrap_response(response)
            merged["count"] += int(result.get("count") or len(batch))
            merged["ids"].extend(result.get("ids") or batch)
        return merged

    def query_vectors(self, vector: list[float], options: dict[str, Any]) -> dict[str, Any]:
        payload = {"vector": vector, **options}
        response = self.session.post(
            f"{self.base_url}/vectorize/v2/indexes/{self.index_name}/query",
            json=payload,
            timeout=120,
        )
        return unwrap_response(response)

    def _upload_ndjson(self, operation: str, vectors: Iterable[dict[str, Any]]) -> dict[str, Any]:
        with tempfile.NamedTemporaryFile("w+b", suffix=".ndjson") as f:
            count = 0
            for vector in vectors:
                line = json.dumps(vector, ensure_ascii=False).encode("utf-8")
                f.write(line + b"\n")
                count += 1
            f.flush()
            f.seek(0)

            response = self.session.post(
                f"{self.base_url}/vectorize/v2/indexes/{self.index_name}/{operation}",
                files={"vectors": (Path(f.name).name, f.read(), "application/x-ndjson")},
                timeout=300,
            )

        result = unwrap_response(response)
        result.setdefault("submitted", count)
        return result


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def unwrap_response(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as e:
        raise RuntimeError(f"Cloudflare returned non-JSON response: {response.text[:500]}") from e

    if not response.ok or data.get("success") is False:
        errors = data.get("errors") or response.text
        raise RuntimeError(f"Cloudflare API request failed ({response.status_code}): {errors}")

    result = data.get("result")
    return result if isinstance(result, dict) else {"result": result}


def is_cloudflare_duplicate(response: requests.Response, marker: str) -> bool:
    try:
        data = response.json()
    except ValueError:
        return False

    errors = data.get("errors") or []
    marker = marker.lower()
    for error in errors:
        if marker in str(error.get("message", "")).lower():
            return True
    return False


def batched[T](items: list[T], size: int) -> Iterable[list[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
