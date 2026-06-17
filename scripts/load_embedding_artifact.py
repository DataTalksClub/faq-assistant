from __future__ import annotations

import argparse
import json
from pathlib import Path

from faq_assistant.cloudflare import CloudflareClient, batched
from faq_assistant.config import load_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--config", default="config.toml")
    args = parser.parse_args()

    config = load_config(args.config)
    batch_size = int(config["ingestion"]["batch_size"])
    cloudflare = CloudflareClient(config)

    with args.artifact.open(encoding="utf-8") as f:
        data = json.load(f)

    vectors = data["vectors"]
    submitted = 0
    for batch in batched(vectors, batch_size):
        cloudflare.upsert_vectors(batch)
        submitted += len(batch)

    print(json.dumps({
        "artifact": str(args.artifact),
        "submitted": submitted,
        "embedding_model": data.get("embedding_model"),
        "embedding_dimensions": data.get("embedding_dimensions"),
        "vectorize_index": config["cloudflare"]["vectorize"]["index_name"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
