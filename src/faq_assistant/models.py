from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field


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


class SearchResult(BaseModel):
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


class AnswerSource(BaseModel):
    id: str
    title: str
    source_type: str
    section: str = ""
    url: str = ""


class QueryRewrite(BaseModel):
    query: str = Field(description="Concise semantic search query used for retrieval")


class RagAnswer(BaseModel):
    answer: str = Field(description="Final answer for Slack, using only retrieved context")
    found_answer: bool = Field(description="Whether the retrieved context answers the question")
    sources: list[AnswerSource] = Field(default_factory=list)
