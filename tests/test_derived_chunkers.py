"""C5, C6 and C7 chunker tests. Jobs J13, J14 and J4.

All three derive from C1, which creates two failure modes worth pinning:

  - **Span drift.** Their builder reuses C1's vectors, which is only valid while
    their chunk spans are C1's chunk spans. Drift would bind every vector to the
    wrong text and still produce an index that loads and retrieves.
  - **Leakage.** C7 indexes query text against gold passages. The benchmark's
    250 queries are the `test` split, so indexing test queries puts the answer
    key into the index. Measured, that mistake is worth +0.47 Hit@1 in English
    and +0.54 in Hindi - it does not look like a bug, it looks like success.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from rag_core.chunking.base import Chunker  # noqa: E402
from rag_core.chunking.c1_fixed import FixedChunker  # noqa: E402
from rag_core.chunking.c5_metadata import FILTER_KEYS, MetadataChunker  # noqa: E402
from rag_core.chunking.c6_hierarchical import HierarchicalChunker  # noqa: E402
from rag_core.chunking.c7_doc2query import (  # noqa: E402
    ALL_SPLITS,
    SAFE_SPLITS,
    Doc2QueryChunker,
)
from rag_core.config import INT8_MODEL, ONNX_DIR, TOKENIZER_FILE  # noqa: E402
from rag_core.retrieval.embedder import Embedder  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (ONNX_DIR / TOKENIZER_FILE).exists(),
    reason="run scripts/03_export_onnx.py first",
)


@pytest.fixture(scope="module")
def embedder() -> Embedder:
    return Embedder(ONNX_DIR / INT8_MODEL, ONNX_DIR / TOKENIZER_FILE, threads=2)


def passages() -> list[dict]:
    """Two passages in one query group, so C6 has a parent to build."""
    return [
        {
            "passage_id": f"7:{i}:en",
            "text": f"Sentence number {i} about the androgen receptor protein. "
                    * 3,
            "language": "en",
            "script": "Latn",
            "query_id": "7",
            "position": str(i),
            "parallel_id": f"7:{i}",
            "is_selected_any": "True" if i == 0 else "False",
        }
        for i in range(2)
    ]


# -- the shared invariant: spans identical to C1 -----------------------------


@pytest.mark.parametrize("factory", [MetadataChunker, HierarchicalChunker])
def test_derived_chunks_match_c1_exactly(factory, embedder: Embedder) -> None:
    """The precondition for reusing C1's vectors. If this fails, 02c's gate
    fails too - but this says so in a second rather than after a full build."""
    base = FixedChunker(embedder).chunk(passages())
    derived = factory(embedder).chunk(passages())
    assert [c.chunk_id for c in derived] == [c.chunk_id for c in base]
    assert [c.text for c in derived] == [c.text for c in base]


@pytest.mark.parametrize(
    "factory", [MetadataChunker, HierarchicalChunker, Doc2QueryChunker]
)
def test_satisfies_protocol(factory, embedder: Embedder) -> None:
    assert isinstance(factory(embedder), Chunker)


@pytest.mark.parametrize(
    "factory", [MetadataChunker, HierarchicalChunker, Doc2QueryChunker]
)
def test_params_record_the_c1_derivation(factory, embedder: Embedder) -> None:
    assert factory(embedder).params()["reuses"] == "c1"


# -- C5 ----------------------------------------------------------------------


def test_c5_writes_every_declared_filter_key(embedder: Embedder) -> None:
    for chunk in MetadataChunker(embedder).chunk(passages()):
        assert set(chunk.meta) == set(FILTER_KEYS)


def test_c5_filter_values_come_from_the_passage(embedder: Embedder) -> None:
    first = MetadataChunker(embedder).chunk(passages())[0]
    assert first.meta["script"] == "Latn"
    assert first.meta["is_selected_any"] == "True"
    assert first.meta["position"] == "0"


# -- C6 ----------------------------------------------------------------------


def test_c6_groups_passages_into_one_parent(embedder: Embedder) -> None:
    chunker = HierarchicalChunker(embedder)
    chunker.chunk(passages())
    assert chunker.parents == {"7:en": ["7:0:en", "7:1:en"]}


def test_c6_every_chunk_points_at_its_parent(embedder: Embedder) -> None:
    chunker = HierarchicalChunker(embedder)
    for chunk in chunker.chunk(passages()):
        assert chunk.meta["parent_id"] == "7:en"


def test_c6_parents_do_not_mix_languages(embedder: Embedder) -> None:
    """The en and hi passages for a query are translations of each other, not
    extra context. A mixed parent would hand a Hindi answer stage English text."""
    english = passages()
    mixed = list(english)
    for row in english:  # iterate the original, not the list being appended to
        hi = dict(row)
        hi.update(passage_id=row["passage_id"].replace(":en", ":hi"),
                  language="hi", script="Deva")
        mixed.append(hi)
    chunker = HierarchicalChunker(embedder)
    chunker.chunk(mixed)
    assert set(chunker.parents) == {"7:en", "7:hi"}
    for parent_id, members in chunker.parents.items():
        language = parent_id.split(":")[1]
        assert all(pid.endswith(f":{language}") for pid in members)


# -- C7: the leakage guard ---------------------------------------------------


def test_c7_defaults_to_corpus_only(embedder: Embedder) -> None:
    """The default must never include an evaluation partition."""
    assert Doc2QueryChunker(embedder).indexable_splits == SAFE_SPLITS
    assert "test" not in SAFE_SPLITS
    assert "dev" not in SAFE_SPLITS


def test_c7_default_is_not_flagged_leaky(embedder: Embedder) -> None:
    assert Doc2QueryChunker(embedder).leaky is False


@pytest.mark.parametrize("split", ["test", "dev"])
def test_c7_indexing_an_evaluation_split_is_flagged_leaky(
    split: str, embedder: Embedder
) -> None:
    """Opting in is allowed - it is how the leak was measured - but it must
    announce itself, because meta.json is what a reader trusts later."""
    chunker = Doc2QueryChunker(embedder, indexable_splits=frozenset({split}))
    assert chunker.leaky is True
    assert chunker.params()["leaky"] is True


def test_c7_all_splits_is_leaky(embedder: Embedder) -> None:
    assert Doc2QueryChunker(embedder, indexable_splits=ALL_SPLITS).leaky is True


def test_c7_keeps_c1_chunks_first_then_appends(embedder: Embedder) -> None:
    """02c reuses C1's vectors for the leading rows and embeds only the tail, so
    the C1 chunks must come first and stay contiguous."""
    base = FixedChunker(embedder).chunk(passages())
    derived = Doc2QueryChunker(embedder).chunk(passages())
    assert [c.chunk_id for c in derived[: len(base)]] == [c.chunk_id for c in base]


def test_c7_query_rows_are_marked_as_query_derived(embedder: Embedder) -> None:
    base = len(FixedChunker(embedder).chunk(passages()))
    for chunk in Doc2QueryChunker(embedder).chunk(passages())[base:]:
        assert chunk.meta["source"] == "query"
        assert chunk.meta["split"] in SAFE_SPLITS
