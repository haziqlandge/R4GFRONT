"""Circuit breaker and call policy tests. Phase 5.

Rules.md 4 makes the breaker a HARD requirement: "Rate limits are handled in code,
not by hoping. A 429 opens the breaker and routes to extractive." These tests are
what makes that sentence true rather than aspirational, so the 429 case and the
no-retry-on-the-hot-path case are both pinned here deliberately.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.harness.errors import UpstreamUnavailable  # noqa: E402
from rag_core.harness.policies import (  # noqa: E402
    BreakerState,
    CircuitBreaker,
    RateLimited,
    call_with_policy,
)


def test_starts_closed_and_allows() -> None:
    b = CircuitBreaker("test")
    assert b.state == BreakerState.CLOSED
    assert b.allows()


def test_opens_only_after_threshold() -> None:
    """One failure is a blip; two is a pattern. A breaker that opens on the first
    transient error would disable the generative path for a whole demo."""
    b = CircuitBreaker("test", failure_threshold=2)
    b.record_failure()
    assert b.state == BreakerState.CLOSED, "one failure must not open it"
    b.record_failure()
    assert b.state == BreakerState.OPEN
    assert not b.allows()


def test_429_trips_immediately_without_counting() -> None:
    """Rules.md 4. A rate limit is not a transient error to count up to - it is
    upstream stating the answer for the rest of the window."""
    b = CircuitBreaker("test", failure_threshold=5)
    b.trip()
    assert b.state == BreakerState.OPEN
    assert not b.allows()


def test_success_resets_the_count() -> None:
    b = CircuitBreaker("test", failure_threshold=2)
    b.record_failure()
    b.record_success()
    b.record_failure()
    assert b.state == BreakerState.CLOSED


def test_half_open_admits_exactly_one_probe() -> None:
    """Without this guard a burst of concurrent requests all probe a still-broken
    upstream at once, which is the stampede the breaker exists to prevent."""
    b = CircuitBreaker("test", failure_threshold=1, recovery_seconds=0.0)
    b.record_failure()
    assert b.state == BreakerState.HALF_OPEN
    assert b.allows(), "first caller probes"
    assert not b.allows(), "second caller must be rejected while the probe is in flight"


def test_half_open_failure_reopens_for_a_full_window() -> None:
    """A failed probe must restart the recovery clock, not leave the breaker
    half-open and probeable on every subsequent request."""
    b = CircuitBreaker("test", failure_threshold=1, recovery_seconds=60.0)
    b.record_failure()
    assert b.state == BreakerState.OPEN

    b.recovery_seconds = 0.0  # simulate the window elapsing
    assert b.state == BreakerState.HALF_OPEN
    assert b.allows(), "the probe is admitted"

    b.recovery_seconds = 60.0  # the probe fails; a fresh window must open
    b.record_failure()
    assert b.state == BreakerState.OPEN
    assert not b.allows()


async def test_open_breaker_rejects_without_calling() -> None:
    """The point of the breaker: the second failure costs zero wall clock instead
    of another 352ms round trip against a 200ms budget."""
    b = CircuitBreaker("test", failure_threshold=1)
    b.trip()
    called = False

    async def fn() -> str:
        nonlocal called
        called = True
        return "never"

    with pytest.raises(UpstreamUnavailable):
        await call_with_policy(fn, b, timeout_ms=100.0)
    assert not called, "an open breaker must not touch the network"


async def test_success_passes_through_and_closes() -> None:
    b = CircuitBreaker("test")

    async def fn() -> str:
        return "ok"

    assert await call_with_policy(fn, b, timeout_ms=100.0) == "ok"
    assert b.state == BreakerState.CLOSED


async def test_timeout_becomes_degradable_error() -> None:
    """A hung upstream must surface as UpstreamUnavailable, which pipeline.py
    treats as degradable, so the extractive fallback still runs."""
    b = CircuitBreaker("test")

    async def slow() -> str:
        await asyncio.sleep(1.0)
        return "too late"

    with pytest.raises(UpstreamUnavailable):
        await call_with_policy(slow, b, timeout_ms=20.0)
    assert b.failures == 1


async def test_hot_path_default_is_zero_retries() -> None:
    """Latency.md 2: one Groq round trip has a measured 352ms floor against a
    200ms budget. A retry cannot make a call fit a budget it already missed - it
    only doubles the time spent discovering that."""
    b = CircuitBreaker("test", failure_threshold=99)
    attempts = 0

    async def fails() -> str:
        nonlocal attempts
        attempts += 1
        raise UpstreamUnavailable("test", "boom")

    with pytest.raises(UpstreamUnavailable):
        await call_with_policy(fails, b, timeout_ms=100.0)
    assert attempts == 1, "the hot path must not retry"


async def test_retries_are_available_for_offline_callers() -> None:
    b = CircuitBreaker("test", failure_threshold=99)
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise UpstreamUnavailable("test", "boom")
        return "ok"

    assert await call_with_policy(flaky, b, timeout_ms=100.0, retries=2) == "ok"
    assert attempts == 3


async def test_rate_limited_propagates_and_trips() -> None:
    b = CircuitBreaker("test", failure_threshold=99)

    async def limited() -> str:
        raise RateLimited("groq", "429")

    with pytest.raises(RateLimited):
        await call_with_policy(limited, b, timeout_ms=100.0)
    assert b.state == BreakerState.OPEN, "a 429 opens the breaker regardless of threshold"


def test_snapshot_exposes_state_for_the_trace() -> None:
    """The breaker must be visible in the trace rather than an invisible behaviour
    change - Phases.md 8 wants the fallback demonstrable on camera."""
    b = CircuitBreaker("groq")
    snap = b.snapshot()
    assert snap["name"] == "groq"
    assert snap["state"] == BreakerState.CLOSED
