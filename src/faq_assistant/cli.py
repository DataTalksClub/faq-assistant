from __future__ import annotations

import argparse
import json

def main() -> None:
    parser = argparse.ArgumentParser(prog="faq-assistant")
    parser.add_argument("--config", default="config.toml")

    subparsers = parser.add_subparsers(dest="command", required=True)

    corpus_parser = subparsers.add_parser("corpus")
    corpus_subparsers = corpus_parser.add_subparsers(dest="corpus_command", required=True)
    corpus_subparsers.add_parser("build")

    index_parser = subparsers.add_parser("index")
    index_subparsers = index_parser.add_subparsers(dest="index_command", required=True)
    index_subparsers.add_parser("build")

    args = parser.parse_args()
    if args.command == "corpus" and args.corpus_command == "build":
        from faq_assistant.corpus import build_search_corpus  # needs the `ingest` group

        result = build_search_corpus(args.config)
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    if args.command == "index" and args.index_command == "build":
        from faq_assistant.search_index import build_search_index

        result = build_search_index()
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
