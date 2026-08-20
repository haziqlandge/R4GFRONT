"""C3, semantic breakpoint chunking. See chunking/c3_semantic.py.

The two traps the strategy's own spec calls out are what most of these pin:
the threshold is corpus-wide rather than per passage, and chunk text is sliced
from the source by character offset rather than decoded from token ids.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.chunking.c3_semantic import (  # noqa: E402
    SemanticChunker,
    sentence_spans,
)


class FakeEmbedder:
    """Deterministic stand-in. Vectors come from a caller-supplied table.

    The real embedder is a 90 MB ONNX session; these tests are about the
    grouping logic, which must be testable without it.
    """

    def __init__(self, table: dict[str, list[float]] | None = None) -> None:
        self.table = table or {}
        self.tokenizer = _FakeTokenizer()
        self.batches: list[int] = []

    def encode(self, texts: list[str], kind: str) -> np.ndarray:
        self.batches.append(len(texts))
        out = np.zeros((len(texts), 3), dtype=np.float32)
        for i, t in enumerate(texts):
            v = np.asarray(self.table.get(t.strip(), [1.0, 0.0, 0.0]), dtype=np.float32)
            out[i] = v / (np.linalg.norm(v) or 1.0)
        return out


class _FakeEncoding:
    def __init__(self, text: str) -> None:
        # One "token" per character keeps offsets trivial and exact.
        self.ids = list(range(len(text)))
        self.offsets = [(i, i + 1) for i in range(len(text))]


class _FakeTokenizer:
    def encode(self, text: str, add_special_tokens: bool = True) -> _FakeEncoding:
        return _FakeEncoding(text)


def passage(pid: str, text: str, language: str = "en") -> dict:
    return {
        "passage_id": pid,
        "text": text,
        "language": language,
        "parallel_id": pid.rsplit(":", 1)[0],
    }


# -- segmentation -------------------------------------------------------------


def test_spans_slice_the_source_verbatim() -> None:
    text = "First one. Second one. Third one."
    spans = sentence_spans(text, "en")
    assert [text[a:b] for a, b in spans] == ["First one.", "Second one.", "Third one."]


def test_hindi_splits_on_the_danda_and_keeps_it() -> None:
    text = "पहला वाक्य। दूसरा वाक्य। तीसरा वाक्य।"
    spans = sentence_spans(text, "hi")
    pieces = [text[a:b] for a, b in spans]
    assert len(pieces) == 3
    assert all(p.endswith("।") for p in pieces)


def test_a_hindi_passage_is_not_one_sentence() -> None:
    """The silent-quality failure: a period splitter leaves Hindi undivided."""
    text = "पहला वाक्य। दूसरा वाक्य।"
    assert len(sentence_spans(text, "hi")) == 2


def test_no_terminal_punctuation_is_still_one_sentence() -> None:
    assert [t for t in sentence_spans("no punctuation here", "en")] == [(0, 19)]


def test_empty_and_whitespace_produce_no_spans() -> None:
    assert sentence_spans("", "en") == []
    assert sentence_spans("   \n  ", "en") == []


# -- grouping -----------------------------------------------------------------


def test_similar_sentences_stay_in_one_chunk() -> None:
    """No gap clears the bar, so the passage emits a single chunk."""
    table = {"A one.": [1, 0, 0], "A two.": [1, 0, 0], "A three.": [1, 0, 0]}
    ch = SemanticChunker(FakeEmbedder(table), percentile=92.0)
    chunks = ch.chunk([passage("p:0:en", "A one. A two. A three.")])
    assert len(chunks) == 1
    assert chunks[0].text == "A one. A two. A three."


def test_a_semantic_jump_cuts_the_passage() -> None:
    table = {
        "Cats purr.": [1, 0, 0],
        "Cats sleep.": [1, 0, 0],
        "Steel rusts.": [0, 1, 0],
        "Steel bends.": [0, 1, 0],
    }
    ch = SemanticChunker(FakeEmbedder(table), percentile=50.0)
    chunks = ch.chunk([passage("p:0:en", "Cats purr. Cats sleep. Steel rusts. Steel bends.")])
    assert [c.text for c in chunks] == ["Cats purr. Cats sleep.", "Steel rusts. Steel bends."]
    assert [c.ordinal for c in chunks] == [0, 1]
    assert chunks[0].meta["sentences"] == "2"


def test_threshold_is_corpus_wide_not_per_passage() -> None:
    """THE trap in the spec.

    Passage A has one small gap and passage B one large one. Per passage, each
    would see its own single gap at the 92nd percentile and cut - splitting A
    even though its gap is tiny in corpus terms. One corpus-wide threshold cuts
    only B.
    """
    table = {
        "A one.": [1.0, 0.0, 0.0],
        "A two.": [0.999, 0.045, 0.0],   # nearly identical, tiny gap
        "B one.": [1.0, 0.0, 0.0],
        "B two.": [0.0, 1.0, 0.0],       # orthogonal, large gap
    }
    ch = SemanticChunker(FakeEmbedder(table), percentile=92.0)
    chunks = ch.chunk([
        passage("a:0:en", "A one. A two."),
        passage("b:0:en", "B one. B two."),
    ])
    by_passage: dict[str, int] = {}
    for c in chunks:
        by_passage[c.passage_id] = by_passage.get(c.passage_id, 0) + 1
    assert by_passage["a:0:en"] == 1, "the small gap must not cut"
    assert by_passage["b:0:en"] == 2, "the large gap must cut"


def test_threshold_and_gap_count_are_recorded_for_reproducibility() -> None:
    table = {"A one.": [1, 0, 0], "A two.": [0, 1, 0]}
    ch = SemanticChunker(FakeEmbedder(table))
    assert ch.params()["breakpoint_threshold"] is None
    ch.chunk([passage("a:0:en", "A one. A two.")])
    p = ch.params()
    assert isinstance(p["breakpoint_threshold"], float)
    assert p["gaps_measured"] == 1
    assert p["breakpoint_percentile"] == 92.0


def test_single_sentence_passage_yields_one_chunk_and_no_gap() -> None:
    ch = SemanticChunker(FakeEmbedder({"Only one.": [1, 0, 0]}))
    chunks = ch.chunk([passage("p:0:en", "Only one.")])
    assert len(chunks) == 1
    assert ch.params()["gaps_measured"] == 0


def test_empty_passage_yields_nothing() -> None:
    ch = SemanticChunker(FakeEmbedder())
    assert ch.chunk([passage("p:0:en", "   ")]) == []


# -- the shared rules every strategy must follow ------------------------------


def test_chunk_text_is_a_verbatim_slice_of_the_source() -> None:
    """Never a decode round-trip: the extractive path returns this to the user."""
    text = "Odd  spacing here. And   another one."
    table = {"Odd  spacing here.": [1, 0, 0], "And   another one.": [0, 1, 0]}
    ch = SemanticChunker(FakeEmbedder(table), percentile=50.0)
    for c in ch.chunk([passage("p:0:en", text)]):
        assert c.text in text, f"{c.text!r} is not a slice of the source"


def test_the_length_cap_applies_before_chunking() -> None:
    """One Hindi passage is 4,093 words; uncapped it dominates the build."""
    text = ". ".join(f"Sentence number {i}" for i in range(40)) + "."
    ch = SemanticChunker(FakeEmbedder(), max_tokens=30)  # 30 "tokens" = 30 chars
    chunks = ch.chunk([passage("p:0:en", text)])
    assert ch.truncated_count == 1
    assert all(c.truncated for c in chunks)
    assert all(c.text in text[:30] for c in chunks)


def test_batches_respect_the_configured_size() -> None:
    emb = FakeEmbedder({f"S{i}.": [1, 0, 0] for i in range(20)})
    ch = SemanticChunker(emb, batch=4)
    ch.chunk([passage(f"p:{i}:en", " ".join(f"S{j}." for j in range(5))) for i in range(4)])
    assert emb.batches and max(emb.batches) <= 4


def test_identical_input_gives_identical_output() -> None:
    """Determinism: same corpus in, same index out."""
    table = {f"S{i}.": [1.0, i / 10.0, 0.0] for i in range(6)}
    rows = [passage(f"p:{i}:en", " ".join(f"S{j}." for j in range(6))) for i in range(3)]
    a = SemanticChunker(FakeEmbedder(table)).chunk(rows)
    b = SemanticChunker(FakeEmbedder(table)).chunk(rows)
    assert [(c.chunk_id, c.text) for c in a] == [(c.chunk_id, c.text) for c in b]


def test_chunks_carry_the_ids_a_citation_needs() -> None:
    ch = SemanticChunker(FakeEmbedder({"One.": [1, 0, 0]}))
    c = ch.chunk([passage("q:3:hi", "One.", "hi")])[0]
    assert c.passage_id == "q:3:hi"
    assert c.parallel_id == "q:3"
    assert c.language == "hi"
    assert c.chunk_id == "q:3:hi#0"
