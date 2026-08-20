"""The input guard, wired into the pipeline. Phase 6.

`tests/test_input_guard.py` proves the guard decides correctly in isolation.
This file proves the pipeline actually asks it, and that a rejected question
costs nothing downstream.

The second part is the point. ISSUES.md I1's pathological query costs 118 ms
inside `embed_query`, and ISSUES.md I25 established that a stage timeout cannot
interrupt it once it starts. So a guard that returns the right verdict while the
embedder runs anyway would be decoration. The assertion that matters is that the
embedder is never called.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.harness.pipeline import Context  # noqa: E402
from rag_core.harness.stages import Runtime, build_pipeline  # noqa: E402
from rag_core.harness.trace import Trace  # noqa: E402

PASSAGES = {"p1": "Mount Everest rises 8,849 metres above sea level."}


def chunk(pid: str):
    return {"chunk_id": f"{pid}-0", "passage_id": pid, "text": PASSAGES[pid],
            "language": "en", "meta": {}}


class CountingEmbedder:
    """Records every call, so "never embedded" is checkable rather than assumed."""

    def __init__(self) -> None:
        self.encode_calls = 0

    def token_count(self, text: str) -> int:
        return len(text.split())

    def encode_one(self, text: str, kind: str) -> np.ndarray:
        self.encode_calls += 1
        return np.zeros((384,), dtype=np.float32)


class FakeIndex:
    def __init__(self) -> None:
        self.chunks = [chunk("p1")]
        self.searches = 0

    def search(self, vec, k):
        self.searches += 1
        return [(0, 0.91)][:k]

    def chunk(self, row: int):
        return self.chunks[row]


def make():
    embedder = CountingEmbedder()
    index = FakeIndex()
    rt = Runtime(embedder, index)
    rt.build_passage_map(index.chunks)
    return embedder, index, build_pipeline(rt)


async def run(pipeline, query: str):
    return await pipeline.run(Context(query=query, trace=Trace(budget_ms=200.0)))


async def test_an_oversized_question_is_refused_without_embedding_it() -> None:
    embedder, index, pipeline = make()

    ctx = await run(pipeline, "x" * 7168)

    decision = ctx.data.get("route")
    assert decision is not None, "the guard did not produce a routing decision"
    assert decision.decision == "ABSTAIN"
    assert decision.abstain_reason == "UNSAFE_INPUT"
    assert ctx.data.get("answer") is None
    # The whole reason this layer exists.
    assert embedder.encode_calls == 0
    assert index.searches == 0


async def test_a_normal_question_passes_straight_through_the_guard() -> None:
    """The guard must be invisible to real traffic, or it is a quality problem
    wearing a safety label."""
    embedder, index, pipeline = make()

    ctx = await run(pipeline, "how tall is Mount Everest")

    assert embedder.encode_calls == 1
    assert index.searches == 1
    assert ctx.data.get("answer") == PASSAGES["p1"]


class FakeReranker:
    """Scores everything the same, high enough to clear whatever floor the test
    pins. What is under test here is the OUTPUT guard, not the routing."""

    def __init__(self, score: float = 2.0) -> None:
        self.score = score

    def rerank(self, query, candidates, top_k=None, deadline_ms=None):
        return [(pid, self.score) for pid, _ in candidates], len(candidates)


class FakeGroq:
    """Returns whatever the test wants the model to have said."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self._configured = True
        from rag_core.harness.policies import CircuitBreaker

        self.breaker = CircuitBreaker("fake")

    @property
    def configured(self) -> bool:
        return self._configured

    async def generate(self, query, citations):
        return self.reply


def make_generative(reply: str):
    embedder = CountingEmbedder()
    index = FakeIndex()
    rt = Runtime(embedder, index, reranker=FakeReranker(), groq=FakeGroq(reply))
    rt.build_passage_map(index.chunks)
    return rt, build_pipeline(rt)


async def test_an_ungrounded_model_answer_is_refused_rather_than_returned(monkeypatch) -> None:
    """ISSUES.md I26, made operational.

    The retrieval score cannot tell a supported answer from an unsupported one,
    so a model answer that has nothing to do with the passage it was handed will
    clear every threshold upstream of here. This is the layer that stops it.
    """
    from rag_core import config

    # Pin an explicit generative band so the router sends this to the model.
    # The shipped thresholds are calibration output and move; a wiring test that
    # read them would break for reasons unrelated to the wiring.
    monkeypatch.setattr(config, "ROUTE_TAU_LOW", -2.0)
    monkeypatch.setattr(config, "ROUTE_TAU_HIGH", 4.0)

    _, pipeline = make_generative("The Burj Khalifa in Dubai is 828 metres tall.")
    ctx = await pipeline.run(
        Context(query="how tall is Everest", mode="accurate", trace=Trace(budget_ms=200.0))
    )

    decision = ctx.data.get("route")
    assert decision.decision == "ABSTAIN"
    assert decision.abstain_reason == "UNGROUNDED_OUTPUT"
    assert ctx.data.get("answer") is None


async def test_a_grounded_model_answer_survives_the_guard(monkeypatch) -> None:
    """The guard has to be invisible when the model behaves, or it is just a
    second abstention floor with worse aim."""
    from rag_core import config

    monkeypatch.setattr(config, "ROUTE_TAU_LOW", -2.0)
    monkeypatch.setattr(config, "ROUTE_TAU_HIGH", 4.0)

    grounded = "Mount Everest rises 8,849 metres above sea level."
    _, pipeline = make_generative(grounded)
    ctx = await pipeline.run(
        Context(query="how tall is Everest", mode="accurate", trace=Trace(budget_ms=200.0))
    )

    assert ctx.data.get("answer") == grounded
    assert ctx.data["route"].decision == "GENERATIVE"


async def test_groundedness_is_recorded_even_when_it_passes() -> None:
    """The number is evidence, not a gate. An extractive answer is a verbatim
    span so it scores at the top, and showing that is the point: it is what
    makes "structurally incapable of hallucinating" a measurement rather than a
    claim."""
    _, _, pipeline = make()
    ctx = await run(pipeline, "how tall is Mount Everest")

    assert ctx.data.get("groundedness") == 1.0
