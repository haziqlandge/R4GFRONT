"""BM25 lexical index tests. Job J11.

Written before the implementation. The behaviours pinned here are the ones that
actually matter for this corpus:

  - Devanagari must be tokenized with indic-nlp, not whitespace. Architecture.md
    3.4 says whitespace tokenization destroys BM25 quality on Devanagari, and
    that failure is silent - you get an index, it returns results, they are just
    worse.
  - search() must return the same (row, score) shape as DenseIndex.search(), or
    J12's fusion cannot consume both uniformly.
  - Rare exact terms must outrank common ones. That is the entire reason BM25 is
    in this system alongside dense retrieval (Architecture.md 3.4): dense is weak
    on rare entities, numbers and proper nouns, which MS MARCO queries are full of.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from rag_core.retrieval.lexical import BM25Index, detect_language, tokenize  # noqa: E402


# Small hand-built corpus. Row indices are what search() returns, so they are the
# thing under test - keep them obvious.
CHUNKS: list[dict] = [
    {"text": "The androgen receptor is a nuclear receptor protein.", "language": "en"},
    {"text": "Photosynthesis converts light energy into chemical energy.", "language": "en"},
    {"text": "The mitochondrion is the powerhouse of the cell.", "language": "en"},
    {"text": "एंड्रोजेन रिसेप्टर एक नाभिकीय रिसेप्टर प्रोटीन है।", "language": "hi"},
    {"text": "प्रकाश संश्लेषण प्रकाश ऊर्जा को रासायनिक ऊर्जा में बदलता है।", "language": "hi"},
]


# -- tokenization -----------------------------------------------------------


def test_devanagari_is_not_whitespace_split() -> None:
    """The failure Architecture.md 3.4 warns about, pinned. Whitespace splitting
    leaves the danda attached to the preceding word, so 'परिभाषा।' and 'परिभाषा'
    become different terms and never match."""
    text = "एंड्रोजेन रिसेप्टर की परिभाषा।"
    tokens = tokenize(text, "hi")
    assert "परिभाषा" in tokens
    assert "परिभाषा।" not in tokens


def test_danda_is_dropped_not_kept_as_a_term() -> None:
    """indic_tokenize emits the danda as its own token. Left in, it appears in
    almost every Hindi passage and becomes a useless high-frequency term."""
    assert "।" not in tokenize("प्रकाश ऊर्जा में बदलता है।", "hi")


def test_latin_is_lowercased_and_stripped_of_punctuation() -> None:
    tokens = tokenize("The Androgen Receptor, protein.", "en")
    assert "androgen" in tokens
    assert "receptor" in tokens
    assert "receptor," not in tokens
    assert "The" not in tokens


def test_language_detected_from_script_when_not_declared() -> None:
    """Queries arrive without a language tag. Script is the only signal available
    in-process, and it is sufficient for en vs hi."""
    assert detect_language("एंड्रोजेन रिसेप्टर") == "hi"
    assert detect_language("androgen receptor") == "en"


def test_code_mixed_text_detected_as_indic() -> None:
    """Hinglish is how people actually type. Any Devanagari present means the
    Indic tokenizer is the safer choice - it handles Latin runs fine, the
    reverse is not true."""
    assert detect_language("androgen रिसेप्टर kya hai") == "hi"


# -- search contract --------------------------------------------------------


@pytest.fixture()
def index() -> BM25Index:
    idx = BM25Index()
    idx.build(CHUNKS)
    return idx


def test_search_returns_row_score_pairs_like_dense_index(index: BM25Index) -> None:
    """J12's fusion consumes dense and lexical results with the same code path.
    If these shapes diverge, fusion silently ranks on mismatched types."""
    hits = index.search("androgen receptor", k=3)
    assert hits, "expected at least one hit"
    for row, score in hits:
        assert isinstance(row, int)
        assert isinstance(score, float)
        assert 0 <= row < len(CHUNKS)


def test_exact_term_match_ranks_first(index: BM25Index) -> None:
    hits = index.search("androgen receptor", k=3)
    assert hits[0][0] == 0


def test_rare_term_beats_common_term(index: BM25Index) -> None:
    """The reason BM25 earns its place next to dense retrieval: 'mitochondrion'
    is rare and decisive, 'the' is everywhere and worthless."""
    hits = index.search("mitochondrion", k=3)
    assert hits[0][0] == 2


def test_hindi_query_retrieves_hindi_passage(index: BM25Index) -> None:
    hits = index.search("एंड्रोजेन रिसेप्टर", k=3)
    assert hits[0][0] == 3


def test_search_respects_k(index: BM25Index) -> None:
    assert len(index.search("energy", k=2)) <= 2


def test_search_never_returns_more_rows_than_corpus(index: BM25Index) -> None:
    assert len(index.search("receptor", k=999)) <= len(CHUNKS)


def test_empty_query_returns_no_hits(index: BM25Index) -> None:
    assert index.search("", k=5) == []


def test_query_of_only_punctuation_returns_no_hits(index: BM25Index) -> None:
    """Tokenizes to nothing. Must return empty rather than raising - the input
    guard rejects junk upstream, but this must not be the thing that crashes."""
    assert index.search("।।। ...", k=5) == []


def test_scores_are_descending(index: BM25Index) -> None:
    scores = [s for _, s in index.search("receptor protein", k=5)]
    assert scores == sorted(scores, reverse=True)


# -- persistence ------------------------------------------------------------


def test_save_load_roundtrip_preserves_ranking(tmp_path: Path) -> None:
    built = BM25Index()
    built.build(CHUNKS)
    expected = built.search("androgen receptor", k=3)

    built.save(tmp_path)
    loaded = BM25Index()
    loaded.load(tmp_path)

    assert [r for r, _ in loaded.search("androgen receptor", k=3)] == [
        r for r, _ in expected
    ]


def test_load_from_missing_directory_raises_index_not_ready(tmp_path: Path) -> None:
    from rag_core.harness.errors import IndexNotReady

    with pytest.raises(IndexNotReady):
        BM25Index().load(tmp_path / "does-not-exist")


def test_search_before_build_raises_index_not_ready() -> None:
    from rag_core.harness.errors import IndexNotReady

    with pytest.raises(IndexNotReady):
        BM25Index().search("anything", k=5)
