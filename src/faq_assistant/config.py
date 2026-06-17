from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


def load_config(path: str | Path = "config.toml") -> dict[str, Any]:
    config_path = Path(path)
    with config_path.open("rb") as f:
        return tomllib.load(f)


def course_for_channel(config: dict[str, Any], channel_id: str) -> tuple[str, str | None]:
    channel = config.get("slack", {}).get("channels", {}).get(channel_id)
    if channel:
        return str(channel.get("scope", "docs")), channel.get("course")

    default_scope = str(config.get("slack", {}).get("default_scope", "docs"))
    return default_scope, None
