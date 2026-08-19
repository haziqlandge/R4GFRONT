"""Confidence to path routing. Architecture.md 3.6, Latency.md 3.2.

One calibrated signal drives two requirements. Requirement 3 asks the system to
meet a latency target; requirement 6 asks it to know when not to answer. Both are
answered by the same number - the cross-encoder's score for the best candidate:

    high      the top passage answers the question. Return it verbatim, make no
              network call, and finish inside the budget.
    moderate  something relevant was found but the top passage is not clearly the
              answer. Hand the top passages to Groq to compose from. This leaves
              the 200 ms budget by construction (352 ms measured floor) and is
              reported as Band B rather than hidden.
    low       nothing retrieved is good enough. Abstain, with a typed reason.

That this is ONE mechanism rather than two is the design's main economy, and it is
why the threshold work in Phase 5 also does most of Phase 6's job.

Why the reranker score and never the dense score: ISSUES.md I3. Dense cosine puts
a correct answer at 0.919 and pure gibberish at 0.862 - a 0.05 margin that cannot
carry a floor. A bi-encoder compares two embeddings that never met; a cross-encoder
reads the pair together. Routing on the dense score would either abstain on good
answers or accept nonsense.

Rules.md 6: no magic numbers on the hot path. The thresholds live in config.py with
a comment recording what calibrated them, and the calibration itself is
scripts/06_calibrate_routing.py against the dev partition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..config import ROUTE_TAU_HIGH, ROUTE_TAU_LOW
from .schemas import AbstainReason, AnswerPath

Decision = Literal["EXTRACTIVE", "GENERATIVE", "ABSTAIN"]


@dataclass(frozen=True)
class Route:
    """What to do, and why - the reason travels with the decision so the trace and
    the AbstentionPanel can show it rather than inferring it from the outcome."""

    decision: Decision
    reason: str
    abstain_reason: AbstainReason | None = None

    @property
    def path(self) -> AnswerPath:
        if self.decision == "EXTRACTIVE":
            return "EXTRACTIVE"
        if self.decision == "GENERATIVE":
            return "GENERATIVE"
        return "NONE"


def route(
    top1_score: float | None,
    score_gap: float | None = None,
    generative_available: bool = False,
    tau_low: float = ROUTE_TAU_LOW,
    tau_high: float = ROUTE_TAU_HIGH,
) -> Route:
    """Pick a path from the reranker's confidence.

    `generative_available` is false when there is no Groq key or the circuit
    breaker is open (Rules.md 4: a 429 opens the breaker and routes to extractive).
    In that case the middle band degrades to EXTRACTIVE rather than to ABSTAIN:
    a moderately-confident passage returned with its citation is more useful than a
    refusal, and the user can see the score. The degradation is recorded in the
    reason so it appears in the trace instead of looking like a normal fast path.
    """
    if top1_score is None:
        return Route(
            "ABSTAIN",
            "no candidates retrieved",
            abstain_reason="LOW_CONFIDENCE",
        )

    if top1_score < tau_low:
        return Route(
            "ABSTAIN",
            f"rerank top-1 {top1_score:.2f} below floor {tau_low:.2f}",
            abstain_reason="LOW_CONFIDENCE",
        )

    if top1_score >= tau_high:
        return Route("EXTRACTIVE", f"rerank top-1 {top1_score:.2f} >= {tau_high:.2f}")

    if not generative_available:
        return Route(
            "EXTRACTIVE",
            f"rerank top-1 {top1_score:.2f} in the fallback band, "
            "but the generative path is unavailable; degraded to extractive",
        )

    return Route(
        "GENERATIVE",
        f"rerank top-1 {top1_score:.2f} in [{tau_low:.2f}, {tau_high:.2f})",
    )
