from __future__ import annotations

from pathlib import PurePosixPath


def matches_any(path: str, patterns: list[str]) -> bool:
    if not patterns:
        return True

    normalized = path.strip("/")
    pure = PurePosixPath(normalized)

    for pattern in patterns:
        pat = pattern.strip("/")
        if pat.endswith("/**") and normalized.startswith(pat[:-3].rstrip("/") + "/"):
            return True
        if pure.match(pat):
            return True
        if pat.startswith("**/") and pure.match(pat[3:]):
            return True
        if "/" not in pat and pure.name == pat:
            return True

    return False
