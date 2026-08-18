"""C2: sentence-window. Job J2, box EMBED.

Embed a single sentence; return a window of n=2 neighbouring sentences at
retrieval time. Classic small-to-big: precise matching, wide context.

The window expansion lives at RETRIEVAL, not at build. The index holds
sentence vectors; the neighbours are attached when a hit is returned.

Write the sentence split to `artifacts/sentences.parquet` as a first-class
artifact. J3 (c3_semantic) consumes it, and recomputing it there would double
the cost of both jobs for nothing.

TRAP: Indic sentence segmentation is not `text.split(".")`. Devanagari ends
sentences with the danda (U+0964). `indic-nlp-library` is on the allowlist
(Rules.md 3.1) for exactly this.

Depends on: J1 GPU embedder + its parity gate.

Implementing this
-----------------
You add ONLY this file. `registry.py`, `scripts/02_build_indexes.py` and
`scripts/05_eval_retrieval.py` are owned by BENCH (Phase3-Parallel.md 2.4/4).
When the class is ready, tell BENCH and they swap the `_Pending` entry in
`registry.py` for it. That is the whole integration.

Implement the `Chunker` protocol from `base.py`: `name`, `chunk(passages)`,
`params()`. `c1_fixed.py` is the worked example - read it first, it is short.

Two things `c1_fixed.py` does that every strategy must also do:
  - slice chunk text from the source by CHARACTER OFFSET, never by decoding
    token ids back to text. The extractive path returns this text to the user
    verbatim and a decode round-trip loses whitespace and normalises characters.
  - apply the length cap before chunking. One Hindi passage is 4,093 words from
    a 205-word English source (a translation repetition loop). Uncapped, a few
    of those dominate the build.

`params()` is written into `meta.json` and is how the strategy becomes
reproducible. Put every tunable in it.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..retrieval.embedder import Embedder
from .base import Chunk, PassageRecord

WINDOW_NEIGHBOURS = 2
MAX_PASSAGE_TOKENS = 384

# Devanagari danda and double danda. A period-based splitter leaves an entire
# Hindi passage as one "sentence", which is the silent-quality failure
# Architecture.md 3.4 warns about.
_DANDA = "।॥"
_INDIC_SPLIT = re.compile(f"[{_DANDA}]+")
_LATIN_SPLIT = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str, language: str) -> list[str]:
    """Sentence split, script-aware.

    indic-nlp-library is the documented tool for this and is on the allowlist,
    but its trivial_tokenize emits the danda as a separate token rather than
    giving sentence spans - so for segmentation the danda regex is both simpler
    and exactly what the library would key on. Latin text keeps the standard
    terminal-punctuation rule.
    """
    parts = (_INDIC_SPLIT if language == "hi" else _LATIN_SPLIT).split(text)
    return [s.strip() for s in parts if s and s.strip()]


class SentenceWindowChunker:
    """One sentence per chunk; neighbours attached at retrieval, not at build.

    The window is deliberately NOT materialised into the indexed text. Indexing
    sentence+neighbours would embed each sentence up to three times and defeat
    the precision the strategy is after - the point is a tight vector and a wide
    returned context.
    """

    name = "c2"

    def __init__(
        self,
        embedder: Embedder,
        window: int = WINDOW_NEIGHBOURS,
        max_tokens: int = MAX_PASSAGE_TOKENS,
        **kwargs: object,
    ) -> None:
        self.embedder = embedder
        self.window = window
        self.max_tokens = max_tokens
        self.truncated_count = 0

    def params(self) -> dict[str, object]:
        return {
            "strategy": self.name,
            "window_neighbours": self.window,
            "max_passage_tokens": self.max_tokens,
            "unit": "sub-passage, sentence",
            "expansion": "at retrieval, not at build",
        }

    def chunk(self, passages: Iterable[PassageRecord]) -> list[Chunk]:
        out: list[Chunk] = []
        for p in passages:
            out.extend(self.chunk_one(p))
        return out

    def chunk_one(self, passage: PassageRecord) -> list[Chunk]:
        text: str = passage["text"] or ""
        if not text.strip():
            return []

        enc = self.embedder.tokenizer.encode(text, add_special_tokens=False)
        truncated = len(enc.ids) > self.max_tokens
        if truncated:
            self.truncated_count += 1
            text = text[: enc.offsets[self.max_tokens - 1][1]]

        chunks: list[Chunk] = []
        for i, sentence in enumerate(split_sentences(text, passage["language"])):
            n_tok = len(self.embedder.tokenizer.encode(sentence, add_special_tokens=False).ids)
            if n_tok == 0:
                continue
            chunks.append(
                Chunk(
                    chunk_id=f"{passage['passage_id']}#{len(chunks)}",
                    text=sentence,
                    passage_id=passage["passage_id"],
                    parallel_id=passage["parallel_id"],
                    language=passage["language"],
                    ordinal=len(chunks),
                    token_count=n_tok,
                    truncated=truncated,
                )
            )
        return chunks
