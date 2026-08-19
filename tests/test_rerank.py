"""Cross-encoder reranker tests. Phase 5.

These run against the real ONNX model when it is present and skip cleanly when it
is not, so a fresh clone that has not run scripts/03b_export_reranker.py still has
a green suite. The pure-ordering tests do not need the model at all.

What is pinned here is the set of failure modes that degrade quality SILENTLY,
which is the same reason retrieval/embedder.py's prefix and pooling rules are
called out in its docstring: nothing raises when a cross-encoder is fed the wrong
shape, it just ranks badly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.config import (  # noqa: E402
    ONNX_DIR,
    RERANK_MAX_TOKENS,
    RERANK_MODEL_FILE,
    RERANK_TOKENIZER_FILE,
    RERANKER,
)
from rag_core.retrieval.rerank import CrossEncoder  # noqa: E402

MODEL_DIR = ONNX_DIR / f"rerank-{RERANKER}"
MODEL_PATH = MODEL_DIR / RERANK_MODEL_FILE
TOKENIZER_PATH = MODEL_DIR / RERANK_TOKENIZER_FILE

needs_model = pytest.mark.skipif(
    not MODEL_PATH.exists(),
    reason="reranker ONNX not fetched; run scripts/03b_export_reranker.py",
)


@pytest.fixture(scope="module")
def ce() -> CrossEncoder:
    return CrossEncoder(MODEL_PATH, TOKENIZER_PATH, threads=2)


def test_missing_model_raises_with_a_useful_message() -> None:
    with pytest.raises(FileNotFoundError, match="03b_export_reranker"):
        CrossEncoder(Path("nope/model.onnx"), TOKENIZER_PATH)


@needs_model
def test_empty_candidates_returns_empty(ce: CrossEncoder) -> None:
    assert ce.score("q", []).shape == (0,)
    assert ce.rerank("q", []) == ([], 0)


@needs_model
def test_scores_align_with_input_order(ce: CrossEncoder) -> None:
    """score() must NOT sort. The caller holds a parallel candidate list and a
    silently-sorted return would mis-attribute every score to the wrong passage."""
    passages = ["the capital of France is Paris", "bananas are yellow", "a dog barks"]
    scores = ce.score("what is the capital of France", passages)
    assert scores.shape == (3,)
    assert int(np.argmax(scores)) == 0


@needs_model
def test_relevant_passage_outranks_irrelevant(ce: CrossEncoder) -> None:
    """The basic competence check. If this fails the pair encoding is wrong -
    typically query and passage concatenated as one string, losing the segment
    boundary the model was trained with."""
    ranked, _ = ce.rerank(
        "how tall is Mount Everest",
        [
            ("a", "Mount Everest rises 8,849 metres above sea level."),
            ("b", "Cheese is made from milk and is a popular food."),
            ("c", "The Pacific Ocean is the largest ocean on Earth."),
        ],
    )
    assert ranked[0][0] == "a"


@needs_model
def test_equal_length_batches_are_exactly_reproducible(ce: CrossEncoder) -> None:
    """With no padding, batching is bit-exact regardless of batch size."""
    passages = ["passage about assorted topics here"] * 7
    one = ce.score("a question", passages, batch_size=1)
    many = ce.score("a question", passages, batch_size=7)
    np.testing.assert_array_equal(one, many)


@needs_model
def test_batch_size_one_is_the_reproducible_configuration(ce: CrossEncoder) -> None:
    """ISSUES.md I24. The int8 model's score for a (query, passage) pair depends on
    what ELSE is in its batch, because ONNX Runtime's dynamic quantization derives
    activation scales per tensor at run time and padding changes the tensor.

    Measured on this model: fp32 drifts 0.000000 with batch size; int8 drifts up to
    0.279 when batch members differ in length, and 0.000000 when they do not. The
    median adjacent-rank logit gap is 0.364, so that perturbation is large enough
    to reorder neighbouring candidates.

    It matters beyond tidiness: Phase 6 calibrates the abstention floor on the
    top-1 rerank score, and a threshold is only meaningful against a score that is
    a function of the pair rather than of its batch neighbours. The reranker
    therefore scores one pair at a time on the hot path.
    """
    passages = [
        "short one",
        "a considerably longer passage with many more tokens in it than the others",
        "medium length passage here",
    ]
    a = ce.score("a question", passages, batch_size=1)
    b = ce.score("a question", passages, batch_size=1)
    np.testing.assert_array_equal(a, b, "batch_size=1 must be deterministic")


@needs_model
def test_rerank_reorders_and_preserves_ids(ce: CrossEncoder) -> None:
    cands = [
        ("p1", "Completely unrelated text about gardening tools."),
        ("p2", "The Eiffel Tower is located in Paris, France."),
    ]
    ranked, _ = ce.rerank("where is the Eiffel Tower", cands)
    assert {pid for pid, _ in ranked} == {"p1", "p2"}
    assert ranked[0][0] == "p2"
    assert ranked[0][1] > ranked[1][1]


@needs_model
def test_top_k_truncates_after_sorting(ce: CrossEncoder) -> None:
    cands = [(str(i), f"text {i}") for i in range(10)]
    ranked, _ = ce.rerank("q", cands, top_k=3)
    assert len(ranked) == 3
    assert [s for _, s in ranked] == sorted([s for _, s in ranked], reverse=True)


@needs_model
def test_long_passage_truncates_without_eating_the_query(ce: CrossEncoder) -> None:
    """truncation strategy is only_second. A long passage must not push the
    question out of the window - a truncated question is unanswerable, and the
    corpus contains a 4,093-word degenerate passage (Memory.md Phase 1)."""
    query = "what is the boiling point of water"
    long_passage = "water " * 4000
    scores = ce.score(query, [long_passage])
    assert scores.shape == (1,)
    assert np.isfinite(scores[0])

    enc = ce.tokenizer.encode(query, long_passage)
    assert len(enc.ids) <= RERANK_MAX_TOKENS
    # the query survives: its tokens are still present at the front of the pair
    query_ids = ce.tokenizer.encode(query).ids
    assert len(query_ids) < len(enc.ids)


@needs_model
def test_stable_sort_keeps_retriever_order_on_ties(ce: CrossEncoder) -> None:
    """Identical text must not be permuted arbitrarily: top-1 becomes the answer
    and a coin flip there is not reproducible across runs."""
    cands = [("first", "identical text"), ("second", "identical text")]
    assert ce.rerank("q", cands)[0][0][0] == "first"


@needs_model
def test_deadline_stops_scoring_and_reports_how_far_it_got() -> None:
    """ISSUES.md I25. A deadline of zero must still score the first pair - a rerank
    that scored nothing would leave top-1 at the dense pick with a cross-encoder
    score attached to it, which is the one genuinely misleading outcome."""
    ce = CrossEncoder(MODEL_PATH, TOKENIZER_PATH, threads=2)
    cands = [(str(i), f"a passage about topic number {i}") for i in range(8)]
    ranked, scored = ce.rerank("a question", cands, deadline_ms=0.0)
    assert scored == 1
    assert len(ranked) == len(cands), "unscored candidates are demoted, never dropped"
    assert all(np.isfinite(s) for _, s in ranked), "no infinities reach the response"


@needs_model
def test_no_deadline_scores_everything() -> None:
    ce = CrossEncoder(MODEL_PATH, TOKENIZER_PATH, threads=2)
    cands = [(str(i), f"a passage about topic number {i}") for i in range(5)]
    ranked, scored = ce.rerank("a question", cands)
    assert scored == 5
    assert [s for _, s in ranked] == sorted([s for _, s in ranked], reverse=True)
