"""The harness. Requirement 5: structured orchestration, not a raw prompt call.

A typed pipeline of stages with per-stage timeouts, declared fallbacks, and a
remaining-budget counter. Tracing comes from harness/trace.py, which was built
and tested in Phase 0 and is not reimplemented here.

The budget counter is the piece that makes 200ms a guarantee rather than an
average (Latency.md 4.1). Before each stage the runner asks whether that stage's
allocation still fits in what remains of the budget. If it does not, the stage is
skipped and its fallback runs instead: quality degrades, latency is protected,
and the skip is visible in the returned trace rather than silent.

Context is frozen. Each stage returns a NEW context rather than mutating one, so
a run is reconstructible from its trace - which is what makes a bad P100 sample
debuggable after the fact instead of merely observed.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .errors import RagCoreError, StageError, StageSkipped, StageTimeout
from .trace import Trace, span


class Context(BaseModel):
    """Immutable state threaded through the pipeline."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    query: str
    language: str = "auto"
    strategy: str = "c1"
    mode: str = "fast"
    trace: Trace
    # Populated progressively by stages. `data` holds intermediate artifacts
    # (query vector, candidates, answer) keyed by stage name.
    data: dict[str, Any] = Field(default_factory=dict)

    def with_data(self, **updates: Any) -> "Context":
        """Return a new Context carrying additional data."""
        merged = {**self.data, **updates}
        return self.model_copy(update={"data": merged})


class Stage(Protocol):
    """Every pipeline stage implements exactly this."""

    name: str
    timeout_ms: float
    budget_ms: float

    async def run(self, ctx: Context) -> Context: ...


class FunctionStage:
    """Adapts an async callable into a Stage.

    Most stages are a single function; this avoids a class per stage while
    keeping the declared timeout/budget/fallback contract explicit.
    """

    def __init__(
        self,
        name: str,
        fn: Callable[[Context], Awaitable[Context]],
        timeout_ms: float,
        budget_ms: float,
        fallback: Callable[[Context], Awaitable[Context]] | None = None,
        required: bool = False,
    ) -> None:
        self.name = name
        self.fn = fn
        self.timeout_ms = timeout_ms
        self.budget_ms = budget_ms
        self.fallback = fallback
        # A required stage has no meaningful degraded mode; if it fails the
        # request fails rather than returning something unfounded.
        self.required = required

    async def run(self, ctx: Context) -> Context:
        return await self.fn(ctx)


class Pipeline:
    """Runs stages in order under a shared budget."""

    def __init__(self, stages: list[FunctionStage], budget_ms: float) -> None:
        self.stages = stages
        self.budget_ms = budget_ms

    async def run(self, ctx: Context) -> Context:
        for stage in self.stages:
            ctx = await self._run_stage(stage, ctx)
        ctx.trace.finish()
        return ctx

    async def _run_stage(self, stage: FunctionStage, ctx: Context) -> Context:
        trace = ctx.trace

        # Budget gate. Checked BEFORE the span opens so a skipped stage costs
        # nothing measurable, which is the whole point of skipping it.
        if not stage.required and trace.remaining_ms < stage.budget_ms:
            trace.add_skipped(
                stage.name,
                detail=f"{trace.remaining_ms:.1f}ms left, needs {stage.budget_ms:.0f}ms",
            )
            return await self._fallback(stage, ctx, StageSkipped(stage.name, "budget"))

        with span(trace, stage.name) as sp:
            try:
                return await asyncio.wait_for(
                    stage.run(ctx), timeout=stage.timeout_ms / 1000.0
                )
            except asyncio.TimeoutError:
                sp.close(status="failed", detail="timeout")
                err: StageError = StageTimeout(stage.name, stage.timeout_ms)
            except StageError as exc:
                sp.close(status="failed", detail=type(exc).__name__)
                err = exc
            except RagCoreError:
                # Fatal by construction: no fallback can make the answer honest.
                sp.close(status="failed", detail="fatal")
                raise

        return await self._fallback(stage, ctx, err)

    async def _fallback(
        self, stage: FunctionStage, ctx: Context, err: StageError
    ) -> Context:
        """Graceful degradation. Architecture.md 6.2: never a 500 to the user."""
        if stage.required:
            raise err
        if stage.fallback is None:
            # No fallback declared: the stage contributes nothing and the
            # pipeline continues with the context unchanged.
            return ctx
        with span(ctx.trace, f"{stage.name}:fallback") as sp:
            sp.status = "fallback"
            return await stage.fallback(ctx)
