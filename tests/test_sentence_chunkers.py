"""C2 sentence-window tests. Job J2.

The trap this pins is the one Architecture.md 3.4 and the C2 stub both call out:
Indic sentence segmentation is not `text.split(".")`. Devanagari ends sentences
with the danda (U+0964), so a period-based splitter returns one giant "sentence"
for every Hindi passage - which produces an index, returns results, and is
silently much worse.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(ROOT))

from rag_core.chunking.base import Chunker  # noqa: E402
from rag_core.chunking.c2_sentence_window import SentenceWindowChunker  # noqa: E402
from rag_core.config import INT8_MODEL, ONNX_DIR, TOKENIZER_FILE  # noqa: E402
from rag_core.retrieval.embedder import Embedder  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (ONNX_DIR / TOKENIZER_FILE).exists(), reason="run scripts/03_export_onnx.py first"
)

EN = "The androgen receptor is a protein. It binds testosterone. It then moves to the nucleus."
HI = "एंड्रोजेन रिसेप्टर एक प्रोटीन है। यह टेस्टोस्टेरोन से जुड़ता है। फिर यह नाभिक में जाता है।"


@pytest.fixture(scope="module")
def embedder() -> Embedder:
    return Embedder(ONNX_DIR / INT8_MODEL, ONNX_DIR / TOKENIZER_FILE, threads=2)


@pytest.fixture(scope="module")
def chunker(embedder: Embedder) -> SentenceWindowChunker:
    return SentenceWindowChunker(embedder)


def passage(text: str, pid: str = "1:0:en", lang: str = "en") -> dict:
    return {"passage_id": pid, "text": text, "language": lang,
            "parallel_id": pid.rsplit(":", 1)[0]}


def test_satisfies_chunker_protocol(chunker: SentenceWindowChunker) -> None:
    assert isinstance(chunker, Chunker)


def test_english_splits_on_sentence_punctuation(chunker: SentenceWindowChunker) -> None:
    assert len(chunker.chunk_one(passage(EN))) == 3


def test_devanagari_splits_on_danda_not_period(chunker: SentenceWindowChunker) -> None:
    """The whole reason indic-nlp is a dependency. A period-based splitter yields
    one chunk here instead of three."""
    chunks = chunker.chunk_one(passage(HI, pid="1:0:hi", lang="hi"))
    assert len(chunks) == 3


def test_danda_is_not_left_in_the_chunk_text(chunker: SentenceWindowChunker) -> None:
    for c in chunker.chunk_one(passage(HI, pid="1:0:hi", lang="hi")):
        assert not c.text.strip().endswith("।")


def test_each_chunk_records_its_sentence_ordinal(chunker: SentenceWindowChunker) -> None:
    """Retrieval expands a hit to its n=2 neighbours, which needs position."""
    chunks = chunker.chunk_one(passage(EN))
    assert [c.ordinal for c in chunks] == [0, 1, 2]


def test_chunk_text_is_a_substring_of_the_passage(chunker: SentenceWindowChunker) -> None:
    """The extractive path returns this verbatim, so no reconstruction artifacts."""
    for c in chunker.chunk_one(passage(EN)):
        assert c.text in EN


def test_single_sentence_passage_yields_one_chunk(chunker: SentenceWindowChunker) -> None:
    assert len(chunker.chunk_one(passage("Just one sentence here."))) == 1


def test_empty_passage_yields_nothing(chunker: SentenceWindowChunker) -> None:
    assert chunker.chunk_one(passage("   ")) == []


def test_metadata_carried_to_every_chunk(chunker: SentenceWindowChunker) -> None:
    for c in chunker.chunk_one(passage(HI, pid="42:3:hi", lang="hi")):
        assert c.passage_id == "42:3:hi"
        assert c.parallel_id == "42:3"
        assert c.language == "hi"


def test_emits_more_chunks_than_c1_on_multi_sentence_text(embedder: Embedder) -> None:
    """C2's premise: a sentence is a smaller retrieval unit than a 96-token
    window. If it were not, C2 would be C1 with extra steps."""
    from rag_core.chunking.c1_fixed import FixedChunker

    p = passage(EN)
    assert len(SentenceWindowChunker(embedder).chunk_one(p)) > len(
        FixedChunker(embedder).chunk_one(p)
    )


def test_params_records_window(chunker: SentenceWindowChunker) -> None:
    p = chunker.params()
    assert p["strategy"] == "c2"
    assert p["window_neighbours"] == 2
