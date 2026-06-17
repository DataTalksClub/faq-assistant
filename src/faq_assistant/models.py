from __future__ import annotations

from dataclasses import MISSING, asdict, dataclass, field
from typing import Any, get_args, get_origin, get_type_hints


@dataclass(frozen=True)
class SourceDocument:
    source_type: str
    scope: str
    course: str | None
    course_name: str | None
    section: str
    title: str
    text: str
    url: str | None
    repo: str | None
    path: str | None
    source_id: str


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    metadata: dict


class StructuredModel:
    @classmethod
    def model_validate(cls, value):
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise ValueError(f"{cls.__name__} expects a JSON object")

        fields = getattr(cls, "__dataclass_fields__", {})
        type_hints = get_type_hints(cls)
        data: dict[str, Any] = {}
        for name, spec in fields.items():
            if name in value:
                raw = value[name]
            elif spec.default is not MISSING:
                raw = spec.default
            elif spec.default_factory is not MISSING:
                raw = spec.default_factory()
            else:
                raw = None
            data[name] = coerce_value(raw, type_hints[name])
        return cls(**data)

    def model_dump(self) -> dict:
        return asdict(self)

    @classmethod
    def model_json_schema(cls) -> dict:
        fields = getattr(cls, "__dataclass_fields__", {})
        type_hints = get_type_hints(cls)
        properties = {}
        required = []

        for name, spec in fields.items():
            properties[name] = schema_for_type(type_hints[name])
            required.append(name)

        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }


@dataclass
class SearchResult(StructuredModel):
    id: str = ""
    score: float = 0.0
    source_type: str = ""
    scope: str = ""
    course: str = ""
    section: str = ""
    title: str = ""
    text: str = ""
    url: str = ""
    repo: str = ""
    path: str = ""


@dataclass
class AnswerSource(StructuredModel):
    id: str = ""
    title: str = ""
    source_type: str = ""
    section: str = ""
    url: str = ""


@dataclass
class QueryRewrite(StructuredModel):
    query: str = ""


@dataclass
class RagAnswer(StructuredModel):
    answer: str = ""
    found_answer: bool = False
    sources: list[AnswerSource] = field(default_factory=list)


def coerce_value(value: Any, target_type):
    origin = get_origin(target_type)
    args = get_args(target_type)

    if origin is list:
        item_type = args[0] if args else Any
        if not isinstance(value, list):
            return []
        return [coerce_value(item, item_type) for item in value]

    if isinstance(target_type, type) and issubclass(target_type, StructuredModel):
        return target_type.model_validate(value)

    if target_type is bool:
        return bool(value)
    if target_type is float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    if target_type is int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    if target_type is str:
        return "" if value is None else str(value)

    return value


def schema_for_type(target_type) -> dict:
    origin = get_origin(target_type)
    args = get_args(target_type)

    if origin is list:
        item_type = args[0] if args else Any
        return {"type": "array", "items": schema_for_type(item_type)}

    if isinstance(target_type, type) and issubclass(target_type, StructuredModel):
        return target_type.model_json_schema()

    if target_type is bool:
        return {"type": "boolean"}
    if target_type is float:
        return {"type": "number"}
    if target_type is int:
        return {"type": "integer"}
    return {"type": "string"}
