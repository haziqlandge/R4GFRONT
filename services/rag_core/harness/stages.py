"""Concrete pipeline stages for the Phase 2 slice.

Each stage declares its budget and timeout from config, so the numbers in
Latency.md 4 are the numbers the code enforces rather than a parallel document
that drifts. Phase 3 inserts lexical_search and fuse, Phase 5 inserts rerank and
route, Phase 6 the guards - all by appending to build_pipeline(), not by editing
these functions.
"""

from __future__ import annotations

import numpy as np

from ..answering.extractive import build_answer
from ..chunking.base import ChunkRecord
from ..config import (
    BUDGET_MS,
    DENSE_TOP_K,
    STAGE_BUDGET_MS,
    STAGE_TIMEOUT_MS,
)
from ..harness.errors import EmbedFailed
from ..retrieval.dense import DenseIndex
from ..retrieval.embedder import Embedder
from .pipeline import Context, FunctionStage, Pipeline


class Runtime:
    """Everything loaded once at startup and shared by every request.

    Rules.md 2.1: model sessions and indexes are created at startup with a fixed
    thread count, never per request.
    """

    def __init__(self, embedder: Embedder, index: DenseIndex) -> None:
        self.embedder = embedder
        self.index = index
        # passage_id -> full passage text, for citations and the extractive answer.
        self.passage_text: dict[str, str] = {}

    def build_passage_map(self, chunks: list[ChunkRecord]) -> None:
        """Reconstruct passage text from its chunks.

        Chunk 0 of a passage that produced only one chunk IS the passage. For
        multi-chunk passages the overlap makes naive concatenation wrong, so the
        longest chunk is used as the representative span. Phase 5 replaces this
        with a proper passage store when span selection needs exact offsets.
        """
        best: dict[str, str] = {}
        for c in chunks:
            pid = c["passage_id"]
            if len(c["text"]) > len(best.get(pid, "")):
                best[pid] = c["text"]
        self.passage_text = best


def build_pipeline(rt: Runtime) -> Pipeline:
    async def embed_query(ctx: Context) -> Context:
        try:
            vec = rt.embedder.encode_one(ctx.query, "query")
        except Exception as exc:  # noqa: BLE001 - re-raised as a typed error
            raise EmbedFailed(str(exc)) from exc
        return ctx.with_data(query_vector=vec)

    async def dense_search(ctx: Context) -> Context:
        vec: np.ndarray = ctx.data["query_vector"]
        rows = rt.index.search(vec, DENSE_TOP_K)
        hits = [(rt.index.chunk(row), score) for row, score in rows]
        return ctx.with_data(hits=hits)

    async def answer_extractive(ctx: Context) -> Context:
        hits = ctx.data.get("hits", [])
        answer, citations = build_answer(hits, rt.passage_text)
        top1 = hits[0][1] if hits else None
        gap = (hits[0][1] - hits[1][1]) if len(hits) > 1 else None
        return ctx.with_data(
            answer=answer, citations=citations, top1=top1, score_gap=gap
        )

    return Pipeline(
        stages=[
            # required: without a query vector there is no retrieval, and no
            # honest way to answer. Degrading here would mean inventing one.
            FunctionStage(
                "embed_query",
                embed_query,
                timeout_ms=STAGE_TIMEOUT_MS["embed_query"],
                budget_ms=STAGE_BUDGET_MS["embed_query"],
                required=True,
            ),
            FunctionStage(
                "dense_search",
                dense_search,
                timeout_ms=STAGE_TIMEOUT_MS["dense_search"],
                budget_ms=STAGE_BUDGET_MS["dense_search"],
                required=True,
            ),
            FunctionStage(
                "answer_extractive",
                answer_extractive,
                timeout_ms=STAGE_TIMEOUT_MS["answer_extractive"],
                budget_ms=STAGE_BUDGET_MS["answer_extractive"],
            ),
        ],
        budget_ms=BUDGET_MS,
    )
