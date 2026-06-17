from __future__ import annotations

import json

from js import Object
from pyodide.ffi import to_js as _to_js


def to_js(value):
    return _to_js(value, dict_converter=Object.fromEntries)


def to_py(value):
    if value is None:
        return None

    converter = getattr(value, "to_py", None)
    if callable(converter):
        return converter()

    return value


def env_value(env, name: str, fallback: str = "") -> str:
    return str(to_py(getattr(env, name, None)) or fallback)


def parse_json(text: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
