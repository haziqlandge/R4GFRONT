"""C8 late-chunking tests. Job J8.

C8's entire claim is "same spans as C1, different context". If the spans drift,
the C8-vs-C1 comparison stops being single-variable and answers nothing, so that
equivalence is the first thing pinned here.

The vectors differ, not the chunks: C8 encodes the whole passage once and
mean-pools each span out of the token-level hidden states, so every chunk vector
carries surrounding context. That pooling is exercised in the build script test
below.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(ROOT))

from rag_core.chunking.base import Chunker  # noqa: E402
from rag_core.chunking.c1_fixed import FixedChunker  # noqa: E402
from rag_core.chunking.c8_late import LateChunker  # noqa: E402
from rag_core.config import INT8_MODEL, ONNX_DIR, TOKENIZER_FILE  # noqa: E402
from rag_core.retrieval.embedder import Embedder  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (ONNX_DIR / TOKENIZER_FILE).exists(), reason="run scripts/03_export_onnx.py first"
)


@pytest.fixture(scope="module")
def embedder() -> Embedder:
    return Embedder(ONNX_DIR / INT8_MODEL, ONNX_DIR / TOKENIZER_FILE, threads=2)


def passage(text: str, pid: str = "1:0:en") -> dict:
    return {"passage_id": pid, "text": text, "language": "en",
            "parallel_id": pid.rsplit(":", 1)[0]}


LONG = " ".join(f"sentence number {i} about a specific topic." for i in range(60))


def test_satisfies_chunker_protocol(embedder: Embedder) -> None:
    assert isinstance(LateChunker(embedder), Chunker)


def test_spans_are_identical_to_c1(embedder: Embedder) -> None:
    """The single-variable guarantee. If this fails, C8 vs C1 measures two
    changes at once and the comparison is void."""
    c1 = FixedChunker(embedder).chunk_one(passage(LONG))
    c8 = LateChunker(embedder).chunk_one(passage(LONG))
    assert [c.chunk_id for c in c8] == [c.chunk_id for c in c1]
    assert [c.text for c in c8] == [c.text for c in c1]


def test_records_token_span_for_each_chunk(embedder: Embedder) -> None:
    """The builder needs to know which token positions to pool for each chunk."""
    chunks = LateChunker(embedder).chunk_one(passage(LONG))
    assert len(chunks) > 1
    for c in chunks:
        assert "tok_start" in c.meta and "tok_end" in c.meta
        assert int(c.meta["tok_end"]) > int(c.meta["tok_start"])


def test_spans_are_contiguous_and_ordered(embedder: Embedder) -> None:
    chunks = LateChunker(embedder).chunk_one(passage(LONG))
    starts = [int(c.meta["tok_start"]) for c in chunks]
    assert starts == sorted(starts)
    assert starts[0] == 0


def test_span_end_never_exceeds_passage_token_count(embedder: Embedder) -> None:
    """Pooling past the end of last_hidden_state would silently read padding."""
    text = LONG
    n_tokens = len(embedder.tokenizer.encode(text, add_special_tokens=False).ids)
    for c in LateChunker(embedder).chunk_one(passage(text)):
        assert int(c.meta["tok_end"]) <= n_tokens


def test_short_passage_yields_single_full_span(embedder: Embedder) -> None:
    chunks = LateChunker(embedder).chunk_one(passage("A short passage."))
    assert len(chunks) == 1
    assert int(chunks[0].meta["tok_start"]) == 0


def test_length_cap_is_applied(embedder: Embedder) -> None:
    """Architecture.md 4.1's cap, specified since Phase 1 and never implemented
    until now. Without it the 4,093-word degenerate Hindi passage dominates."""
    ch = LateChunker(embedder, max_tokens=96)
    chunks = ch.chunk_one(passage(" ".join(f"token{i}" for i in range(3000))))
    assert all(c.truncated for c in chunks)
    assert max(int(c.meta["tok_end"]) for c in chunks) <= 96


def test_params_records_strategy_and_cap(embedder: Embedder) -> None:
    p = LateChunker(embedder).params()
    assert p["strategy"] == "c8"
    assert "max_passage_tokens" in p
