"""Harness tests: the budget counter, timeouts, and graceful degradation.

These cover the mechanism Latency.md 4.1 calls the reason 200ms is a guarantee
rather than an average, so they are worth more than their line count suggests.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.harness.errors import (  # noqa: E402
    EmbedFailed,
    StageError,
    StageTimeout,
)
from rag_core.harness.pipeline import Context, FunctionStage, Pipeline  # noqa: E402
from rag_core.harness.trace import Trace  # noqa: E402


def ctx(budget_ms: float = 200.0) -> Context:
    return Context(query="q", trace=Trace(budget_ms=budget_ms))


def stage(name: str, fn, *, timeout_ms=50.0, budget_ms=10.0, fallback=None, required=False):
    return FunctionStage(
        name, fn, timeout_ms=timeout_ms, budget_ms=budget_ms,
        fallback=fallback, required=required,
    )


async def ok(c: Context) -> Context:
    return c.with_data(ran=c.data.get("ran", 0) + 1)


# -- context ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_is_immutable_and_forks() -> None:
    a = ctx()
    b = a.with_data(x=1)
    assert a.data == {} and b.data == {"x": 1}
    with pytest.raises(Exception):
        a.query = "other"  # type: ignore[misc]


# -- happy path -------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_stages_run_and_emit_spans() -> None:
    p = Pipeline([stage("a", ok), stage("b", ok)], budget_ms=200.0)
    out = await p.run(ctx())
    assert out.data["ran"] == 2
    assert [s.name for s in out.trace.spans] == ["a", "b"]
    assert all(s.status == "ok" for s in out.trace.spans)


# -- the budget counter -----------------------------------------------------


@pytest.mark.asyncio
async def test_stage_skipped_when_budget_exhausted() -> None:
    """The mechanism from Latency.md 4.1: quality degrades, latency is protected,
    and the skip is visible rather than silent."""
    async def slow(c: Context) -> Context:
        await asyncio.sleep(0.05)
        return c

    p = Pipeline(
        [
            stage("slow", slow, timeout_ms=500.0, budget_ms=5.0),
            stage("expensive", ok, budget_ms=60.0),
        ],
        budget_ms=40.0,  # slow eats it, expensive cannot fit
    )
    out = await p.run(ctx(budget_ms=40.0))
    names = {s.name: s for s in out.trace.spans}
    assert names["expensive"].status == "skipped"
    assert "left" in (names["expensive"].detail or "")
    assert "ran" not in out.data  # the skipped stage genuinely did not run


@pytest.mark.asyncio
async def test_skipped_stage_runs_its_fallback() -> None:
    async def fb(c: Context) -> Context:
        return c.with_data(via_fallback=True)

    p = Pipeline(
        [stage("rerank", ok, budget_ms=999.0, fallback=fb)], budget_ms=10.0
    )
    out = await p.run(ctx(budget_ms=10.0))
    assert out.data["via_fallback"] is True
    assert [s.status for s in out.trace.spans] == ["skipped", "fallback"]


@pytest.mark.asyncio
async def test_required_stage_is_never_skipped_for_budget() -> None:
    """embed_query is required: degrading it would mean inventing a vector."""
    p = Pipeline([stage("embed", ok, budget_ms=999.0, required=True)], budget_ms=1.0)
    out = await p.run(ctx(budget_ms=1.0))
    assert out.data["ran"] == 1


# -- timeouts and degradation -----------------------------------------------


@pytest.mark.asyncio
async def test_timeout_marks_span_failed_and_uses_fallback() -> None:
    async def hangs(c: Context) -> Context:
        await asyncio.sleep(1.0)
        return c

    async def fb(c: Context) -> Context:
        return c.with_data(degraded=True)

    p = Pipeline([stage("hangs", hangs, timeout_ms=20.0, fallback=fb)], budget_ms=200.0)
    out = await p.run(ctx())
    assert out.data["degraded"] is True
    assert out.trace.spans[0].status == "failed"
    assert out.trace.spans[0].detail == "timeout"


@pytest.mark.asyncio
async def test_stage_error_without_fallback_continues_unchanged() -> None:
    """Architecture.md 6.2: never a 500 to the user for a degradable failure."""
    async def boom(c: Context) -> Context:
        raise StageError("boom", "index cold")

    p = Pipeline([stage("boom", boom), stage("after", ok)], budget_ms=200.0)
    out = await p.run(ctx())
    assert out.data["ran"] == 1  # 'after' still ran
    assert out.trace.spans[0].status == "failed"


@pytest.mark.asyncio
async def test_required_stage_failure_propagates() -> None:
    async def boom(c: Context) -> Context:
        raise StageTimeout("embed", 30.0)

    p = Pipeline([stage("embed", boom, required=True)], budget_ms=200.0)
    with pytest.raises(StageTimeout):
        await p.run(ctx())


@pytest.mark.asyncio
async def test_fatal_error_is_not_degraded() -> None:
    """A fatal error must not be quietly swallowed into a plausible answer."""
    async def boom(c: Context) -> Context:
        raise EmbedFailed("onnx session gone")

    p = Pipeline([stage("embed", boom)], budget_ms=200.0)
    with pytest.raises(EmbedFailed):
        await p.run(ctx())


@pytest.mark.asyncio
async def test_trace_is_finished_and_serializable() -> None:
    p = Pipeline([stage("a", ok)], budget_ms=200.0)
    out = await p.run(ctx())
    assert out.trace.ended_ns is not None
    payload = out.trace.serialize()
    assert set(payload) == {"total_ms", "budget_ms", "stages"}
