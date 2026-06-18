import json
from pathlib import Path

from faq_assistant.search_index import build_search_index


ROOT = Path(__file__).resolve().parents[1]
CORPUS_ARTIFACT = ROOT / "artifacts" / "search" / "search-corpus.json"
INDEX_ARTIFACT = ROOT / "artifacts" / "search" / "search-index.zsx"


def main() -> None:
    result = build_search_index(corpus_artifact=CORPUS_ARTIFACT, index_artifact=INDEX_ARTIFACT)
    result["index"] = str(Path(result["index"]).relative_to(ROOT))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
