from __future__ import annotations

import argparse
import json
import time
from typing import Any

from faq_assistant.chunking import chunk_documents
from faq_assistant.config import load_config
from faq_assistant.ingest import save_embedding_artifact
from faq_assistant.openai import OpenAIClient, parse_batch_embeddings_output
from faq_assistant.sources import load_source_documents


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("batch_id")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--poll-interval-seconds", type=int, default=30)
    args = parser.parse_args()

    config = load_config(args.config)
    openai = OpenAIClient(config)
    batch = wait_for_completed_batch(
        openai=openai,
        batch_id=args.batch_id,
        wait=args.wait,
        poll_interval_seconds=args.poll_interval_seconds,
    )

    output_file_id = batch.get("output_file_id")
    if not output_file_id:
        raise RuntimeError(f"Batch {args.batch_id} has no output_file_id: {batch}")

    embeddings = parse_batch_embeddings_output(openai.download_file(str(output_file_id)))
    documents = load_source_documents(config)
    chunks = chunk_documents(documents, config)
    missing = [chunk.id for chunk in chunks if chunk.id not in embeddings]
    if missing:
        raise RuntimeError(f"Batch output is missing embeddings for {len(missing)} current chunks")

    vectors = [
        {"id": chunk.id, "values": embeddings[chunk.id], "metadata": chunk.metadata}
        for chunk in chunks
    ]
    path = save_embedding_artifact(
        config=config,
        vectors=vectors,
        documents_count=len(documents),
    )
    print(json.dumps({
        "batch_id": args.batch_id,
        "output_file_id": output_file_id,
        "documents": len(documents),
        "vectors": len(vectors),
        "embeddings_artifact": str(path),
        "usage": batch.get("usage"),
    }, indent=2, sort_keys=True))
    return 0


def wait_for_completed_batch(
    openai: OpenAIClient,
    batch_id: str,
    wait: bool,
    poll_interval_seconds: int,
) -> dict[str, Any]:
    terminal_statuses = {"completed", "failed", "expired", "cancelled"}
    while True:
        batch = openai.retrieve_batch(batch_id)
        status = str(batch.get("status", ""))
        if status == "completed":
            return batch
        if status in terminal_statuses:
            raise RuntimeError(f"Batch {batch_id} ended with status {status}: {batch}")
        if not wait:
            raise RuntimeError(f"Batch {batch_id} is not complete yet: {status}")
        time.sleep(poll_interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
