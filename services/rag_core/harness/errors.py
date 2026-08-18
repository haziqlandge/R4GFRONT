"""Typed error hierarchy. Rules.md 6: no bare `except`, every caught exception
is a named type from this module.

The distinction that matters is DEGRADABLE vs FATAL. A degradable failure has a
defined fallback and the request still returns an answer; a fatal one cannot.
Requirement 5 asks for error recovery, and recovery is only meaningful if the
code can tell the two apart - so the taxonomy encodes it rather than leaving it
to a comment.
"""

from __future__ import annotations


class RagCoreError(Exception):
    """Base. Never raised directly."""

    degradable: bool = False


# -- degradable: the pipeline continues on a fallback ------------------------


class StageError(RagCoreError):
    """A stage failed but the pipeline has somewhere to go."""

    degradable = True

    def __init__(self, stage: str, detail: str = "") -> None:
        self.stage = stage
        self.detail = detail
        super().__init__(f"{stage}: {detail}" if detail else stage)


class StageTimeout(StageError):
    """Stage exceeded its declared timeout_ms. Latency.md 4."""

    def __init__(self, stage: str, timeout_ms: float) -> None:
        self.timeout_ms = timeout_ms
        super().__init__(stage, f"exceeded {timeout_ms:.0f}ms")


class StageSkipped(StageError):
    """Stage could not fit in the remaining budget and was skipped.

    Not an error condition in the usual sense - it is the budget counter working
    as designed (Latency.md 4.1). It is modelled as an exception so that the
    fallback path is the same code path as a genuine failure.
    """


class UpstreamUnavailable(StageError):
    """A network dependency failed or its circuit breaker is open. Phase 5."""


# -- fatal: the request cannot be served -------------------------------------


class IndexNotReady(RagCoreError):
    """Index or model not loaded. Health check should have caught this.

    If this is ever raised in production it means /health passed before warmup
    finished, which is a startup-ordering bug, not a request-level one.
    """


class EmbedFailed(RagCoreError):
    """The embedder could not produce a vector. Without one there is no retrieval
    and no honest way to answer, so this is fatal rather than degradable."""


class InvalidQuery(RagCoreError):
    """Query failed structural validation. Returned as 4xx, not 5xx."""
