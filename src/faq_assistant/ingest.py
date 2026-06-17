from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from faq_assistant.chunking import chunk_documents
from faq_assistant.cloudflare import CloudflareClient, batched
from faq_assistant.models import Chunk
from faq_assistant.openai import OpenAIClient
from faq_assistant.sources import load_source_documents


def rebuild_index(config: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    embedding_config = config["embeddings"]
    vectorize_config = config["cloudflare"]["vectorize"]
    batch_size = int(config["ingestion"]["batch_size"])

    documents = load_source_documents(config)
    chunks = chunk_documents(documents, config)
    new_ids = {chunk.id for chunk in chunks}
    old_ids: set[str] = set()

    if dry_run:
        return {
            "documents": len(documents),
            "chunks": len(chunks),
            "old_ids": len(old_ids),
            "new_ids": len(new_ids),
            "stale_ids": 0,
        }

    cloudflare = CloudflareClient(config)
    old_ids = cloudflare.list_vector_ids()

    vectors: list[dict[str, Any]] = []
    if embedding_config["provider"] == "openai" and embedding_config.get("batch_enabled", False):
        vectors = build_vectors_openai_batch(
            openai=OpenAIClient(config),
            embedding_config=embedding_config,
            chunks=chunks,
        )
    else:
        for batch in batched(chunks, batch_size):
            vectors.extend(
                build_vectors(
                    openai=OpenAIClient(config),
                    embedding_config=embedding_config,
                    chunks=batch,
                )
            )

    artifact_path = save_embedding_artifact(
        config=config,
        vectors=vectors,
        documents_count=len(documents),
    )

    for batch in batched(vectors, batch_size):
        cloudflare.upsert_vectors(batch)

    stale_ids = sorted(old_ids - new_ids)
    cloudflare.delete_vectors(stale_ids)

    return {
        "index_name": vectorize_config["index_name"],
        "documents": len(documents),
        "chunks": len(chunks),
        "old_ids": len(old_ids),
        "new_ids": len(new_ids),
        "stale_ids": len(stale_ids),
        "embedding_provider": embedding_config["provider"],
        "embedding_model": embedding_config["model"],
        "embedding_dimensions": int(embedding_config["dimensions"]),
        "embeddings_artifact": str(artifact_path),
    }


def build_vectors(
    openai: OpenAIClient,
    embedding_config: dict[str, Any],
    chunks: list[Chunk],
) -> list[dict[str, Any]]:
    if embedding_config["provider"] != "openai":
        raise RuntimeError(f"Unsupported embedding provider: {embedding_config['provider']}")

    texts = [chunk.text for chunk in chunks]
    embeddings = openai.embed_texts(
        model=embedding_config["model"],
        texts=texts,
        dimensions=embedding_dimensions_parameter(embedding_config),
    )
    return [
        {"id": chunk.id, "values": embedding, "metadata": chunk.metadata}
        for chunk, embedding in zip(chunks, embeddings, strict=True)
    ]


def build_vectors_openai_batch(
    openai: OpenAIClient,
    embedding_config: dict[str, Any],
    chunks: list[Chunk],
) -> list[dict[str, Any]]:
    embeddings = openai.embed_texts_batch(
        model=embedding_config["model"],
        items=((chunk.id, chunk.text) for chunk in chunks),
        dimensions=embedding_dimensions_parameter(embedding_config),
        completion_window=str(embedding_config.get("batch_completion_window", "24h")),
        poll_interval_seconds=int(embedding_config.get("batch_poll_interval_seconds", 30)),
    )

    missing = [chunk.id for chunk in chunks if chunk.id not in embeddings]
    if missing:
        raise RuntimeError(f"OpenAI batch did not return embeddings for {len(missing)} chunks")

    return [
        {"id": chunk.id, "values": embeddings[chunk.id], "metadata": chunk.metadata}
        for chunk in chunks
    ]


def embedding_dimensions_parameter(embedding_config: dict[str, Any]) -> int | None:
    if not embedding_config.get("use_dimensions_parameter", False):
        return None
    return int(embedding_config["dimensions"])


def save_embedding_artifact(
    config: dict[str, Any],
    vectors: list[dict[str, Any]],
    documents_count: int,
) -> Path:
    embedding_config = config["embeddings"]
    vectorize_config = config["cloudflare"]["vectorize"]
    artifact_config = config.get("artifacts", {})
    embeddings_dir = Path(str(artifact_config.get("embeddings_dir", "artifacts/embeddings")))
    dimensions = int(embedding_config["dimensions"])
    model = str(embedding_config["model"])
    provider = str(embedding_config["provider"])
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    output_dir = embeddings_dir / f"{sanitize_path_part(provider)}-{sanitize_path_part(model)}-{dimensions}"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{timestamp}.json"

    payload = {
        "created_at": timestamp,
        "embedding_provider": provider,
        "embedding_model": model,
        "embedding_dimensions": dimensions,
        "vectorize_index": vectorize_config["index_name"],
        "documents_count": documents_count,
        "vectors_count": len(vectors),
        "vectors": vectors,
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")

    latest_path = output_dir / "latest.json"
    with latest_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")

    return path


def sanitize_path_part(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value)
