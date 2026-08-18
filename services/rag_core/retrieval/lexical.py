"""bm25s wrapper, Indic-aware tokenization. Job J11.

Why this exists next to `dense.py`: dense retrieval is weak on rare entities,
numbers and proper nouns, which MS MARCO queries are full of (Architecture.md
3.4). It is also the most plausible lever on ISSUES.md I5, the 0.19 Recall@10
gap between English and Hindi.

Two invariants carry the whole module.

**Row alignment.** `search()` returns positions in the chunk list passed to
`build()`, exactly like `DenseIndex.search()` returns positions in its
`chunks.parquet`. J12's fusion combines the two by row, so the lexical index
MUST be built over the same chunk list, in the same order, as the dense index
it is fused with. Build them from the same `chunks.parquet` read and this is
free; build them separately and fusion silently ranks mismatched documents.
`n_chunks` is exposed so the caller can assert the alignment rather than
assume it.

**Tokenization is language-aware or it is worthless.** Whitespace splitting
leaves the danda attached to the preceding word, so `परिभाषा।` and `परिभाषा`
become different terms and never match. That failure is silent - you get an
index, it returns results, they are merely worse - which is why it is pinned by
tests rather than left to review.
"""

from __future__ import annotations

import re
from pathlib import Path

import bm25s
from indicnlp.tokenize import indic_tokenize

from ..chunking.base import ChunkRecord
from ..config import BM25_B, BM25_K1, BM25_METHOD
from ..harness.errors import IndexNotReady

# Devanagari block. Any occurrence routes the whole string to the Indic
# tokenizer - see detect_language().
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# A token is kept only if it carries at least one word character. This is what
# drops the danda: indic_tokenize emits `।` as its own token, and left in it
# appears in almost every Hindi passage, becoming a high-frequency term with no
# discriminative power. `\w` is Unicode-aware here, so Devanagari letters match
# and punctuation does not.
_HAS_WORD_CHAR = re.compile(r"\w", re.UNICODE)

# Latin path: split on word characters directly, which strips the trailing
# comma from `Receptor,` without a separate punctuation pass.
_WORD = re.compile(r"\w+", re.UNICODE)


def detect_language(text: str) -> str:
    """Return 'hi' or 'en' from script alone.

    Queries arrive without a language tag and script is the only signal
    available in-process. Any Devanagari at all means Indic, including
    code-mixed Hinglish: the Indic tokenizer handles embedded Latin runs
    correctly, and the reverse is not true, so this asymmetry is the safe way
    to be wrong.
    """
    return "hi" if _DEVANAGARI.search(text) else "en"


def tokenize(text: str, language: str | None = None) -> list[str]:
    """Lowercased terms for indexing or querying. `language` defaults to detection.

    May legitimately return an empty list - an empty query, or one that is all
    punctuation, has no terms. Callers must treat that as "no hits" rather than
    an error.
    """
    if not text:
        return []
    if language is None:
        language = detect_language(text)

    if language == "en":
        return _WORD.findall(text.lower())

    # trivial_tokenize is the Indic-aware one: it separates the danda and other
    # Devanagari punctuation into standalone tokens, which the filter below then
    # discards. .lower() is a no-op on Devanagari and normalises Latin runs in
    # code-mixed text.
    return [
        token.lower()
        for token in indic_tokenize.trivial_tokenize(text, language)
        if _HAS_WORD_CHAR.search(token)
    ]


class BM25Index:
    """BM25 over the chunk list, one index spanning both languages.

    Deliberately not one index per language. A Hindi query tokenizes to Hindi
    terms which simply do not occur in English chunks, so BM25's own IDF does
    the separation; splitting the index would add a routing decision that can be
    wrong for no gain, and would break row alignment with the single dense index.
    """

    def __init__(self, k1: float = BM25_K1, b: float = BM25_B) -> None:
        self.k1 = k1
        self.b = b
        self._model: bm25s.BM25 | None = None
        self._n_chunks = 0

    @property
    def ready(self) -> bool:
        return self._model is not None

    @property
    def n_chunks(self) -> int:
        """Corpus size. Assert this against the dense index before fusing."""
        return self._n_chunks

    def build(self, chunks: list[ChunkRecord]) -> None:
        """Index `chunks` in order. Row i of results is chunks[i].

        Each chunk's declared `language` is used rather than detected, because
        the corpus knows its own language and detection is only for queries.
        """
        corpus_tokens = [
            tokenize(chunk["text"], chunk.get("language")) for chunk in chunks
        ]
        model = bm25s.BM25(k1=self.k1, b=self.b, method=BM25_METHOD)
        model.index(corpus_tokens, show_progress=False)
        self._model = model
        self._n_chunks = len(chunks)

    def search(self, query: str, k: int) -> list[tuple[int, float]]:
        """Returns (row, score) descending, the same shape as DenseIndex.search().

        Scores are raw BM25 and are NOT comparable to the dense index's cosine
        similarities. That incomparability is precisely why fusion is rank-based
        RRF rather than score normalisation (Architecture.md 3.5).
        """
        if self._model is None:
            raise IndexNotReady("lexical index not built. Call build() or load().")

        tokens = tokenize(query)
        if not tokens:
            return []

        # bm25s raises ValueError when k exceeds the corpus size rather than
        # returning what it has, so clamp. DenseIndex.search does the same.
        k = min(k, self._n_chunks)
        if k <= 0:
            return []

        # n_threads is left at 0 (single-threaded). The hot path is one query
        # against a scipy sparse matmul; a thread pool costs more to spin up
        # than the search takes. Same reasoning as DenseIndex.set_num_threads(1).
        documents, scores = self._model.retrieve(
            [tokens], k=k, show_progress=False, n_threads=0
        )

        # Drop zero scores. bm25s pads the result up to k with documents that
        # share no term with the query; passed into RRF those occupy real ranks
        # and inject noise into the fused list.
        return [
            (int(row), float(score))
            for row, score in zip(documents[0], scores[0])
            if score > 0.0
        ]

    def save(self, path: Path) -> None:
        if self._model is None:
            raise IndexNotReady("nothing to save. Call build() first.")
        path.mkdir(parents=True, exist_ok=True)
        self._model.save(str(path), show_progress=False)

    def load(self, path: Path) -> None:
        # bm25s stores the corpus size in params.index.json; that file is the
        # honest existence check, since an empty directory would otherwise fail
        # deeper in with a bare FileNotFoundError.
        if not (path / "params.index.json").exists():
            raise IndexNotReady(
                f"{path} holds no BM25 index. Run scripts/02_build_indexes.py."
            )
        model = bm25s.BM25.load(str(path), show_progress=False)
        self._model = model
        self._n_chunks = int(model.scores["num_docs"])
