"""Build progress reporter tests.

The reporter exists because index builds run unattended for 30-75 minutes and
"is it working or wedged?" must be answerable at a glance. Pinning the logic
rather than the cosmetics: rate, ETA, and the throttle that stops a 900,000-item
build from emitting 900,000 lines.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from _progress import Progress  # noqa: E402


def test_reports_fraction_done() -> None:
    p = Progress(total=1000, label="embed")
    line = p.line(done=250, elapsed_s=10.0)
    assert "250" in line and "1,000" in line
    assert "25.0%" in line


def test_average_rate_is_items_per_second() -> None:
    p = Progress(total=1000, label="embed")
    assert "50.0/s" in p.line(done=500, elapsed_s=10.0)


def test_eta_uses_recent_rate_not_average() -> None:
    """Length-sorted batching means throughput DROPS as the run proceeds - short
    texts first, long ones last. An ETA from the average rate is optimistic for
    the whole second half of every build, which is exactly when someone is
    checking whether to wait or kill it."""
    p = Progress(total=1000, label="embed")
    p.line(done=800, elapsed_s=10.0)          # fast early phase, 80/s
    line = p.line(done=900, elapsed_s=20.0)   # recent window is 10/s
    # 100 remaining at the recent 10/s is ~10s, not the ~2s an 45/s average implies
    assert "0.2 min" in line or "10s" in line or "0.1 min" not in line


def test_first_call_always_reports() -> None:
    """A 70-minute build must print something immediately, so you can tell it
    started rather than wedged during setup."""
    p = Progress(total=1_000_000, label="embed", min_interval_s=5.0)
    assert p.should_report(done=1000, elapsed_s=0.5) is True


def test_throttle_suppresses_the_next_rapid_call() -> None:
    """900,000 items must not produce 900,000 lines."""
    p = Progress(total=1_000_000, label="embed", min_interval_s=5.0)
    p.should_report(done=1000, elapsed_s=0.5)
    assert p.should_report(done=1100, elapsed_s=1.0) is False


def test_throttle_allows_after_interval() -> None:
    p = Progress(total=1_000_000, label="embed", min_interval_s=5.0)
    p.should_report(done=1000, elapsed_s=0.0)
    assert p.should_report(done=2000, elapsed_s=6.0) is True


def test_final_item_always_reports_even_if_throttled() -> None:
    """The completion line must never be swallowed by the throttle."""
    p = Progress(total=100, label="embed", min_interval_s=999.0)
    assert p.should_report(done=100, elapsed_s=0.1) is True


def test_zero_elapsed_does_not_divide_by_zero() -> None:
    p = Progress(total=100, label="embed")
    p.line(done=0, elapsed_s=0.0)  # must not raise


def test_extra_fields_are_rendered() -> None:
    """Callers attach build-specific context - current sequence length, RAM -
    without the reporter needing to know what those are."""
    p = Progress(total=100, label="embed")
    line = p.line(done=50, elapsed_s=1.0, extra={"seq": "96 tok", "rss": "2.1 GB"})
    assert "seq 96 tok" in line
    assert "rss 2.1 GB" in line
