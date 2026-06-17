from __future__ import annotations

import argparse
import json

from faq_assistant.corpus import build_search_corpus


def main() -> None:
    parser = argparse.ArgumentParser(prog="faq-assistant")
    parser.add_argument("--config", default="config.toml")

    subparsers = parser.add_subparsers(dest="command", required=True)

    corpus_parser = subparsers.add_parser("corpus")
    corpus_subparsers = corpus_parser.add_subparsers(dest="corpus_command", required=True)
    corpus_subparsers.add_parser("build")

    args = parser.parse_args()
    if args.command == "corpus" and args.corpus_command == "build":
        result = build_search_corpus(args.config)
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
