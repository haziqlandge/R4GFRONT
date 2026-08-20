"""Concrete pipeline stages.

Each stage declares its budget and timeout from config, so the numbers in
Latency.md 4 are the numbers the code enforces rather than a parallel document
that drifts. Phase 5 appended rerank, route and answer_generative; Phase 6 appends
the guards - all by adding to build_pipeline(), not by editing existing stages.
"""

from __future__ import annotations

import numpy as np

from ..answering.extractive import build_answer, detect_language
from ..answering.generative import GroqClient
from ..answering.router import Route, route
from .. import config
from ..chunking.base import ChunkRecord
from ..config import (
    BUDGET_MS,
    DENSE_TOP_K,
    GROQ_TIMEOUT_MS,
    INPUT_MAX_CHARS,
    INPUT_MAX_TOKENS,
    RERANK_DEADLINE_MARGIN_MS,
    RERANK_TOP_K,
    STAGE_BUDGET_MS,
    STAGE_TIMEOUT_MS,
)
from ..guardrails.input_guard import InputGuard
from ..guardrails.output_guard import groundedness, invalid_citations
from ..harness.errors import EmbedFailed, UpstreamUnavailable
from ..retrieval.dense import DenseIndex
from ..retrieval.embedder import Embedder
from ..retrieval.rerank import CrossEncoder
from .pipeline import Context, FunctionStage, Pipeline


class Runtime:
    """Everything loaded once at startup and shared by every request.

    Rules.md 2.1: model sessions and indexes are created at startup with a fixed
    thread count, never per request.
    """

    def __init__(
        self,
        embedder: Embedder,
        index: DenseIndex,
        reranker: CrossEncoder | None = None,
        groq: GroqClient | None = None,
    ) -> None:
        self.embedder = embedder
        self.index = index
        # Optional so the Phase 2 dense-only pipeline still builds - the benchmark
        # harness and several tests construct a Runtime without them, and a
        # required reranker would make "measure retrieval alone" impossible.
        self.reranker = reranker
        self.groq = groq
        # passage_id -> full passage text, for citations and the extractive answer.
        self.passage_text: dict[str, str] = {}

    def load_passage_store(self) -> bool:
        """Load the REAL passage text from the frozen slice. Closes ISSUES.md I9.

        Phase 2 reconstructed passage text by taking a passage's longest chunk,
        which is exact for the 78% of passages that emit one chunk and truncated
        for the rest. Phase 5 makes that inaccuracy matter in two new ways:

          1. The cross-encoder scores (query, passage) pairs. Scoring a truncated
             passage is scoring a different passage, and the rerank eval in
             scripts/05d_eval_rerank.py reads the real text from passages.parquet -
             so an approximate store would mean the measured Hit@1 lift was not
             the lift the service actually delivers. A benchmark that does not
             describe the deployed system is the failure Rules.md 1 exists to
             prevent.
          2. The extractive answer is handed to the user verbatim. A silently
             truncated answer reads as a bug.

        Costs ~162 MB resident against the 8 GB box (Devices.md 6), which the
        budget carries. Returns False when the slice is absent, so a caller can
        fall back to the derived map rather than failing to start.
        """
        import pyarrow.parquet as pq

        from ..config import PASSAGES_PARQUET

        if not PASSAGES_PARQUET.exists():
            return False
        table = pq.read_table(PASSAGES_PARQUET, columns=["passage_id", "text"])
        self.passage_text = dict(
            zip(table.column("passage_id").to_pylist(), table.column("text").to_pylist())
        )
        return True

    def build_passage_map(self, chunks: list[ChunkRecord]) -> None:
        """Approximate fallback: reconstruct passage text from its chunks.

        Chunk 0 of a passage that produced only one chunk IS the passage. For
        multi-chunk passages the overlap makes naive concatenation wrong, so the
        longest chunk is used as the representative span.

        Superseded by load_passage_store() wherever the frozen slice is available.
        Kept because the tests build a Runtime from synthetic chunks with no slice
        on disk, and because a deployment that ships only an index directory should
        still answer rather than refuse to start.
        """
        best: dict[str, str] = {}
        for c in chunks:
            pid = c["passage_id"]
            if len(c["text"]) > len(best.get(pid, "")):
                best[pid] = c["text"]
        self.passage_text = best


