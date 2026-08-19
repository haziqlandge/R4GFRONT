"""Metric and pairing tests for the J15 comparison harness.

The harness exists because of ISSUES.md I21: comparing separately-dated eval
JSONs compared sample sizes rather than strategies. Two properties have to hold
or the Phase 3 decision rests on nothing:

  1. Every strategy is scored on the SAME queries, in the SAME order, so
     per-query results line up positionally and bootstrap deltas are genuinely
     paired rather than two independent samples differenced.
  2. The metrics are actually correct.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _compare import ndcg_at_k, paired_bootstrap, per_query_metrics  # noqa: E402


# -- nDCG -------------------------------------------------------------------


def test_ndcg_is_one_when_gold_is_rank_one() -> None:
    assert ndcg_at_k(["p1", "p2", "p3"], {"p1"}, k=3) == 1.0


def test_ndcg_is_zero_when_no_gold_retrieved() -> None:
    assert ndcg_at_k(["p1", "p2"], {"p9"}, k=2) == 0.0


def test_ndcg_discounts_lower_ranks() -> None:
    top = ndcg_at_k(["p1", "p2"], {"p1"}, k=2)
    low = ndcg_at_k(["p2", "p1"], {"p1"}, k=2)
    assert top > low


def test_ndcg_matches_hand_computed_value() -> None:
    """Single gold at rank 2: DCG = 1/log2(3), IDCG = 1/log2(2) = 1."""
    assert abs(ndcg_at_k(["x", "g"], {"g"}, k=2) - (1 / np.log2(3))) < 1e-9


def test_ndcg_with_two_gold_passages_rewards_both() -> None:
    one = ndcg_at_k(["g1", "x", "x2"], {"g1", "g2"}, k=3)
    both = ndcg_at_k(["g1", "g2", "x"], {"g1", "g2"}, k=3)
    assert both > one


# -- per-query arrays, the pairing contract ---------------------------------


def test_per_query_metrics_returns_one_entry_per_query() -> None:
    ranked = [["a"], ["b"], ["c"]]
    gold = [{"a"}, {"z"}, {"c"}]
    m = per_query_metrics(ranked, gold, k=10)
    assert len(m["hit1"]) == 3
    assert len(m["recall10"]) == 3
    assert len(m["ndcg10"]) == 3


def test_per_query_arrays_are_positionally_aligned_with_input() -> None:
    """The pairing guarantee. Query i's result must sit at index i for every
    strategy, or a paired bootstrap silently differences unrelated queries."""
    ranked = [["a"], ["z"], ["c"]]
    gold = [{"a"}, {"y"}, {"c"}]
    m = per_query_metrics(ranked, gold, k=10)
    assert list(m["hit1"]) == [1.0, 0.0, 1.0]


def test_queries_with_no_gold_are_excluded_consistently() -> None:
    """A query with empty gold cannot be scored. It must be dropped by the
    CALLER before scoring, so every strategy sees the identical query list -
    dropping inside would let two strategies end up with different lengths."""
    m = per_query_metrics([["a"]], [set()], k=10)
    assert len(m["hit1"]) == 1
    assert m["hit1"][0] == 0.0


# -- paired bootstrap -------------------------------------------------------


def test_paired_bootstrap_reports_zero_for_identical_inputs() -> None:
    a = np.array([1.0, 0.0, 1.0, 1.0])
    mean, lo, hi = paired_bootstrap(a, a.copy(), n=500, seed=0)
    assert mean == 0.0 and lo == 0.0 and hi == 0.0


def test_paired_bootstrap_detects_a_consistent_difference() -> None:
    a = np.ones(200)
    b = np.zeros(200)
    mean, lo, hi = paired_bootstrap(a, b, n=500, seed=0)
    assert mean == 1.0
    assert lo > 0.0


def test_paired_bootstrap_ci_straddles_zero_for_noise() -> None:
    rng = np.random.default_rng(0)
    a = rng.integers(0, 2, 300).astype(float)
    b = rng.integers(0, 2, 300).astype(float)
    _, lo, hi = paired_bootstrap(a, b, n=1000, seed=1)
    assert lo < 0.0 < hi


def test_paired_bootstrap_requires_equal_lengths() -> None:
    """Mismatched lengths mean the two runs did not score the same queries -
    exactly the I21 defect. Fail loudly rather than truncating."""
    import pytest

    with pytest.raises(ValueError):
        paired_bootstrap(np.ones(10), np.ones(9), n=10, seed=0)
