"""Typed result objects returned by the tools.

Tools never raise: on failure they return a result with ok=False and a
human-readable `error`, so the orchestrator can decide what to do next.
These models describe tool output only; LLM output models live in
agent/schemas.py.
"""

from pydantic import BaseModel, Field


class SearchItem(BaseModel):
    title: str
    url: str
    snippet: str = Field(default="", max_length=500)
    score: float = Field(default=0.0, description="Search engine relevance score")
    suspicious: bool = Field(
        default=False,
        description="True if the title or snippet contains likely prompt-injection phrases",
    )
    injection_matches: list[str] = Field(
        default=[], description="The phrases that triggered `suspicious`"
    )


class SearchResult(BaseModel):
    query: str
    items: list[SearchItem] = []
    ok: bool
    error: str | None = None


class PageResult(BaseModel):
    url: str
    title: str = ""
    text: str = Field(default="", description="Extracted main text, truncated")
    truncated: bool = False
    suspicious: bool = Field(
        default=False,
        description="True if the text contains likely prompt-injection phrases",
    )
    injection_matches: list[str] = Field(
        default=[], description="The phrases that triggered `suspicious`"
    )
    ok: bool
    error: str | None = None
