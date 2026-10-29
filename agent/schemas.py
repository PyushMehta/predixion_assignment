"""
Output schemas for the AI Research Agent.

Every LLM output is validated against these models. If validation fails,
the orchestrator retries (with the error message) or falls back to a
low-confidence response -- it never returns unvalidated text.
"""

from enum import Enum
from typing import Literal
from pydantic import BaseModel, Field, HttpUrl, model_validator


# ---------- Planning ----------

class PlanStep(BaseModel):
    step_id: int
    goal: str = Field(description="What this step tries to find out")
    tool: Literal["web_search", "scrape_page", "none"]
    query: str | None = Field(default=None, description="Search query or URL, if a tool is used")


class Plan(BaseModel):
    question: str
    sub_questions: list[str] = Field(min_length=1, max_length=5)
    steps: list[PlanStep] = Field(min_length=1, max_length=8)  # cap = cost/latency control
    needs_full_pages: bool = Field(
        default=True,
        description="False when search snippets are enough (simple factual questions); "
                    "true for comparisons and research questions",
    )


# ---------- Sources ----------

class Source(BaseModel):
    id: str = Field(description="Short id like 'S1', used for citations")
    title: str
    url: HttpUrl
    snippet: str = Field(max_length=500)
    relevance: float = Field(ge=0, le=1, description="How relevant to the question (0-1)")


# ---------- Final answer ----------

class Finding(BaseModel):
    claim: str
    source_ids: list[str] = Field(min_length=1, description="Every claim must cite >= 1 source")


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class ResearchResponse(BaseModel):
    question: str
    short_answer: str = Field(max_length=1000)
    key_findings: list[Finding] = Field(
        default=[], description="May be empty only for a low-confidence answer (e.g. insufficient evidence)"
    )
    sources: list[Source]
    confidence: Confidence
    confidence_reason: str = Field(description="Why this confidence level")
    assumptions: list[str] = []
    limitations: list[str] = []
    next_steps: list[str] = []

    @model_validator(mode="after")
    def check_grounding(self):
        # Catch hallucinated citations: every cited id must exist in sources.
        valid_ids = {s.id for s in self.sources}
        for f in self.key_findings:
            missing = set(f.source_ids) - valid_ids
            if missing:
                raise ValueError(f"Finding cites unknown sources {missing}: {f.claim[:60]}")

        # Guardrail: "high" confidence needs at least 3 sources.
        if self.confidence == Confidence.high and len(self.sources) < 3:
            raise ValueError("High confidence requires at least 3 sources")

        # No findings is an honest answer only when confidence is low.
        if not self.key_findings and self.confidence != Confidence.low:
            raise ValueError("key_findings may be empty only when confidence is low")
        return self
