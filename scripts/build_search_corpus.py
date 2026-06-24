from __future__ import annotations

import json
from pathlib import Path

from faq_assistant.corpus import build_search_corpus


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = ROOT / "artifacts" / "search" / "search-corpus.json"


def main() -> None:
    result = build_search_corpus(artifact_path=ARTIFACT_PATH)
    result["artifact"] = str(Path(result["artifact"]).relative_to(ROOT))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
