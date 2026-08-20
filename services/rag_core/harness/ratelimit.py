"""Per-client sliding-window rate limiter for the aside upstream. ISSUES.md I35.

This is NOT the circuit breaker and does not replace it. `policies.py` reacts to
an upstream that has already failed; this declines to make the call that would
fail it. Both are wanted, and the order matters: the limiter runs first, so a
client who has spent their window never touches the network and therefore never
records a failure against a breaker that is protecting everyone else.

WHY PER CLIENT. `rag_core` serves one public site from one free-tier Groq key,
and the aside spends the same 12,000-token window as the real generative fallback
(`ISSUES.md` I7). `DONT-FORGET.md` 13 records the failure this exists to prevent:
a visitor clicking repeatedly in accurate mode exhausts that shared window, and
then the panel stops appearing for every OTHER visitor and Band B degrades with
it. Bucketing by client turns a site-wide outage into one person's brief pause.

WHY A SLIDING WINDOW rather than the cheaper fixed bucket. A fixed 60-second
bucket admits `limit` calls at 0:59 and `limit` more at 1:01 - double the
intended rate across the boundary, arriving in two seconds. The whole point here
is to bound a burst, and the burst is exactly what a fixed bucket lets through.

WHY A PLAIN DICT AND NO LOCK. Same reasoning as `CircuitBreaker`: rag_core is one
async process on one event loop, and `allow()` contains no await, so it cannot be
interleaved. Note that the deployed box runs FOUR uvicorn workers
(`deploy/etc/shruti-core.service`), so the effective site-wide ceiling is
`limit x workers` per client. That is deliberate and recorded rather than fixed
with shared state: a Redis for a five-per-minute courtesy limit on a panel that
is allowed to not appear would be more moving parts than the problem has.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, Final

# Stop tracking a client this long after their last call. Without it the dict is
# an unbounded memory leak keyed by every IP that ever visited - slow, but a leak
# in a process that is meant to stay up for a judging window.
IDLE_EVICT_SECONDS: Final[float] = 300.0

# Only sweep when the table has grown past this, so the common case (a handful of
# visitors) never pays for the sweep at all.
SWEEP_ABOVE_KEYS: Final[int] = 512


class RateLimiter:
    """`limit` calls per `window_seconds`, per key, sliding.

    One instance per upstream. If a second one is ever added, give it its own
    limiter rather than sharing this: a shared window would let exhausting one
    upstream silently spend the allowance of the other.
    """

    def __init__(self, limit: int, window_seconds: float, name: str = "") -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.name = name
        self._hits: dict[str, Deque[float]] = {}

    def allow(self, key: str) -> bool:
        """Whether `key` may call right now, and RECORD it if so.

        Checking and recording are one operation on purpose. A separate
        `check()` then `record()` invites a caller to check, await the network,
        and record afterwards - by which time the window has moved and the count
        describes a different sixty seconds than the one that was checked.
        """
        now = time.monotonic()
        cutoff = now - self.window_seconds

        hits = self._hits.get(key)
        if hits is None:
            hits = deque()
            self._hits[key] = hits

        while hits and hits[0] <= cutoff:
            hits.popleft()

        if len(hits) >= self.limit:
            return False

        hits.append(now)
        if len(self._hits) > SWEEP_ABOVE_KEYS:
            self._evict_idle(now)
        return True

    def remaining(self, key: str) -> int:
        """Calls left in the current window. Read-only - never records a hit.

        For diagnostics and tests. Nothing on the request path calls this,
        because anything that did would be checking a number it then has to act
        on separately; see the note on `allow`.
        """
        hits = self._hits.get(key)
        if not hits:
            return self.limit
        cutoff = time.monotonic() - self.window_seconds
        live = sum(1 for t in hits if t > cutoff)
        return max(0, self.limit - live)

    def _evict_idle(self, now: float) -> None:
        stale = now - IDLE_EVICT_SECONDS
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= stale]:
            del self._hits[key]

    def snapshot(self) -> dict[str, object]:
        """For /health, so the limiter is visible rather than an invisible
        behaviour change - the same argument `CircuitBreaker.snapshot` makes."""
        return {
            "name": self.name,
            "limit": self.limit,
            "window_seconds": self.window_seconds,
            "tracked_clients": len(self._hits),
        }
