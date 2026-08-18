"""Reciprocal rank fusion tests. Job J12.

The behaviours pinned here are the ones that would fail silently in a results
table rather than loudly at runtime:

  - the formula itself, checked against hand-computed values, because an
    off-by-one in `rank` shifts every score and still produces a plausible order
  - determinism under ties, since RRF ties constantly and dict order would
    otherwise decide a published benchmark
  - a document found by only one retriever is promoted, not penalised, which is
    what makes adding a weak retriever safe
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from rag_core.config import RRF_K  # noqa: E402
from rag_core.retrieval.fusion import reciprocal_rank_fusion  # noqa: E402


def rows(fused: list[tuple[int, float]]) -> list[int]:
    return [row for row, _ in fused]


# -- the formula ------------------------------------------------------------


def test_single_ranking_scores_match_the_formula() -> None:
    """1/(k+rank), rank starting at 1. If rank were 0-based every score here
    would be wrong while the ORDER stayed right, so order alone cannot catch it."""
    fused = reciprocal_rank_fusion([[(7, 0.9), (3, 0.8)]], k=60)
    assert fused[0] == (7, pytest.approx(1 / 61))
    assert fused[1] == (3, pytest.approx(1 / 62))


def test_agreement_sums_across_rankings() -> None:
    """A row ranked first by both retrievers scores 2/(k+1)."""
    fused = reciprocal_rank_fusion([[(1, 9.0)], [(1, 0.4)]], k=60)
    assert fused == [(1, pytest.approx(2 / 61))]


def test_default_k_is_the_configured_constant() -> None:
    assert reciprocal_rank_fusion([[(1, 1.0)]])[0][1] == pytest.approx(1 / (RRF_K + 1))


# -- why RRF was chosen -----------------------------------------------------


def test_incomparable_score_scales_do_not_matter() -> None:
    """The reason for rank-based fusion (Architecture.md 3.5). BM25 scores in the
    tens and cosine scores under 1.0 fuse identically to the same scales shrunk
    by 1000x, because only the ordering is read."""
    big = [[(1, 42.0), (2, 17.0)], [(2, 0.93), (1, 0.91)]]
    small = [[(1, 0.042), (2, 0.017)], [(2, 0.00093), (1, 0.00091)]]
    assert reciprocal_rank_fusion(big) == reciprocal_rank_fusion(small)


def test_consensus_beats_a_single_first_place() -> None:
    """Row 2 is second in both lists; row 1 is first in one and absent from the
    other. Agreement across retrievers wins, which is the entire premise."""
    fused = reciprocal_rank_fusion([[(1, 9.0), (2, 8.0)], [(3, 9.0), (2, 8.0)]])
    assert rows(fused)[0] == 2


def test_a_row_found_by_one_retriever_only_is_promoted_not_penalised() -> None:
    """Missing contributes zero, not a negative. So adding a retriever can only
    lift documents - a retriever that is useless for a query is harmless."""
    alone = reciprocal_rank_fusion([[(5, 1.0)]])
    with_second = reciprocal_rank_fusion([[(5, 1.0)], [(9, 1.0)]])
    assert dict(alone)[5] == pytest.approx(dict(with_second)[5])
    assert 9 in dict(with_second)


# -- determinism ------------------------------------------------------------


def test_ties_break_on_row_number_deterministically() -> None:
    """Rows 4 and 2 both appear once at rank 1 of different lists, so they tie
    exactly. Without a tie-break the published ranking would depend on dict
    iteration order."""
    fused = reciprocal_rank_fusion([[(4, 1.0)], [(2, 1.0)]])
    assert [r for r, _ in fused] == [2, 4]
    assert fused[0][1] == pytest.approx(fused[1][1])


def test_repeated_calls_are_identical() -> None:
    rankings = [[(1, 0.9), (2, 0.8), (3, 0.7)], [(3, 5.0), (1, 4.0), (9, 3.0)]]
    assert reciprocal_rank_fusion(rankings) == reciprocal_rank_fusion(rankings)


def test_unsorted_input_is_sorted_defensively() -> None:
    """A caller handing in ascending scores must not silently invert the fusion."""
    assert reciprocal_rank_fusion([[(1, 0.9), (2, 0.1)]]) == reciprocal_rank_fusion(
        [[(2, 0.1), (1, 0.9)]]
    )


# -- shape and edges --------------------------------------------------------


def test_scores_are_descending() -> None:
    fused = reciprocal_rank_fusion([[(1, 0.9), (2, 0.8), (3, 0.7)], [(3, 5.0)]])
    assert [s for _, s in fused] == sorted((s for _, s in fused), reverse=True)


def test_top_k_truncates() -> None:
    rankings = [[(1, 0.9), (2, 0.8), (3, 0.7), (4, 0.6)]]
    assert len(reciprocal_rank_fusion(rankings, top_k=2)) == 2


def test_returns_row_score_pairs_like_the_retrievers() -> None:
    for row, score in reciprocal_rank_fusion([[(1, 0.9)], [(2, 3.0)]]):
        assert isinstance(row, int)
        assert isinstance(score, float)


def test_all_empty_rankings_fuse_to_nothing() -> None:
    assert reciprocal_rank_fusion([[], []]) == []


def test_one_empty_ranking_leaves_the_other_intact() -> None:
    """A lexical search returning nothing (an all-punctuation query) must not
    take dense's results down with it."""
    assert rows(reciprocal_rank_fusion([[(1, 0.9), (2, 0.8)], []])) == [1, 2]


def test_no_rankings_at_all() -> None:
    assert reciprocal_rank_fusion([]) == []


# -- weights ----------------------------------------------------------------


def test_uniform_weights_are_the_default() -> None:
    rankings = [[(1, 0.9), (2, 0.8)], [(2, 5.0), (3, 4.0)]]
    assert reciprocal_rank_fusion(rankings) == reciprocal_rank_fusion(
        rankings, weights=[1.0, 1.0]
    )


def test_zero_weight_silences_a_retriever() -> None:
    rankings = [[(1, 0.9)], [(2, 5.0)]]
    assert rows(reciprocal_rank_fusion(rankings, weights=[1.0, 0.0])) == [1, 2]
    assert dict(reciprocal_rank_fusion(rankings, weights=[1.0, 0.0]))[2] == 0.0


def test_weight_count_must_match_ranking_count() -> None:
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([[(1, 0.9)], [(2, 0.8)]], weights=[1.0])
