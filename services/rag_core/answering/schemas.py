"""I/O contract. Architecture.md section 9, matched field for field.

This is fixed now, in Phase 2, even though most fields cannot be populated until
Phases 5 and 6. LatencyWaterfall.tsx, AbstentionPanel.tsx and CitationChip.tsx are
all built against this shape in Phase 8; a field renamed later is a frontend
rewrite under deadline. Unpopulated fields carry honest nulls rather than being
absent, so the frontend can be written once against the final shape.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AnswerStatus = Literal["ANSWERED", "ABSTAINED"]
AnswerPath = Literal["EXTRACTIVE", "GENERATIVE", "NONE"]
AbstainReason = Literal[
    "OFF_TOPIC",
    "LOW_CONFIDENCE",
    "UNSAFE_INPUT",
    "UNGROUNDED_OUTPUT",
    "AMBIGUOUS_RETRIEVAL",
]


class AnswerRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    language: str = "auto"
    strategy: str = "c1"
    trace: bool = True


class Citation(BaseModel):
    passage_id: str
    score: float
    text: str
    language: str


class Confidence(BaseModel):
    """The single calibrated signal behind both routing and abstention.

    Architecture.md 3.6: one mechanism serves requirement 3 (latency routing) and
    requirement 6 (knowing when not to answer). In Phase 2 there is no reranker,
    so `rerank_top1` carries the dense top-1 score and the rest are null.
    """

    rerank_top1: float | None = None
    score_gap: float | None = None
    groundedness: float | None = None


class StageSpan(BaseModel):
    name: str
    ms: float
    status: str
    detail: str | None = None


class TraceView(BaseModel):
    total_ms: float
    budget_ms: float
    stages: list[StageSpan]


class AnswerResponse(BaseModel):
    trace_id: str
    status: AnswerStatus
    path: AnswerPath
    answer: str | None = None
    abstain_reason: AbstainReason | None = None
    citations: list[Citation] = Field(default_factory=list)
    confidence: Confidence = Field(default_factory=Confidence)
    trace: TraceView | None = None
