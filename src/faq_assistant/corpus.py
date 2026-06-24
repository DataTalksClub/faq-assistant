from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from faq_assistant.chunking import chunk_documents
from faq_assistant.config import load_config
from faq_assistant.sources import load_source_documents


def build_search_corpus(
    config_path: str | Path = "config.toml",
    artifact_path: str | Path = "artifacts/search/search-corpus.json",
) -> dict[str, Any]:
    config = load_config(config_path)
    documents = load_source_documents(config)
    chunks = chunk_documents(documents, config)

    records = []
    for chunk in chunks:
        metadata = chunk.metadata
        records.append(
            {
                "id": chunk.id,
                "source_type": str(metadata.get("source_type", "")),
                "scope": str(metadata.get("scope", "")),
                "course": str(metadata.get("course", "")),
                "section": str(metadata.get("section", "")),
                "title": str(metadata.get("title", "")),
                "text": chunk.text,
                "url": str(metadata.get("url", "")),
                "repo": str(metadata.get("repo", "")),
                "path": str(metadata.get("path", "")),
            }
        )

    raw = json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    artifact = Path(artifact_path)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(raw + b"\n")

    return {
        "documents": len(documents),
        "chunks": len(chunks),
        "raw_json_mb": round(len(raw) / 1024 / 1024, 2),
        "artifact": str(artifact),
    }
