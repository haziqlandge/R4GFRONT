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
    AbstainReason,
    AnswerPath,
    AnswerRequest,
    AnswerResponse,
    AnswerStatus,
    Confidence,
    StageSpan,
    TraceView,
)
from .answering.generative import GroqClient
from .config import (
    ASIDE_RATE_LIMIT,
    ASIDE_RATE_WINDOW_SECONDS,
    BUDGET_MS,
    GROQ_MODEL,
    DEFAULT_STRATEGY,
    INT8_MODEL,
    ONNX_DIR,
    ONNX_THREADS_EMBED_SERVING,
    ONNX_THREADS_SERVING,
    RERANK_MODEL_FILE,
    RERANK_TOKENIZER_FILE,
    RERANKER,
    TOKENIZER_FILE,
    load_env,
)
from .harness.errors import InvalidQuery, RagCoreError
from .harness.pipeline import Context, Pipeline
from .harness.ratelimit import RateLimiter
from .harness.stages import Runtime, build_pipeline
from .harness.trace import Trace
from .retrieval.dense import DenseIndex
from .retrieval.embedder import Embedder
from .retrieval.rerank import CrossEncoder

STATE: dict[str, object] = {"ready": False}

# Module-level so it survives across requests. ISSUES.md I35, and the docstring
# in harness/ratelimit.py for why this is not the circuit breaker's job.
ASIDE_LIMIT: RateLimiter = RateLimiter(
    ASIDE_RATE_LIMIT, ASIDE_RATE_WINDOW_SECONDS, "groq-aside"
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    started = time.perf_counter()
    load_env()

    # One thread, not ONNX_THREADS_SERVING. Two ORT sessions on 4 vCPUs oversubscribe
    # and the embedder's spinning pool was costing the reranker half its cores; see
    # the measurement above ONNX_THREADS_EMBED_SERVING in config.py.
    embedder = Embedder(
        ONNX_DIR / INT8_MODEL,
        ONNX_DIR / TOKENIZER_FILE,
        threads=ONNX_THREADS_EMBED_SERVING,
    )
    index = DenseIndex(DEFAULT_STRATEGY)
    index.load()

    # The reranker is what turns retrieval into a correct answer (ISSUES.md I2),
    # but a missing file must not take the service down - dense-only is a degraded
    # mode, not a broken one, and /health reports which one is running.
    rerank_dir = ONNX_DIR / f"rerank-{RERANKER}"
    reranker: CrossEncoder | None = None
    if (rerank_dir / RERANK_MODEL_FILE).exists():
        reranker = CrossEncoder(
            rerank_dir / RERANK_MODEL_FILE,
            rerank_dir / RERANK_TOKENIZER_FILE,
            threads=ONNX_THREADS_SERVING,
        )

    groq = GroqClient()
    await groq.start()

    rt = Runtime(embedder, index, reranker=reranker, groq=groq)
    # Real passage text where the slice is present, the derived approximation
    # otherwise. ISSUES.md I9: the reranker and the extractive answer both consume
    # this, so the difference is now user-visible rather than cosmetic.
    exact = rt.load_passage_store()
    if not exact:
        rt.build_passage_map(index.chunks)
    pipeline = build_pipeline(rt)

    STATE["runtime"] = rt
    STATE["pipeline"] = pipeline
    STATE["strategy"] = DEFAULT_STRATEGY
    STATE["chunks"] = len(index.chunks)
    STATE["reranker"] = RERANKER if reranker is not None else None
    STATE["passage_store"] = "exact" if exact else "derived"
    STATE["generative"] = groq.configured
    # Which model the aside would answer with, or null when there is no key.
    # Reported for the same reason `reranker` is: a process that can serve the
    # panel and one that cannot are both "ok" and behave differently.
    STATE["aside_model"] = GROQ_MODEL if groq.configured else None

    # Warmup: a real query through the real pipeline, discarded. Now covers the
    # cross-encoder too, whose first inference is far slower than its steady state.
    warm = Context(query="what is a warmup query", trace=Trace(budget_ms=BUDGET_MS))
    await pipeline.run(warm)

    STATE["ready"] = True
    STATE["startup_seconds"] = round(time.perf_counter() - started, 2)
    try:
        yield
    finally:
        await groq.close()


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
            # Which capabilities actually came up. A dense-only process and a
            # fully-reranked one are both healthy but answer differently, and
            # that difference should be visible without reading the logs.
            "reranker": STATE.get("reranker"),
            "generative": STATE.get("generative"),
            "passage_store": STATE.get("passage_store"),
            # The aside's model and the per-client cap it is behind. ISSUES.md I35.
            "aside": {
                "model": STATE.get("aside_model"),
                "per_client_per_minute": ASIDE_RATE_LIMIT,
            },
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

    # The router owns the decision; this only reports it. Reading it back off
    # `answer` would lose the distinction between "abstained on low confidence"
    # and "retrieval returned nothing", which is exactly what AbstentionPanel.tsx
    # has to tell the user apart.
    decision = ctx.data.get("route")
    status: AnswerStatus
    path: AnswerPath
    abstain_reason: AbstainReason | None
    if decision is not None:
        status = "ABSTAINED" if decision.decision == "ABSTAIN" else "ANSWERED"
        path = decision.path
        abstain_reason = decision.abstain_reason
    else:
        status = "ANSWERED" if answer else "ABSTAINED"
        path = "EXTRACTIVE" if answer else "NONE"
        abstain_reason = None if answer else "LOW_CONFIDENCE"

    return AnswerResponse(
        trace_id=trace.trace_id,
        status=status,
        path=path,
        answer=answer,
        abstain_reason=abstain_reason,
        citations=citations,
        confidence=Confidence(
            rerank_top1=ctx.data.get("top1"),
            score_gap=ctx.data.get("score_gap"),
            # Phase 6. Declared in the contract since Phase 2 and null until now.
            # It is reported whether or not it gated anything, because the number
            # is the evidence: an extractive answer scoring 1.0 is what makes
            # "this cannot hallucinate" checkable rather than asserted.
            groundedness=ctx.data.get("groundedness"),
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
        mode=req.mode,
        trace=Trace(budget_ms=BUDGET_MS),
    )
    ctx = await pipeline.run(ctx)
    return _to_response(ctx, req.trace)


def client_key(request: Request) -> str:
    """Who "per user" means, given that /v1/aside has no login and no session.

    The client IP, and getting it right depends on where the request came from:

      - Deployed, every request arrives from Caddy on 127.0.0.1, so the socket
        peer is useless and X-Forwarded-For is the only signal.
      - Locally there is no proxy, the socket peer is the client, and any
        X-Forwarded-For present was put there by the client itself.

    THE RIGHTMOST ENTRY, NOT THE LEFTMOST. The usual reading of
    X-Forwarded-For is "leftmost is the original client", and it is wrong here.
    Caddy APPENDS the real peer to whatever header arrived, so a client sending
    `X-Forwarded-For: 1.2.3.4` produces `1.2.3.4, <their real IP>`. Trusting the
    leftmost would let anyone mint a fresh identity per request by varying one
    header, which is the entire limiter defeated by a one-line curl. With exactly
    one trusted proxy in front, the rightmost entry is the one Caddy wrote and
    the only one that cannot be forged.

    If a second proxy is ever put in front of Caddy, this becomes wrong in the
    other direction (it would bucket every client onto that proxy's IP) and needs
    a trusted-hop count. Recorded rather than pre-built - see ISSUES.md I35.
    A wrong answer here costs a shared bucket, never a wrong answer to a user.

    Falls back to "unknown" so a missing peer degrades to one shared bucket
    rather than to no limit at all.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        if hops:
            return hops[-1]
    return request.client.host if request.client else "unknown"


@app.post("/v1/aside")
async def aside(req: AnswerRequest, request: Request) -> dict[str, object]:
    """The model's own answer, with no retrieved context. NOT part of Band A.

    A separate endpoint rather than a field on /v1/answer, and that is the whole
    design. Band A is "transcript in to response serialized" and it must contain
    no network call; folding this into the answer would put a ~500 ms round trip
    inside the number the submission rests on. The browser paints our answer
    first and asks for this afterwards, so the measured path is untouched and a
    slow or dead upstream costs a panel that never appears.

    ONE UPSTREAM: `openai/gpt-oss-20b` on Groq, capped at ASIDE_MAX_TOKENS (240),
    answering from its own training-time memory with no retrieval at all. A
    Gemini primary with google_search grounding was built and removed on 21 Aug -
    ISSUES.md I35 records why, and why "the panel shows CURRENT facts" is not a
    claim this endpoint can make.

    BEHIND A PER-CLIENT CAP of ASIDE_RATE_LIMIT calls per minute (ISSUES.md I35).
    The aside and the real generative fallback spend the same 12,000-token window
    (I7), so an unbounded panel is a way for one visitor to take Band B away from
    everyone. A capped client is refused here, before the network, which is also
    what stops them recording failures against a breaker protecting other
    visitors.

    `configured` is checked BEFORE the limiter, not after. An unkeyed upstream
    would otherwise burn a slot from a window it can never use, so a deployment
    with no Groq key would spend a budget on nothing.

    Never returns an error to the caller. `text: null` means "no aside", which
    the page renders as nothing at all - a rate-limited visitor sees exactly what
    a visitor with a dead upstream sees, and neither sees a failure.

    `model` names the model that answered, and the page prints it in the panel
    footer. An unattributed panel would be the one unlabelled claim on a site
    whose whole pitch is that every figure names its source.
    """
    if not STATE.get("ready"):
        raise InvalidQuery("service still warming")
    rt: Runtime = STATE["runtime"]  # type: ignore[assignment]

    if rt.groq is None or not rt.groq.configured:
        return {"text": None, "model": None}

    if not ASIDE_LIMIT.allow(client_key(request)):
        return {"text": None, "model": None}

    text = await rt.groq.aside(req.query)
    return {"text": text, "model": GROQ_MODEL if text else None}


@app.exception_handler(RagCoreError)
async def rag_error_handler(request: Request, exc: RagCoreError) -> ORJSONResponse:
    """Typed errors become typed responses. Architecture.md 6.2: never an
    unexplained 500."""
    return ORJSONResponse(
        status_code=503 if not exc.degradable else 200,
        content={"error": type(exc).__name__, "detail": str(exc)},
    )
