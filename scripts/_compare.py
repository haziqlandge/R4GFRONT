"""Metrics and pairing for the J15 strategy comparison. Pure functions only.

Split out from the harness script so the maths is unit-testable without loading
a 655 MB index.

The pairing contract is the important part. ISSUES.md I21 records the Phase 3
comparison being assembled from separately-dated eval files that had used
different query counts - which compared sample sizes, not strategies. Every
function here works on per-query arrays that are positionally aligned across
strategies, so a delta between two strategies is always the same query minus
itself.
"""

from __future__ import annotations

import numpy as np


def ndcg_at_k(ranked_ids: list[str], gold: set[str], k: int) -> float:
    """Binary-relevance nDCG@k.

    Binary rather than graded because the corpus gives binary labels: MS MARCO's
    is_selected marks a passage answer-bearing or not, with no relevance grades
    to model. IDCG is therefore the best achievable ordering given how many gold
    passages exist, capped at k.
    """
    if not gold:
        return 0.0
    dcg = 0.0
    for i, pid in enumerate(ranked_ids[:k]):
        if pid in gold:
            dcg += 1.0 / np.log2(i + 2)
    ideal_hits = min(len(gold), k)
    idcg = sum(1.0 / np.log2(i + 2) for i in range(ideal_hits))
    return float(dcg / idcg) if idcg > 0 else 0.0


def per_query_metrics(
    ranked_per_query: list[list[str]], gold_per_query: list[set[str]], k: int
) -> dict[str, np.ndarray]:
    """Score each query independently, preserving input order.

    Returns arrays where index i is query i. Nothing is filtered here: a query
    with no gold scores 0 rather than being dropped, because dropping inside
    would let two strategies return different-length arrays and silently break
    pairing. The caller filters the query list once, up front, for everyone.
    """
    if len(ranked_per_query) != len(gold_per_query):
        raise ValueError(
            f"ranked ({len(ranked_per_query)}) and gold ({len(gold_per_query)}) "
            "lengths differ - these did not score the same queries"
        )

    hit1, recall10, mrr10, ndcg10 = [], [], [], []
    for ranked, gold in zip(ranked_per_query, gold_per_query):
        top = ranked[:k]
        hit1.append(1.0 if top and top[0] in gold else 0.0)
        found = [i for i, pid in enumerate(top) if pid in gold]
        recall10.append(1.0 if found else 0.0)
        mrr10.append(1.0 / (found[0] + 1) if found else 0.0)
        ndcg10.append(ndcg_at_k(ranked, gold, k))

    return {
        "hit1": np.array(hit1),
        "recall10": np.array(recall10),
        "mrr10": np.array(mrr10),
        "ndcg10": np.array(ndcg10),
    }


def paired_bootstrap(
    a: np.ndarray, b: np.ndarray, n: int = 4000, seed: int = 0
) -> tuple[float, float, float]:
    """Bootstrap CI for the PAIRED difference a - b. Returns (mean, lo95, hi95).

    Paired, not two-sample: resampling the per-query differences removes
    between-query variance, which on 500 queries is most of the variance. The
    unpaired interval on this corpus is roughly +/-0.04, wide enough to hide
    every effect measured in Phase 3; the paired interval is several times
    tighter and is what makes small deltas interpretable.
    """
    if len(a) != len(b):
        raise ValueError(
            f"paired bootstrap needs equal lengths, got {len(a)} and {len(b)} - "
            "the two runs did not score the same queries (see ISSUES.md I21)"
        )
    d = a - b
    if not np.any(d):
        return 0.0, 0.0, 0.0
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), (n, len(d)))
    boot = d[idx].mean(axis=1)
    return float(d.mean()), float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))
