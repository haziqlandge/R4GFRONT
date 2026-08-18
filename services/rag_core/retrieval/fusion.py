"""Reciprocal rank fusion, k=60. Job J12.

Architecture.md 3.5:

    score(d) = sum over retrievers of 1 / (k + rank(d))

Rank-based rather than score-based, and that is the whole point. Dense cosine
similarities live around 0.85-0.95 on this corpus (ISSUES.md I3) while BM25
scores are unbounded and corpus-dependent; normalising them onto a common scale
well enough to add would need per-query calibration this project does not have
time to do honestly. RRF needs none - it only asks each retriever for an
ordering, which is the one thing both produce reliably.

The inputs are `(row, score)` lists as returned by `DenseIndex.search()` and
`BM25Index.search()`. **Their rows must index the same chunk list.** Both are
built from the same `chunks.parquet` (see lexical.py), so this holds by
construction; fusing rows from indexes built over different chunk lists would
silently rank mismatched documents, and nothing downstream could detect it.

Scores in the input are read for ordering only, never arithmetically. A caller
that hands in an unsorted list gets a wrong answer, so this module sorts
defensively rather than trusting the contract.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..config import RRF_K

Ranking = Sequence[tuple[int, float]]


def reciprocal_rank_fusion(
    rankings: Sequence[Ranking],
    k: int = RRF_K,
    top_k: int | None = None,
    weights: Sequence[float] | None = None,
) -> list[tuple[int, float]]:
    """Fuse ranked `(row, score)` lists into one, best first.

    Returns `(row, rrf_score)`. The returned score is an RRF score and is not
    comparable to either input's scores, nor to a probability - it exists to
    order rows and to be handed to the reranker, nothing else.

    A row missing from a ranking contributes zero from that ranking rather than
    a penalty. That asymmetry is deliberate: it means adding a retriever can
    only promote documents, never demote them, so a retriever that returns
    nothing useful for a query is harmless rather than actively damaging.

    `weights` lets a caller down-weight a retriever without changing the shape
    of the pipeline. It defaults to uniform and is deliberately left uniform for
    Phase 3's comparison table - ISSUES.md I17 raises the question of whether
    lexical should contribute equally in Hindi, but answering it by fitting
    weights against the bench slice would be tuning on the test set (Rules.md 5).
    Calibrate it in Phase 5 against the dev partition or not at all.
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    elif len(weights) != len(rankings):
        raise ValueError(
            f"{len(weights)} weights for {len(rankings)} rankings"
        )

    fused: dict[int, float] = {}
    for ranking, weight in zip(rankings, weights):
        # Sort defensively. Both callers already return descending scores, but a
        # silently mis-ordered input would produce a plausible fused list that is
        # simply wrong, which is the hardest kind of bug to see in a results table.
        ordered = sorted(ranking, key=lambda pair: pair[1], reverse=True)
        for rank, (row, _score) in enumerate(ordered, start=1):
            fused[row] = fused.get(row, 0.0) + weight / (k + rank)

    # Ties are common - any two rows appearing at the same rank in one list and
    # in neither of the others score identically - so break them on row number
    # to make the output a deterministic function of its inputs. Without this,
    # dict iteration order decides the benchmark's ranking.
    ordered_rows = sorted(fused.items(), key=lambda pair: (-pair[1], pair[0]))
    return ordered_rows[:top_k] if top_k is not None else ordered_rows
