"""The per-client aside limiter. ISSUES.md I35.

The behaviours pinned here are the ones that would fail silently in production:
a limiter that leaks between clients, a window that never reopens, and the fixed
-bucket boundary burst that is the reason this is a sliding window at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from rag_core.config import ASIDE_RATE_LIMIT, ASIDE_RATE_WINDOW_SECONDS  # noqa: E402
from rag_core.harness.ratelimit import RateLimiter  # noqa: E402


def test_allows_exactly_the_limit_then_refuses() -> None:
    rl = RateLimiter(limit=5, window_seconds=60.0, name="t")
    assert [rl.allow("1.2.3.4") for _ in range(5)] == [True] * 5
    assert rl.allow("1.2.3.4") is False


def test_clients_do_not_share_a_window() -> None:
    """The whole point of bucketing per client: one visitor exhausting their
    window must not stop the next visitor's panel from appearing."""
    rl = RateLimiter(limit=2, window_seconds=60.0)
    assert rl.allow("a") and rl.allow("a")
    assert rl.allow("a") is False
    assert rl.allow("b") is True


def test_window_reopens_as_it_slides(monkeypatch) -> None:
    """Sliding, not fixed. Calls fall out of the window one at a time as they
    age past it, so capacity returns gradually rather than all at once."""
    now = [1000.0]
    monkeypatch.setattr("rag_core.harness.ratelimit.time.monotonic", lambda: now[0])

    rl = RateLimiter(limit=2, window_seconds=60.0)
    assert rl.allow("a") is True          # t=1000
    now[0] = 1030.0
    assert rl.allow("a") is True          # t=1030
    assert rl.allow("a") is False         # both still inside the window

    now[0] = 1061.0                        # the t=1000 call has aged out
    assert rl.allow("a") is True
    assert rl.allow("a") is False          # t=1030 and t=1061 still inside


def test_no_boundary_burst(monkeypatch) -> None:
    """The failure a fixed bucket has and this does not.

    A fixed 60-second bucket admits `limit` calls at 0:59 and `limit` more at
    1:01 - twice the intended rate, arriving two seconds apart. Bounding that
    burst is the reason for the deque.
    """
    now = [0.0]
    monkeypatch.setattr("rag_core.harness.ratelimit.time.monotonic", lambda: now[0])

    rl = RateLimiter(limit=5, window_seconds=60.0)
    now[0] = 59.0
    assert all(rl.allow("a") for _ in range(5))

    now[0] = 61.0                          # a fixed bucket would have reset here
    assert rl.allow("a") is False, "5 more calls got through 2 seconds later"

    # A REFUSED call is not recorded, so the window is still the five at t=59 and
    # it reopens 60 seconds after THEM rather than 60 seconds after the attempt.
    now[0] = 118.9                         # 59.0 is still inside the window
    assert rl.allow("a") is False
    now[0] = 119.1                         # 59.0 has now aged out
    assert rl.allow("a") is True


def test_remaining_does_not_consume() -> None:
    rl = RateLimiter(limit=3, window_seconds=60.0)
    assert rl.remaining("a") == 3
    assert rl.remaining("a") == 3, "reading the counter spent a call"
    rl.allow("a")
    assert rl.remaining("a") == 2


def test_idle_clients_are_evicted(monkeypatch) -> None:
    """Without this the table is an unbounded leak keyed by every IP that ever
    visited, in a process meant to stay up for a judging window."""
    now = [0.0]
    monkeypatch.setattr("rag_core.harness.ratelimit.time.monotonic", lambda: now[0])
    monkeypatch.setattr("rag_core.harness.ratelimit.SWEEP_ABOVE_KEYS", 10)

    rl = RateLimiter(limit=5, window_seconds=60.0)
    for i in range(20):
        rl.allow(f"client-{i}")
    assert rl.snapshot()["tracked_clients"] > 1

    now[0] = 10_000.0                      # everyone is long idle
    rl.allow("fresh")
    assert rl.snapshot()["tracked_clients"] == 1


def test_zero_disables_the_limiter() -> None:
    """0 is the off switch, and it must mean "admit everything" rather than
    "refuse everything" - a limiter that admits nothing is a broken endpoint,
    not a policy."""
    rl = RateLimiter(limit=0, window_seconds=60.0)
    assert all(rl.allow("a") for _ in range(50))


def test_shipped_config() -> None:
    """15 per minute per client. Loose enough that a judge working through the
    sample questions never meets it, tight enough that a script cannot drain the
    shared token window in seconds."""
    assert ASIDE_RATE_LIMIT == 15
    assert ASIDE_RATE_WINDOW_SECONDS == 60.0
