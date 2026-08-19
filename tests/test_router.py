"""Routing tests. Phase 5.

The router is where requirement 3 (latency) and requirement 6 (knowing when not to
answer) meet, so these tests are about behaviour at the boundaries rather than
about the threshold values - which are calibrated data, not code, and change when
the reranker changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.answering.router import route  # noqa: E402

LOW, HIGH = -2.0, 4.0


def r(score, **kw):
    kw.setdefault("tau_low", LOW)
    kw.setdefault("tau_high", HIGH)
    kw.setdefault("generative_available", True)
    return route(score, **kw)


def test_high_confidence_goes_extractive() -> None:
    assert r(9.0).decision == "EXTRACTIVE"


def test_low_confidence_abstains_with_a_typed_reason() -> None:
    d = r(-7.0)
    assert d.decision == "ABSTAIN"
    assert d.abstain_reason == "LOW_CONFIDENCE"
    assert d.path == "NONE"


def test_middle_band_goes_generative() -> None:
    assert r(1.0).decision == "GENERATIVE"


def test_no_candidates_abstains_rather_than_crashing() -> None:
    d = r(None)
    assert d.decision == "ABSTAIN"
    assert d.abstain_reason == "LOW_CONFIDENCE"


def test_boundaries_are_half_open() -> None:
    """A score exactly at tau_high is extractive; exactly at tau_low is generative.
    Pinned so a future threshold change cannot silently flip the edge case."""
    assert r(HIGH).decision == "EXTRACTIVE"
    assert r(HIGH - 1e-9).decision == "GENERATIVE"
    assert r(LOW).decision == "GENERATIVE"
    assert r(LOW - 1e-9).decision == "ABSTAIN"


def test_breaker_open_degrades_to_extractive_not_abstain() -> None:
    """Rules.md 4: a 429 opens the breaker and routes to EXTRACTIVE. A moderately
    confident passage with its citation beats a refusal, and the user sees the
    score either way."""
    d = r(1.0, generative_available=False)
    assert d.decision == "EXTRACTIVE"
    assert "unavailable" in d.reason


def test_breaker_open_does_not_rescue_a_low_score() -> None:
    """Degrading the middle band must not smuggle the abstain band along with it."""
    assert r(-7.0, generative_available=False).decision == "ABSTAIN"


def test_reason_is_always_populated() -> None:
    """The reason travels with the decision so the trace and AbstentionPanel can
    show it rather than inferring it from the outcome."""
    for score in (None, -7.0, 1.0, 9.0):
        assert r(score).reason


def test_path_maps_to_the_api_contract() -> None:
    """Architecture.md 9 fixes these three strings; the frontend is built on them."""
    assert r(9.0).path == "EXTRACTIVE"
    assert r(1.0).path == "GENERATIVE"
    assert r(-7.0).path == "NONE"