def build_pipeline(rt: Runtime) -> Pipeline:
    # Handed the embedder's own tokenizer, because the count that matters is the
    # one the model will actually process, not an approximation of it.
    guard = InputGuard(
        count_tokens=rt.embedder.token_count,
        max_tokens=INPUT_MAX_TOKENS,
        max_chars=INPUT_MAX_CHARS,
    )

    async def input_guard(ctx: Context) -> Context:
        """Layer 1. Refuse before anything expensive happens.

        This runs first and it is the only thing bounding `embed_query`.
        ISSUES.md I25: a stage timeout cannot interrupt synchronous ONNX work, so
        by the time the embedder has started on a 512-token input the 118 ms is
        already spent. The refusal has to happen here or not at all.

        A rejection is an ABSTENTION, not an error. Requirement 6 asks the system
        to show that it knows when not to answer, and a 4xx is the system saying
        the caller made a mistake, which is a different claim.
        """
        verdict = guard.check(ctx.query)
        if verdict.ok:
            return ctx.with_data(input_tokens=verdict.tokens)
        return ctx.with_data(
            blocked=True,
            input_tokens=verdict.tokens,
            hits=[],
            route=Route("ABSTAIN", verdict.detail, abstain_reason=verdict.reason),
        )

    async def embed_query(ctx: Context) -> Context:
        # The point of the guard: a blocked question never reaches the model.
        if ctx.data.get("blocked"):
            return ctx
        try:
            vec = rt.embedder.encode_one(ctx.query, "query")
        except Exception as exc:  # noqa: BLE001 - re-raised as a typed error
            raise EmbedFailed(str(exc)) from exc
        return ctx.with_data(query_vector=vec)

    async def dense_search(ctx: Context) -> Context:
        if ctx.data.get("blocked"):
            return ctx
        vec: np.ndarray = ctx.data["query_vector"]
        rows = rt.index.search(vec, DENSE_TOP_K)
        hits = [(rt.index.chunk(row), score) for row, score in rows]
        return ctx.with_data(hits=hits)

    async def rerank(ctx: Context) -> Context:
        """Reorder the dense candidates with the cross-encoder.

        Deduplicates to distinct PASSAGES first. Two reasons, and the second is the
        one that bites: a passage that happened to emit three chunks would
        otherwise consume three of the twenty rerank slots and crowd out genuine
        alternatives, and the answer is shown as a passage anyway (ISSUES.md I22 is
        the same argument, made for the Phase 3 comparison).

        The candidates keep their ChunkRecord shape, so build_answer and the
        citation path downstream are untouched - only the ordering and the score
        change. Architecture.md 6: stages append, they do not rewrite each other.
        """
        hits: list[tuple[ChunkRecord, float]] = ctx.data.get("hits", [])
        if rt.reranker is None or not hits:
            return ctx

        best: dict[str, tuple[ChunkRecord, float]] = {}
        for chunk, score in hits:
            pid = chunk["passage_id"]
            if pid not in best:
                best[pid] = (chunk, score)
            if len(best) >= RERANK_TOP_K:
                break

        pairs = [
            (pid, rt.passage_text.get(pid) or str(chunk["text"]))
            for pid, (chunk, _) in best.items()
        ]
        # The stage's declared timeout_ms cannot stop this work (ISSUES.md I25:
        # asyncio.wait_for only fires at an await point and ONNX never yields), so
        # the deadline is handed to the reranker, which checks it between pairs.
        # Whatever the trace says is left, minus a small margin for the sort and
        # the answer stage that still have to run.
        deadline = max(ctx.trace.remaining_ms - RERANK_DEADLINE_MARGIN_MS, 0.0)
        ranked, scored = rt.reranker.rerank(ctx.query, pairs, deadline_ms=deadline)
        if scored < len(pairs):
            # The stage still closes "ok": it ran, and it returned every candidate
            # it was given, reordered as far as the budget allowed. What it did not
            # do is rerank all of them, and that has to be readable in the trace
            # rather than inferred from a suspiciously round stage time.
            ctx.trace.note(f"deadline: scored {scored} of {len(pairs)}")
        reordered = [(best[pid][0], score) for pid, score in ranked]
        return ctx.with_data(
            hits=reordered, reranked=True, rerank_scored=scored,
            rerank_partial=scored < len(pairs),
        )

    async def route_stage(ctx: Context) -> Context:
        # An earlier layer already decided. The router picks between paths for a
        # question that reached retrieval; it does not get to overturn a refusal
        # issued before the question was allowed in.
        if ctx.data.get("blocked"):
            return ctx
        hits = ctx.data.get("hits", [])
        top1 = hits[0][1] if hits else None
        gap = (hits[0][1] - hits[1][1]) if len(hits) > 1 else None
        # The breaker's state is part of the routing input, not an afterthought:
        # Rules.md 4 requires an open breaker to route to extractive rather than
        # to a call that will fail.
        available = (
            rt.groq is not None
            and rt.groq.configured
            and rt.groq.breaker.allows()
            and ctx.data.get("reranked", False)
        )
        # Thresholds are read from the module at CALL time, not bound as defaults
        # at import time. They are calibration output (scripts/06_calibrate_routing.py),
        # so they change when the reranker changes - and a value frozen into a
        # function signature at import cannot be re-pointed without a restart.
        # "fast" collapses the generative band to nothing by putting tau_high at
        # the floor: everything above the abstention threshold answers
        # extractively and no network call can happen. This is a routing change,
        # not a quality claim - the same passages come back either way, the
        # difference is whether an LLM composes over them.
        tau_high = (
            config.ROUTE_TAU_LOW if ctx.mode == "fast" else config.ROUTE_TAU_HIGH
        )
        decision = route(
            top1,
            gap,
            generative_available=available and ctx.mode == "accurate",
            tau_low=config.ROUTE_TAU_LOW,
            tau_high=tau_high,
        )
        return ctx.with_data(route=decision, top1=top1, score_gap=gap)

    async def answer_extractive(ctx: Context) -> Context:
        decision: Route | None = ctx.data.get("route")
        if decision is not None and decision.decision == "ABSTAIN":
            return ctx.with_data(answer=None, citations=[])

        hits = ctx.data.get("hits", [])
        # Answer in the language that was asked. ctx.language is "auto" unless the
        # caller pinned it, and the frontend does not pin it, so the script of the
        # query is what decides in practice.
        want = ctx.language if ctx.language in ("en", "hi") else detect_language(ctx.query)
        answer, citations, remapped = build_answer(hits, rt.passage_text, prefer_language=want)
        if remapped:
            # Visible in the waterfall for the same reason a truncated rerank is:
            # the reader is being shown a different passage id than the one that
            # was retrieved, and that should not be silent.
            ctx.trace.note(f"answered in {want}, {remapped} passage(s) swapped for their twin")
        top1 = ctx.data.get("top1", hits[0][1] if hits else None)
        gap = ctx.data.get("score_gap", (hits[0][1] - hits[1][1]) if len(hits) > 1 else None)
        return ctx.with_data(
            answer=answer, citations=citations, top1=top1, score_gap=gap
        )

    async def answer_generative(ctx: Context) -> Context:
        """The fallback path. Leaves the 200ms budget by construction.

        Runs only when the router chose it. The extractive answer is already in
        ctx by this point, so every failure mode here - timeout, 429, open breaker,
        the model reporting insufficient context - degrades to a real answer rather
        than to nothing. That is why this stage has no declared fallback: the
        fallback already happened, upstream.
        """
        decision: Route | None = ctx.data.get("route")
        if decision is None or decision.decision != "GENERATIVE" or rt.groq is None:
            return ctx

        try:
            text = await rt.groq.generate(ctx.query, ctx.data.get("citations", []))
        except UpstreamUnavailable:
            # Keep the extractive answer and say so. The path in the response
            # stays EXTRACTIVE because that is what the user actually received.
            return ctx.with_data(
                route=Route(
                    "EXTRACTIVE",
                    f"{decision.reason}; generative call failed, served extractive",
                )
            )

        if text is None:
            return ctx.with_data(
                answer=None,
                citations=[],
                route=Route(
                    "ABSTAIN",
                    "model reported insufficient context",
                    abstain_reason="UNGROUNDED_OUTPUT",
                ),
            )
        return ctx.with_data(answer=text)

    async def output_guard(ctx: Context) -> Context:
        """Layer 4. Check what came back against what it was supposed to come from.

        ISSUES.md I26 is the whole reason this is here rather than in a later
        phase. The abstention floor calibrated in Phase 5 is an out-of-domain
        detector: it refuses questions the corpus cannot answer and it cannot see
        whether an answer is supported, so 92.5% of wrong top-1 answers clear it.
        Nothing upstream of this stage looks at the answer text.

        The extractive path scores 1.0 by construction, because its answer is a
        span of the passage it cites. That is not a free pass, it is the
        measurement that turns "structurally incapable of hallucinating" from a
        claim into a number, so it is recorded rather than skipped.

        A failure here abstains rather than downgrading to the extractive answer.
        The generative path was chosen precisely because the router judged the
        top passage was NOT clearly the answer, so falling back to quoting it
        would return the thing we already decided was not good enough.
        """
        answer = ctx.data.get("answer")
        citations = ctx.data.get("citations", [])
        if not answer or not citations:
            return ctx

        score = groundedness(answer, [c.text for c in citations])
        bad_cites = invalid_citations(answer, len(citations))
        ctx = ctx.with_data(groundedness=score, invalid_citations=bad_cites)

        decision: Route | None = ctx.data.get("route")
        if decision is None or decision.decision != "GENERATIVE":
            # The extractive path cannot fail this: its answer IS the passage.
            # Guarding it would only ever fire on a bug in the span selector,
            # and refusing a verbatim quote for being insufficiently like itself
            # is not a failure mode worth building.
            return ctx

        if score < config.OUTPUT_MIN_GROUNDEDNESS:
            return ctx.with_data(
                answer=None,
                citations=[],
                route=Route(
                    "ABSTAIN",
                    f"groundedness {score:.2f} below the {config.OUTPUT_MIN_GROUNDEDNESS:.2f} floor",
                    abstain_reason="UNGROUNDED_OUTPUT",
                ),
            )
        if bad_cites:
            return ctx.with_data(
                answer=None,
                citations=[],
                route=Route(
                    "ABSTAIN",
                    f"cited passage {bad_cites[0]}, which was never retrieved",
                    abstain_reason="UNGROUNDED_OUTPUT",
                ),
            )
        return ctx

    return Pipeline(
        stages=[
            # required: a guard that can be skipped for budget reasons is not a
            # guard. It is also the cheapest stage in the pipeline, so the case
            # where it would not fit cannot arise honestly.
            FunctionStage(
                "input_guard",
                input_guard,
                timeout_ms=STAGE_TIMEOUT_MS["input_guard"],
                budget_ms=STAGE_BUDGET_MS["input_guard"],
                required=True,
            ),
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
            # Not required: if the cross-encoder times out or is absent, the
            # dense ordering is still a usable ordering. Quality degrades, the
            # skip is visible in the trace, and the request still answers.
            FunctionStage(
                "rerank",
                rerank,
                timeout_ms=STAGE_TIMEOUT_MS["rerank"],
                budget_ms=STAGE_BUDGET_MS["rerank"],
            ),
            FunctionStage(
                "route",
                route_stage,
                timeout_ms=STAGE_TIMEOUT_MS["route"],
                budget_ms=STAGE_BUDGET_MS["route"],
            ),
            FunctionStage(
                "answer_extractive",
                answer_extractive,
                timeout_ms=STAGE_TIMEOUT_MS["answer_extractive"],
                budget_ms=STAGE_BUDGET_MS["answer_extractive"],
            ),
            # Deliberately outside the 200ms budget_ms accounting: this stage only
            # runs when the router chose it, and Latency.md 3 publishes that path
            # as Band B rather than pretending it fits. Its budget_ms is set to 0
            # so the remaining-budget counter never skips it for being expensive -
            # the routing decision, not the clock, is what gates it.
            FunctionStage(
                "answer_generative",
                answer_generative,
                timeout_ms=GROQ_TIMEOUT_MS + 500.0,
                budget_ms=0.0,
            ),
            # Last, because it is the only stage that reads the answer. Not
            # required: if it cannot run, the answer that was already computed
            # is returned unchecked and the skip is visible in the trace, which
            # is a better outcome than refusing an answer we simply did not get
            # around to verifying.
            FunctionStage(
                "output_guard",
                output_guard,
                timeout_ms=STAGE_TIMEOUT_MS["output_guard"],
                budget_ms=STAGE_BUDGET_MS["output_guard"],
            ),
        ],
        budget_ms=BUDGET_MS,
    )
