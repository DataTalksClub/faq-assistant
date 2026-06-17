from __future__ import annotations

import hashlib
from typing import Any

import gitsource

from faq_assistant.models import Chunk, SourceDocument


def chunk_documents(documents: list[SourceDocument], config: dict[str, Any]) -> list[Chunk]:
    chunk_config = config["ingestion"]["chunk"]
    max_chars = int(chunk_config["max_chars"])
    overlap_chars = int(chunk_config["overlap_chars"])
    text_limit = int(config["ingestion"]["metadata_text_max_chars"])

    chunks: list[Chunk] = []
    for doc in documents:
        if doc.source_type == "faq":
            parts = [doc.text.strip()]
        else:
            step = max(1, max_chars - overlap_chars)
            raw_chunks = gitsource.chunk_documents(
                [{"content": doc.text}],
                size=max_chars,
                step=step,
            )
            parts = [str(chunk["content"]).strip() for chunk in raw_chunks]

        for index, text in enumerate(parts):
            if not text:
                continue
            chunk_id = build_chunk_id(doc, index)
            metadata = {
                "id": chunk_id,
                "source_type": doc.source_type,
                "scope": doc.scope,
                "course": doc.course or "",
                "course_name": doc.course_name or "",
                "section": doc.section,
                "title": doc.title,
                "text": text[:text_limit],
                "url": doc.url or "",
                "repo": doc.repo or "",
                "path": doc.path or "",
                "source_id": doc.source_id,
                "chunk_index": index,
            }
            chunks.append(Chunk(id=chunk_id, text=text, metadata=metadata))
    return chunks


def build_chunk_id(doc: SourceDocument, chunk_index: int) -> str:
    if doc.source_type == "faq":
        key = f"{doc.source_type}:{doc.course}:{doc.source_id}"
    elif doc.scope == "course":
        key = f"{doc.source_type}:{doc.course}:{doc.source_id}"
    else:
        key = f"{doc.source_type}:{doc.source_id}"

    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return f"{doc.source_type}:{digest}:{chunk_index:04d}"
