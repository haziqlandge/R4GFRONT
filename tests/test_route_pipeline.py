"""Rerank / route / generate wiring. Phase 5.

These use fakes rather than the real ONNX model and the real Groq endpoint, on
purpose: what is under test is the CONTROL FLOW between the stages - which path
runs, what survives a failure, what the user gets when the network is gone. The
model's quality is measured in scripts/05d_eval_rerank.py and the endpoint's
behaviour in the Band B benchmark; neither belongs in a unit test.

The property worth protecting is the one Architecture.md 6.2 states: never a 500,
and never nothing. Every failure on the fallback path must still return the answer
the extractive path already computed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.harness.errors import UpstreamUnavailable  # noqa: E402
from rag_core.harness.pipeline import Context  # noqa: E402
from rag_core.harness.policies import CircuitBreaker  # noqa: E402
from rag_core.harness.stages import Runtime, build_pipeline  # noqa: E402
from rag_core.harness.trace import Trace  # noqa: E402

PASSAGES = {
    "p1": "Mount Everest rises 8,849 metres above sea level.",
    "p2": "Cheese is made from milk.",
    "p3": "The Pacific is the largest ocean.",
}


def chunk(pid: str):
    return {"chunk_id": f"{pid}-0", "passage_id": pid, "text": PASSAGES[pid],
            "language": "en", "meta": {}}


class FakeEmbedder:
    def encode_one(self, text: str, kind: str) -> np.ndarray:
        return np.zeros((384,), dtype=np.float32)


class FakeIndex:
    """Returns p2, p3, p1 - deliberately the WRONG order, so a test that passes
    without the reranker running cannot pass by accident."""

    def __init__(self) -> None:
        self.chunks = [chunk("p2"), chunk("p3"), chunk("p1")]

    def search(self, vec, k):
        return [(0, 0.91), (1, 0.90), (2, 0.89)][:k]

    def chunk(self, row: int):
        return self.chunks[row]


class FakeReranker:
    """Scores by keyword, so the intended winner is unambiguous.

    Mirrors the real signature including `deadline_ms` and the (ranked, n_scored)
    return, and records the deadline it was handed - the stage passing a deadline
    at all is the ISSUES.md I25 mitigation, so it is worth asserting on.
    """

    def __init__(self, score_for_top: float = 9.0) -> None:
        self.score_for_top = score_for_top
        self.calls = 0
        self.last_deadline_ms: float | None = None

    def rerank(self, query, candidates, top_k=None, deadline_ms=None):
        self.calls += 1
        self.last_deadline_ms = deadline_ms
        out = []
        for pid, text in candidates:
            s = self.score_for_top if "Everest" in text else -6.0
            out.append((pid, s))
        return sorted(out, key=lambda x: -x[1]), len(candidates)


class FakeGroq:
    def __init__(self, reply="composed answer", fail=False, configured=True) -> None:
        self.reply = reply
        self.fail = fail
        self._configured = configured
        self.breaker = CircuitBreaker("fake")
        self.calls = 0

    @property
    def configured(self) -> bool:
        return self._configured

    async def generate(self, query, citations):
        self.calls += 1
        if self.fail:
            raise UpstreamUnavailable("fake", "boom")
        return self.reply


def make(reranker=None, groq=None):
    rt = Runtime(FakeEmbedder(), FakeIndex(), reranker=reranker, groq=groq)
    rt.build_passage_map(rt.index.chunks)
    return rt, build_pipeline(rt)


async def run(pipeline, query="how tall is Mount Everest", mode="accurate"):
    """Defaults to accurate here so the wiring tests exercise the full path.

    The SHIPPED default is "fast" (schemas.AnswerRequest) - see the dedicated mode
    tests below, which pin that separately. A wiring test that inherited the
    product default would silently stop covering the generative branch the day
    that default changed, which is exactly what happened when it did.
    """
    return await pipeline.run(
        Context(query=query, mode=mode, trace=Trace(budget_ms=200.0))
    )


@pytest.fixture
def band(monkeypatch):
    """Pin an explicit generative band and return a score inside it.

    The shipped thresholds are calibration OUTPUT (scripts/06_calibrate_routing.py)
    and move whenever the reranker or the corpus changes. A wiring test that read
    them would be testing the calibration, and would break for a reason that has
    nothing to do with the control flow it is meant to protect.
    """
    from rag_core import config

    monkeypatch.setattr(config, "ROUTE_TAU_LOW", -2.0)
    monkeypatch.setattr(config, "ROUTE_TAU_HIGH", 4.0)
    return 1.0


async def test_reranker_reorders_the_answer() -> None:
    """The whole point of Phase 5: dense puts p2 first, the reranker puts p1 first,
    and the answer follows the reranker."""
    _, pipeline = make(reranker=FakeReranker())
    ctx = await run(pipeline)
    assert ctx.data["answer"] == PASSAGES["p1"]
    assert ctx.data["citations"][0].passage_id == "p1"


async def test_without_a_reranker_the_dense_order_still_answers() -> None:
    """Dense-only is a degraded mode, not a broken one."""
    _, pipeline = make(reranker=None)
    ctx = await run(pipeline)
    assert ctx.data["answer"] == PASSAGES["p2"]


async def test_high_confidence_never_calls_the_network() -> None:
    """Rules.md 2.1: 'the extractive path is defined by not making an LLM call.'"""
    groq = FakeGroq()
    _, pipeline = make(reranker=FakeReranker(score_for_top=9.0), groq=groq)
    ctx = await run(pipeline)
    assert ctx.data["route"].decision == "EXTRACTIVE"
    assert groq.calls == 0


async def test_low_confidence_abstains_with_no_answer() -> None:
    groq = FakeGroq()
    _, pipeline = make(reranker=FakeReranker(score_for_top=-9.0), groq=groq)
    ctx = await run(pipeline)
    assert ctx.data["route"].decision == "ABSTAIN"
    assert ctx.data["answer"] is None
    assert ctx.data["citations"] == []
    assert groq.calls == 0, "an abstention must not spend a network call"


async def test_generative_failure_still_returns_the_extractive_answer(band) -> None:
    """Architecture.md 6.2. The extractive answer is computed BEFORE the network
    call, so a dead upstream costs quality, never the response."""
    groq = FakeGroq(fail=True)
    _, pipeline = make(reranker=FakeReranker(score_for_top=band), groq=groq)
    ctx = await run(pipeline)
    assert groq.calls == 1
    assert ctx.data["answer"] == PASSAGES["p1"], "the extractive answer survives"
    assert ctx.data["route"].decision == "EXTRACTIVE"
    assert "failed" in ctx.data["route"].reason


async def test_open_breaker_routes_around_the_network_entirely(band) -> None:
    """Rules.md 4, HARD: a 429 opens the breaker and routes to extractive."""
    groq = FakeGroq()
    groq.breaker.trip()
    _, pipeline = make(reranker=FakeReranker(score_for_top=band), groq=groq)
    ctx = await run(pipeline)
    assert groq.calls == 0
    assert ctx.data["route"].decision == "EXTRACTIVE"
    assert ctx.data["answer"] == PASSAGES["p1"]


async def test_rerank_emits_a_span_so_the_waterfall_shows_it() -> None:
    """Rules.md 2.2: every stage emits a span. LatencyWaterfall.tsx renders these."""
    _, pipeline = make(reranker=FakeReranker())
    ctx = await run(pipeline)
    names = [s.name for s in ctx.trace.spans]
    assert "rerank" in names
    assert "route" in names


async def test_rerank_deduplicates_to_distinct_passages() -> None:
    """A passage emitting several chunks must not consume several rerank slots."""
    rt, pipeline = make(reranker=FakeReranker())
    rt.index.chunks = [chunk("p2"), chunk("p2"), chunk("p1")]
    rt.build_passage_map(rt.index.chunks)
    ctx = await run(pipeline)
    pids = [c["passage_id"] for c, _ in ctx.data["hits"]]
    assert len(pids) == len(set(pids))


async def test_rerank_is_handed_a_deadline() -> None:
    """ISSUES.md I25: the stage's timeout_ms cannot interrupt sync ONNX work, so
    the only real bound is the deadline the reranker checks between pairs. If the
    stage stops passing one, the 200ms budget silently stops being enforced."""
    rr = FakeReranker()
    _, pipeline = make(reranker=rr)
    await run(pipeline)
    assert rr.last_deadline_ms is not None
    assert 0.0 < rr.last_deadline_ms < 200.0


# -- the A6/D2 mode toggle ---------------------------------------------------
#
# Assumption A6 measured false in Phase 5 (reranked top-1 correct ~40% of the
# time). D2's recorded reversal condition was to expose the fast path as a mode
# rather than let it be the silent default. These pin that contract.


async def test_fast_mode_never_calls_the_network(band) -> None:
    """Even at a score squarely inside the generative band, fast mode must not
    make a network call. This is the whole meaning of "fast"."""
    groq = FakeGroq()
    _, pipeline = make(reranker=FakeReranker(score_for_top=band), groq=groq)
    ctx = await run(pipeline, mode="fast")
    assert groq.calls == 0
    assert ctx.data["route"].decision == "EXTRACTIVE"
    assert ctx.data["answer"] == PASSAGES["p1"]


async def test_accurate_mode_does_call_the_network(band) -> None:
    groq = FakeGroq()
    _, pipeline = make(reranker=FakeReranker(score_for_top=band), groq=groq)
    ctx = await run(pipeline, mode="accurate")
    assert groq.calls == 1
    assert ctx.data["route"].decision == "GENERATIVE"


async def test_fast_mode_still_abstains_below_the_floor() -> None:
    """The mode must not become a way to answer anyway. Requirement 6 outranks
    the latency story: a low-confidence query refuses in both modes."""
    groq = FakeGroq()
    _, pipeline = make(reranker=FakeReranker(score_for_top=-9.0), groq=groq)
    ctx = await run(pipeline, mode="fast")
    assert ctx.data["route"].decision == "ABSTAIN"
    assert ctx.data["answer"] is None


async def test_shipped_default_is_fast() -> None:
    """The published 200ms figure is measured on the extractive path, and Groq's
    free tier serves ~12 calls per window (ISSUES.md I7). Defaulting to a path
    that cannot serve traffic would be the worse dishonesty."""
    from rag_core.answering.schemas import AnswerRequest

    assert AnswerRequest(query="q").mode == "fast"
