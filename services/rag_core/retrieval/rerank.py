"""ONNX int8 cross-encoder reranker. Architecture.md 3.6.

This stage is the one the whole answer story rests on. ISSUES.md I2: dense-only
Hit@1 is 0.356 en / 0.224 hi against Recall@10 0.878, so the right passage is
almost always retrieved and almost never first - and the extractive path returns
the first one. Reranking is what converts good retrieval into a correct answer.

Why a cross-encoder rather than a better bi-encoder: a bi-encoder embeds query and
passage independently and compares two vectors that never met. A cross-encoder
reads them concatenated in one forward pass, so attention runs across the pair and
the model can see that a passage answers *this* question rather than merely sharing
its topic. That is also why its score is the honest confidence signal for routing
and abstention (ISSUES.md I3), where the dense score demonstrably is not.

Rules.md 2.1: no PyTorch at request time. Rules.md 2.2: one session, created at
startup with an explicit thread count.

Two details that silently degrade quality rather than raising:

  1. A cross-encoder takes a PAIR, encoded as a single sequence with the segment
     boundary the model was trained with. Encoding query and passage separately and
     concatenating strings loses that boundary and the scores become noise.
  2. There is NO "query: " / "passage: " prefix here. Those are e5 conventions and
     belong to the embedder; adding them to a cross-encoder feeds it tokens its
     training never saw.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Final, Sequence

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from ..config import RERANK_MAX_TOKENS

# Scores are raw logits. Left uncalibrated on purpose: the routing thresholds in
# Phase 5 are fitted to whatever scale this model actually produces, so squashing
# through a sigmoid here would only add a monotone transform for the calibration to
# undo. Ranking is invariant to it either way.

# ONE PAIR AT A TIME, and it is both the correct and the fast choice.
#
# Correctness: ISSUES.md I24. Dynamic int8 quantization derives activation scales
# per tensor at run time, so padding a batch to its longest member perturbs the
# real tokens too. Measured drift is 0.279 logits against a 0.364 median
# adjacent-rank gap - enough to reorder neighbours - and exactly 0.000 when batch
# members are the same length. Phase 6 puts an abstention floor on this score, and
# a threshold means nothing against a number that moves with its batch neighbours.
#
# Speed: batching is SLOWER here, measured on an idle box at 2 serving threads.
# At depth 10, P50 is 113.8 ms at batch 1 against 166.3 ms at batch 16; at depth
# 20, 249.1 ms against 376.6 ms. Two threads extract little parallelism from a
# batch, while padding to the longest member wastes real compute on every shorter
# passage - the same effect Phase 2 measured when length-sorted batching made the
# offline embedder 1.46x faster. Batch size 1 has no padding at all.
#
# So the reproducible configuration is also the fast one, and there is no trade to
# make. Revisit only if the serving thread count rises well above 2.
DEFAULT_BATCH: Final[int] = 1


class CrossEncoder:
    """Wraps one ONNX cross-encoder session and its tokenizer."""

    def __init__(
        self,
        model_path: Path,
        tokenizer_path: Path,
        threads: int = 2,
        max_tokens: int = RERANK_MAX_TOKENS,
    ) -> None:
        if not model_path.exists():
            raise FileNotFoundError(
                f"{model_path} missing. Run scripts/03b_export_reranker.py first."
            )

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self.session = ort.InferenceSession(
            str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._input_names = {i.name for i in self.session.get_inputs()}

        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        # truncate the PASSAGE, never the question. "longest_first" would eat the
        # query on a long passage, and a truncated question is unanswerable.
        self.tokenizer.enable_truncation(max_length=max_tokens, strategy="only_second")
        self.tokenizer.enable_padding()
        self.max_tokens = max_tokens
        self.model_path = model_path

    def score(
        self, query: str, passages: Sequence[str], batch_size: int = DEFAULT_BATCH
    ) -> np.ndarray:
        """Relevance logit for each (query, passage) pair. Higher is better.

        Returns float32 (len(passages),) in the INPUT order, not sorted - the caller
        owns the ordering decision and needs the scores aligned with its own
        candidate list.
        """
        n = len(passages)
        if n == 0:
            return np.zeros((0,), dtype=np.float32)

        out = np.empty((n,), dtype=np.float32)
        for start in range(0, n, batch_size):
            chunk = list(passages[start : start + batch_size])
            out[start : start + len(chunk)] = self._score_batch(query, chunk)
        return out

    def _score_batch(self, query: str, passages: list[str]) -> np.ndarray:
        # encode_batch with pair input is what produces the trained segment
        # boundary: the tokenizer inserts the separator and sets token_type_ids.
        encodings = self.tokenizer.encode_batch([(query, p) for p in passages])

        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)

        feeds: dict[str, np.ndarray] = {"input_ids": input_ids}
        if "attention_mask" in self._input_names:
            feeds["attention_mask"] = attention_mask
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.array(
                [e.type_ids for e in encodings], dtype=np.int64
            )

        logits: np.ndarray = self.session.run(None, feeds)[0]
        # Sequence-classification heads ship as (n, 1) for a regression-style
        # relevance score and (n, 2) for a binary head. Take the positive class in
        # the two-column case; squeeze the one-column case.
        if logits.ndim == 2 and logits.shape[1] == 2:
            scored = logits[:, 1]
        else:
            scored = logits.reshape(logits.shape[0], -1)[:, 0]
        return scored.astype(np.float32)

    def rerank(
        self,
        query: str,
        candidates: Sequence[tuple[str, str]],
        top_k: int | None = None,
        deadline_ms: float | None = None,
    ) -> tuple[list[tuple[str, float]], int]:
        """Reorder (id, text) candidates by relevance. Returns ((id, score) desc, n_scored).

        Sorted with a stable sort, so equal scores keep the retriever's original
        order rather than being permuted arbitrarily - which matters because the
        top-1 becomes the answer and a coin-flip there is not reproducible.

        DEADLINE. `deadline_ms` is the wall clock this call may spend, and it is
        checked between pairs. ISSUES.md I25: a stage's declared timeout_ms cannot
        interrupt this work, because asyncio.wait_for only fires at an await point
        and ONNX inference never yields - a stage with a 50 ms timeout was measured
        running 123.7 ms and reporting status "ok". The budget counter can decline
        to START a stage; nothing outside this method can stop it once running.

        So the enforcement has to live here, and scoring one pair at a time (which
        I24 already forced for reproducibility) is what makes it possible: the loop
        has a natural yield point every ~11 ms.

        On expiry the unscored candidates keep their retriever order BELOW every
        scored one, rather than being dropped. Dropping them would silently shrink
        the candidate set the citations are drawn from; demoting them preserves the
        contract that the caller gets back exactly what it passed in, reordered as
        far as the budget allowed. `n_scored` is returned so the trace can say how
        far it got instead of presenting a partial rerank as a complete one.
        """
        if not candidates:
            return [], 0

        started = time.perf_counter()
        n = len(candidates)
        scores = np.zeros(n, dtype=np.float32)
        scored = 0
        slowest_pair_ms = 0.0
        for i, (_, text) in enumerate(candidates):
            if deadline_ms is not None and i > 0:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                # PREDICTIVE, not reactive. Asking "have I already overrun?" cannot
                # keep the promise: nothing interrupts a pair once ONNX has it, so
                # a check that passes with 5 ms left still spends a whole pair and
                # lands 20 ms over. The stop condition has to be "will the NEXT
                # pair fit", which needs an estimate of what a pair costs.
                #
                # The estimate is the slowest pair scored SO FAR IN THIS CALL, not
                # the mean. Cost tracks sequence length, the candidates for one
                # query vary in length, and a mean underestimates exactly the case
                # that breaks the budget - the long passage sitting at rank 5.
                # Overestimating costs one pair of reranking on a query that was
                # near the line anyway; underestimating costs the guarantee.
                if elapsed_ms + slowest_pair_ms > deadline_ms:
                    break
            pair_started = time.perf_counter()
            scores[i] = self.score(query, [text])[0]
            slowest_pair_ms = max(slowest_pair_ms, (time.perf_counter() - pair_started) * 1000.0)
            scored += 1

        # Unscored candidates sort below every scored one, and the stable sort then
        # preserves the retriever's order among them. The sentinel is one below the
        # lowest real score rather than -inf: these values reach the response as
        # citation scores, and neither JSON nor a UI has a sensible rendering for
        # an infinity.
        if scored < n:
            floor = float(scores[:scored].min()) - 1.0
            scores[scored:] = floor

        order = np.argsort(-scores, kind="stable")
        if top_k is not None:
            order = order[:top_k]
        return [(candidates[int(i)][0], float(scores[i])) for i in order], scored
