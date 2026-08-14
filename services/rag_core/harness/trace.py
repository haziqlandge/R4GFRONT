"""Per-request tracing. The measurement primitive everything else depends on.

Rules.md section 2.2: every stage emits a span into the trace.
Latency.md section 6: monotonic clock only, captured at stage boundaries inside
the process. No wall clock, no datetime.

The serialized shape is fixed by Architecture.md section 9 so that
LatencyWaterfall.tsx can consume it unchanged in Phase 8.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from typing import Any, Final, Iterator, Literal

from pydantic import BaseModel, Field

SpanStatus = Literal["ok", "skipped", "fallback", "failed"]

_NS_PER_MS: Final[float] = 1_000_000.0


def new_trace_id() -> str:
    """A trace id. uuid4 hex, no dashes: shorter in logs and in the UI."""
    return uuid.uuid4().hex


class Span(BaseModel):
    """One stage's timing record.

    start_ns and end_ns come from time.perf_counter_ns(). They are monotonic and
    have no meaning as absolute times; only their difference is meaningful.
    """

    model_config = {"frozen": False}

    name: str
    start_ns: int
    end_ns: int | None = None
    status: SpanStatus = "ok"
    detail: str | None = None

    @property
    def ms(self) -> float:
        """Elapsed milliseconds. Zero for a span that never closed."""
        if self.end_ns is None:
            return 0.0
        return (self.end_ns - self.start_ns) / _NS_PER_MS

    def close(self, status: SpanStatus = "ok", detail: str | None = None) -> None:
        if self.end_ns is None:
            self.end_ns = time.perf_counter_ns()
        self.status = status
        self.detail = detail

    def serialize(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "ms": round(self.ms, 3),
            "status": self.status,
        }
        if self.detail is not None:
            out["detail"] = self.detail
        return out


class Trace(BaseModel):
    """Ordered spans for one request, plus the budget they were measured against."""

    model_config = {"frozen": False}

    trace_id: str = Field(default_factory=new_trace_id)
    budget_ms: float = 200.0
    started_ns: int = Field(default_factory=time.perf_counter_ns)
    ended_ns: int | None = None
    spans: list[Span] = Field(default_factory=list)

    @property
    def total_ms(self) -> float:
        """Wall time inside the measured band, not the sum of spans.

        These differ when stages overlap or when time is spent between them. The
        band total is the honest number and is what gets published.
        """
        end = self.ended_ns if self.ended_ns is not None else time.perf_counter_ns()
        return (end - self.started_ns) / _NS_PER_MS

    @property
    def spans_ms(self) -> float:
        """Sum of span durations. Compare against total_ms to find unattributed time."""
        return sum(s.ms for s in self.spans)

    @property
    def remaining_ms(self) -> float:
        """Budget left. Drives the skip decision in Latency.md section 4.1."""
        return self.budget_ms - self.total_ms

    def over_budget(self) -> bool:
        return self.total_ms > self.budget_ms

    def finish(self) -> None:
        if self.ended_ns is None:
            self.ended_ns = time.perf_counter_ns()

    def add_skipped(self, name: str, detail: str | None = None) -> Span:
        """Record a stage that never ran. Latency.md section 4.1.

        A skipped stage must appear in the trace, because the UI renders it as a
        hatched bar: visible absence, not invisible absence.
        """
        now = time.perf_counter_ns()
        span = Span(name=name, start_ns=now, end_ns=now, status="skipped", detail=detail)
        self.spans.append(span)
        return span

    def serialize(self) -> dict[str, Any]:
        """Exactly the `trace` object from Architecture.md section 9."""
        return {
            "total_ms": round(self.total_ms, 3),
            "budget_ms": self.budget_ms,
            "stages": [s.serialize() for s in self.spans],
        }


@contextmanager
def span(trace: Trace, name: str) -> Iterator[Span]:
    """Time a block and append its span to the trace.

    An exception closes the span as "failed" and propagates. The harness decides
    what to do about it; tracing never swallows errors.
    """
    s = Span(name=name, start_ns=time.perf_counter_ns())
    trace.spans.append(s)
    try:
        yield s
    except BaseException as exc:
        s.close(status="failed", detail=type(exc).__name__)
        raise
    else:
        if s.end_ns is None:
            s.close(status=s.status)
