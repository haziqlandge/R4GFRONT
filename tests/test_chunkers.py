"""C1 chunker tests. Rules.md 6: tests for the harness, the chunkers and the
guardrails."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from rag_core.chunking.base import Chunk, Chunker  # noqa: E402
from rag_core.chunking.c1_fixed import FixedChunker  # noqa: E402
from rag_core.config import INT8_MODEL, ONNX_DIR, TOKENIZER_FILE  # noqa: E402
from rag_core.retrieval.embedder import Embedder  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (ONNX_DIR / TOKENIZER_FILE).exists(),
    reason="run scripts/03_export_onnx.py first",
)


@pytest.fixture(scope="module")
def embedder() -> Embedder:
    return Embedder(ONNX_DIR / INT8_MODEL, ONNX_DIR / TOKENIZER_FILE, threads=2)


@pytest.fixture(scope="module")
def chunker(embedder: Embedder) -> FixedChunker:
    return FixedChunker(embedder)


def passage(text: str, pid: str = "1:0:en") -> dict:
    return {
        "passage_id": pid,
        "text": text,
        "language": "en",
        "parallel_id": pid.rsplit(":", 1)[0],
    }


def test_satisfies_protocol(chunker: FixedChunker) -> None:
    assert isinstance(chunker, Chunker)


def test_short_passage_is_one_chunk(chunker: FixedChunker) -> None:
    """The common case on this corpus: p50 is 77 tokens, under the 96 window."""
    out = chunker.chunk_one(passage("The androgen receptor is a nuclear receptor."))
    assert len(out) == 1
    assert out[0].ordinal == 0
    assert out[0].truncated is False


def test_chunk_text_is_verbatim_substring(chunker: FixedChunker) -> None:
    """Chunks are sliced from the source by char offset, never decoded from token
    ids - the extractive path returns this text to the user."""
    text = "Quantum entanglement is a physical phenomenon. " * 20
    for c in chunker.chunk_one(passage(text)):
        assert c.text in text


def test_long_passage_splits_with_overlap(chunker: FixedChunker) -> None:
    text = " ".join(f"sentence number {i} about something specific." for i in range(120))
    out = chunker.chunk_one(passage(text))
    assert len(out) > 1
    assert [c.ordinal for c in out] == list(range(len(out)))
    # Consecutive chunks must share content, or the window/stride maths is wrong.
    assert out[0].text[-20:] and any(
        w in out[1].text for w in out[0].text.split()[-5:]
    )


def test_truncation_cap_applied_and_counted(embedder: Embedder) -> None:
    """The 4,093-word Hindi repetition-loop passage is why this cap exists."""
    ch = FixedChunker(embedder, max_tokens=96)
    text = " ".join(f"token{i}" for i in range(3000))
    out = ch.chunk_one(passage(text))
    assert all(c.truncated for c in out)
    assert ch.truncated_count == 1
    assert sum(c.token_count for c in out) <= 96


def test_empty_passage_yields_nothing(chunker: FixedChunker) -> None:
    assert chunker.chunk_one(passage("   ")) == []


def test_metadata_is_carried_to_every_chunk(chunker: FixedChunker) -> None:
    p = passage("A passage about something.", pid="42:3:hi")
    p["language"] = "hi"
    for c in chunker.chunk_one(p):
        assert c.passage_id == "42:3:hi"
        assert c.parallel_id == "42:3"
        assert c.language == "hi"
        assert c.chunk_id.startswith("42:3:hi#")


def test_chunk_is_frozen() -> None:
    c = Chunk(
        chunk_id="a#0", text="x", passage_id="a", parallel_id="a",
        language="en", ordinal=0, token_count=1,
    )
    with pytest.raises(Exception):
        c.text = "y"  # type: ignore[misc]


def test_overlap_must_be_smaller_than_window(embedder: Embedder) -> None:
    with pytest.raises(ValueError):
        FixedChunker(embedder, window=32, overlap=32)


def test_params_recorded_for_reproducibility(chunker: FixedChunker) -> None:
    p = chunker.params()
    assert p["window_tokens"] == 96 and p["overlap_tokens"] == 24
    assert p["strategy"] == "c1"
