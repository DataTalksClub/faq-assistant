from __future__ import annotations

from typing import Any

from faq_assistant.chunking import chunk_documents
from faq_assistant.cloudflare import CloudflareClient, batched
from faq_assistant.models import Chunk
from faq_assistant.sources import load_source_documents


def rebuild_index(config: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    ai_config = config["cloudflare"]["ai"]
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

    for batch in batched(chunks, batch_size):
        vectors = build_vectors(
            cloudflare=cloudflare,
            embedding_model=ai_config["embedding_model"],
            chunks=batch,
        )
        cloudflare.upsert_vectors(vectors)

    stale_ids = sorted(old_ids - new_ids)
    cloudflare.delete_vectors(stale_ids)

    return {
        "index_name": vectorize_config["index_name"],
        "documents": len(documents),
        "chunks": len(chunks),
        "old_ids": len(old_ids),
        "new_ids": len(new_ids),
        "stale_ids": len(stale_ids),
    }


def build_vectors(
    cloudflare: CloudflareClient,
    embedding_model: str,
    chunks: list[Chunk],
) -> list[dict[str, Any]]:
    texts = [chunk.text for chunk in chunks]
    embeddings = cloudflare.embed_texts(embedding_model, texts)
    return [
        {"id": chunk.id, "values": embedding, "metadata": chunk.metadata}
        for chunk, embedding in zip(chunks, embeddings, strict=True)
    ]
