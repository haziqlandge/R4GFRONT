"""Retry, timeout and circuit breaker for the network fallback path. Phase 5.

The remaining-budget counter already lives in harness/pipeline.py, where it belongs
- it is a property of running a pipeline, not of talking to a network. What is here
is everything needed to make ONE unreliable dependency (Groq) safe to depend on.

Rules.md 4 is HARD: "Rate limits are handled in code, not by hoping. Groq client
wraps every call in the circuit breaker. A 429 opens the breaker and routes to
extractive."

Why a breaker rather than retries alone. ISSUES.md I7 measured the Groq free tier
at 12,000 tokens per window, and the fallback path is entirely inside that quota.
When it runs out, every subsequent call fails the same way - so retrying is not
merely useless, it is actively harmful: each attempt costs the wall-clock of a
network round trip (352 ms measured floor, Memory.md 14 Aug) against a 200 ms
budget, and retrying a quota failure produces N times the latency for the same
inevitable outcome. The breaker converts the second and later failures from a
timeout into an instant, free rejection, which is what lets the pipeline fall back
to extractive while still inside its budget.

This also makes the failure demonstrable rather than theoretical. Phases.md 8 asks
for a failure-injection mode so Video 2 can show the breaker opening live; because
the breaker is a plain object with an explicit state machine, that demo is a real
429 producing a real state transition, not a mock.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Final, TypeVar

from .errors import UpstreamUnavailable

T = TypeVar("T")

# Consecutive failures before the breaker opens. Two rather than one: a single
# transient network blip should not disable the generative path for the rest of a
# demo, but a second consecutive failure is a pattern and not noise.
FAILURE_THRESHOLD: Final[int] = 2

# How long the breaker stays open before allowing one probe through. Groq's free
# tier resets on a rolling window; this is deliberately short enough that a demo
# recovers on its own during a recording rather than needing a restart.
RECOVERY_SECONDS: Final[float] = 30.0


class BreakerState:
    CLOSED = "closed"      # calls pass through
    OPEN = "open"          # calls rejected instantly, no network touched
    HALF_OPEN = "half"     # one probe allowed; success closes, failure re-opens


@dataclass
class CircuitBreaker:
    """One breaker per upstream. Not thread-safe by design: rag_core is one async
    process on one event loop, and adding a lock would buy nothing but contention.
    """

    name: str
    failure_threshold: int = FAILURE_THRESHOLD
    recovery_seconds: float = RECOVERY_SECONDS

    failures: int = 0
    opened_at: float | None = None
    _half_open_in_flight: bool = field(default=False, repr=False)

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return BreakerState.CLOSED
        if time.monotonic() - self.opened_at >= self.recovery_seconds:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    def allows(self) -> bool:
        """Whether a call may proceed right now.

        In HALF_OPEN exactly one probe is admitted. Without that guard a burst of
        concurrent requests would all probe a still-broken upstream at once, which
        is the stampede the breaker exists to prevent.
        """
        state = self.state
        if state == BreakerState.CLOSED:
            return True
        if state == BreakerState.HALF_OPEN and not self._half_open_in_flight:
            self._half_open_in_flight = True
            return True
        return False

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None
        self._half_open_in_flight = False

    def record_failure(self) -> None:
        self._half_open_in_flight = False
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.monotonic()

    def trip(self) -> None:
        """Open immediately, without waiting for the threshold.

        Used for a 429: a rate limit is not a transient error to be counted up to,
        it is upstream stating the answer for the whole window. Rules.md 4.
        """
        self.failures = self.failure_threshold
        self.opened_at = time.monotonic()
        self._half_open_in_flight = False

    def snapshot(self) -> dict[str, object]:
        """For the trace and the /health payload, so the breaker is visible rather
        than an invisible behaviour change."""
        return {
            "name": self.name,
            "state": self.state,
            "failures": self.failures,
        }


class RateLimited(UpstreamUnavailable):
    """Upstream returned 429. Degradable: the extractive path is always available."""


async def call_with_policy(
    fn: Callable[[], Awaitable[T]],
    breaker: CircuitBreaker,
    timeout_ms: float,
    retries: int = 0,
) -> T:
    """Run `fn` under a breaker, a hard timeout, and an optional retry.

    `retries` defaults to ZERO, which looks wrong for a resilience helper and is
    deliberate. Latency.md 2: one Groq round trip has a measured 352 ms floor, and
    the whole budget is 200 ms. A retry cannot make a call fit a budget it already
    missed - it can only double the time spent discovering that. Retries are
    available for a caller with slack (an offline script), and the hot path passes
    zero and falls back instead.

    Raises UpstreamUnavailable, which harness/pipeline.py treats as degradable, so
    the stage's declared fallback runs and the user still gets an answer.
    """
    if not breaker.allows():
        raise UpstreamUnavailable(breaker.name, f"circuit {breaker.state}")

    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            result = await asyncio.wait_for(fn(), timeout=timeout_ms / 1000.0)
        except asyncio.TimeoutError as exc:
            last = exc
            breaker.record_failure()
        except RateLimited:
            breaker.trip()  # not worth counting to a threshold
            raise
        except UpstreamUnavailable as exc:
            last = exc
            breaker.record_failure()
        else:
            breaker.record_success()
            return result

        if attempt < retries and breaker.allows():
            continue
        break

    raise UpstreamUnavailable(breaker.name, f"failed after {retries + 1} attempt(s): {last}")
