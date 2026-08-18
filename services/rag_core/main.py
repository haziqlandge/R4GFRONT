"""rag_core FastAPI service. The 200ms budget lives entirely inside this process.

Startup order matters and is deliberate (Architecture.md 10): the ONNX session,
the index and the passage map all load, then ONE full query runs through the
whole pipeline, and only then does /health go green. A cold ONNX session's first
inference is far slower than its steady state, so without that warmup the first
real request pays for it - and on a freshly deployed container the first real
request is usually the judge's.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import orjson
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from .answering.schemas import (
    AnswerRequest,
    AnswerResponse,
    Confidence,
    StageSpan,
    TraceView,
)
from .config import (
    BUDGET_MS,
    DEFAULT_STRATEGY,
    INT8_MODEL,
    ONNX_DIR,
    ONNX_THREADS_SERVING,
    TOKENIZER_FILE,
)
from .harness.errors import InvalidQuery, RagCoreError
from .harness.pipeline import Context, Pipeline
from .harness.stages import Runtime, build_pipeline
from .harness.trace import Trace
from .retrieval.dense import DenseIndex
from .retrieval.embedder import Embedder

STATE: dict[str, object] = {"ready": False}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    started = time.perf_counter()

    embedder = Embedder(
        ONNX_DIR / INT8_MODEL,
        ONNX_DIR / TOKENIZER_FILE,
        threads=ONNX_THREADS_SERVING,
    )
    index = DenseIndex(DEFAULT_STRATEGY)
    index.load()

    rt = Runtime(embedder, index)
    rt.build_passage_map(index.chunks)
    pipeline = build_pipeline(rt)

    STATE["runtime"] = rt
    STATE["pipeline"] = pipeline
    STATE["strategy"] = DEFAULT_STRATEGY
    STATE["chunks"] = len(index.chunks)

    # Warmup: a real query through the real pipeline, discarded.
    warm = Context(query="what is a warmup query", trace=Trace(budget_ms=BUDGET_MS))
    await pipeline.run(warm)

    STATE["ready"] = True
    STATE["startup_seconds"] = round(time.perf_counter() - started, 2)
    yield


app = FastAPI(title="Shruti rag_core", default_response_class=ORJSONResponse,
              lifespan=lifespan)

# The browser calls this directly for the text-input fallback (F16). Keys never
# reach the browser (Rules.md 4); this endpoint holds none.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> ORJSONResponse:
    """Unhealthy until warmup completes, so a load balancer never routes to a
    cold process. Architecture.md 10."""
    ready = bool(STATE.get("ready"))
    return ORJSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ok" if ready else "warming",
            "strategy": STATE.get("strategy"),
            "chunks": STATE.get("chunks"),
            "startup_seconds": STATE.get("startup_seconds"),
        },
    )


def _to_response(ctx: Context, want_trace: bool) -> AnswerResponse:
    answer = ctx.data.get("answer")
    citations = ctx.data.get("citations", [])
    trace = ctx.trace

    view = None
    if want_trace:
        view = TraceView(
            total_ms=round(trace.total_ms, 3),
            budget_ms=trace.budget_ms,
            stages=[
                StageSpan(
                    name=s.name, ms=round(s.ms, 3), status=s.status, detail=s.detail
                )
                for s in trace.spans
            ],
        )

    return AnswerResponse(
        trace_id=trace.trace_id,
        # Phase 2 has no confidence floor yet - routing and abstention arrive in
        # Phase 5 and 6. Until then an empty retrieval is the only ABSTAIN case,
        # and it is reported honestly rather than dressed up as an answer.
        status="ANSWERED" if answer else "ABSTAINED",
        path="EXTRACTIVE" if answer else "NONE",
        answer=answer,
        abstain_reason=None if answer else "LOW_CONFIDENCE",
        citations=citations,
        confidence=Confidence(
            rerank_top1=ctx.data.get("top1"), score_gap=ctx.data.get("score_gap")
        ),
        trace=view,
    )


@app.post("/v1/answer", response_model=AnswerResponse)
async def answer(req: AnswerRequest) -> AnswerResponse:
    if not STATE.get("ready"):
        raise InvalidQuery("service still warming")
    pipeline: Pipeline = STATE["pipeline"]  # type: ignore[assignment]

    ctx = Context(
        query=req.query,
        language=req.language,
        strategy=req.strategy,
        trace=Trace(budget_ms=BUDGET_MS),
    )
    ctx = await pipeline.run(ctx)
    return _to_response(ctx, req.trace)


@app.exception_handler(RagCoreError)
async def rag_error_handler(request: Request, exc: RagCoreError) -> ORJSONResponse:
    """Typed errors become typed responses. Architecture.md 6.2: never an
    unexplained 500."""
    return ORJSONResponse(
        status_code=503 if not exc.degradable else 200,
        content={"error": type(exc).__name__, "detail": str(exc)},
    )
