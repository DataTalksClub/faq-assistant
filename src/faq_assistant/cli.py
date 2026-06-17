from __future__ import annotations

import argparse
import json

from faq_assistant.cloudflare import CloudflareClient
from faq_assistant.config import load_config
from faq_assistant.ingest import rebuild_index


def main() -> None:
    parser = argparse.ArgumentParser(prog="faq-assistant")
    parser.add_argument("--config", default="config.toml")

    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest")
    ingest_parser.add_argument("--mode", choices=["rebuild"], default="rebuild")
    ingest_parser.add_argument("--dry-run", action="store_true")

    index_parser = subparsers.add_parser("index")
    index_subparsers = index_parser.add_subparsers(dest="index_command", required=True)
    index_subparsers.add_parser("create")

    args = parser.parse_args()
    config = load_config(args.config)

    if args.command == "ingest":
        result = rebuild_index(config, dry_run=args.dry_run)
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    if args.command == "index" and args.index_command == "create":
        cloudflare = CloudflareClient(config)
        vectorize = config["cloudflare"]["vectorize"]
        result = {"index": cloudflare.create_vectorize_index(
            dimensions=int(vectorize["dimensions"]),
            metric=str(vectorize["metric"]),
        )}
        result["metadata_indexes"] = []
        for metadata_index in vectorize.get("metadata_indexes", []):
            result["metadata_indexes"].append(
                cloudflare.create_metadata_index(
                    property_name=metadata_index["property_name"],
                    index_type=metadata_index["type"],
                )
            )
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
