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


AnswerMode = Literal["fast", "accurate"]


class AnswerRequest(BaseModel):
    """Request contract. Architecture.md 9.

    `mode` is D2's recorded reversal condition, made real. Assumption A6 -
    "extractive answers are good enough to be the default" - measured false in
    Phase 5: reranked top-1 is the correct passage only ~40% of the time, and D2
    said that if this happened, keep the fast path but expose it as a mode rather
    than silently defaulting to it.

    fast      never calls the network. Extractive above the abstention floor.
              Band A, ~60 ms.
    accurate  lets the router send the middle confidence band to the LLM.
              Band B, ~650 ms, and outside the 200 ms budget by construction.

    The default is "fast" because that is the configuration the 200 ms claim is
    measured on, and because Groq's free tier serves roughly 12 calls per window
    (ISSUES.md I7) - defaulting to a path that cannot serve traffic would be a
    worse dishonesty than defaulting to a fast one whose limits are published.
    """

    query: str = Field(min_length=1, max_length=2000)
    language: str = "auto"
    strategy: str = "c1"
    mode: AnswerMode = "fast"
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
