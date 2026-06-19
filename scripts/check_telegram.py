"""Inspect the Telegram source loader without rebuilding the corpus.

Prints per-channel post counts (within the lookback window) and a few sample
documents so data quality can be eyeballed before wiring into a full build.

    .venv/bin/python scripts/check_telegram.py [course ...]
"""

from __future__ import annotations

import sys

from faq_assistant.config import load_config
from faq_assistant.sources import load_telegram_documents


def main() -> None:
    config = load_config()
    only = set(sys.argv[1:])

    if only:
        config["courses"] = {c: v for c, v in config["courses"].items() if c in only}

    documents = load_telegram_documents(config)

    by_course: dict[str, list] = {}
    for doc in documents:
        by_course.setdefault(doc.course or "", []).append(doc)

    print(f"Loaded {len(documents)} Telegram posts across {len(by_course)} channels\n")
    for course, docs in sorted(by_course.items()):
        channel = config["courses"][course].get("telegram_channel", "?")
        print(f"  {course} (@{channel}): {len(docs)} posts")
    print()

    for course, docs in sorted(by_course.items()):
        print("=" * 80)
        print(f"{course} — first 2 of {len(docs)} posts")
        print("=" * 80)
        for doc in docs[:2]:
            print(f"\n[{doc.source_id}] {doc.url}")
            print(f"  title: {doc.title}")
            body = doc.text if len(doc.text) <= 500 else doc.text[:500] + " …"
            print("  text:")
            for line in body.splitlines():
                print(f"    {line}")
        print()


if __name__ == "__main__":
    main()
