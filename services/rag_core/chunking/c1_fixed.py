"""C1: fixed-size token windows with overlap. The baseline.

96 tokens, 24 overlap - NOT the 256/40 in the original plan. Decision D8: no
English passage in the frozen slice exceeds 205 words, so a 256-token window
emits exactly one chunk per passage and the strategy does nothing at all. At 96
tokens it actually splits the longer half of the corpus, which is the only way
it functions as a baseline worth comparing against.

Windows are measured in real tokenizer tokens, not whitespace words, because the
embedder's context limit and the other strategies are both token-denominated and
a word-based approximation drifts badly on Devanagari.

Chunk text is sliced from the ORIGINAL passage using token character offsets
rather than decoded from token ids. Decoding round-trips lose whitespace and
normalise characters, and the extractive answer path returns this text verbatim
to the user - it has to be the real thing.
"""

from __future__ import annotations

from typing import Iterable

from ..retrieval.embedder import Embedder
from .base import Chunk, PassageRecord

WINDOW_TOKENS = 96
OVERLAP_TOKENS = 24

# Cap before chunking. One Hindi passage in the slice runs to 4,093 words against
# a 205-word English source - a translation-model repetition loop. Uncapped, a
# handful of those produce ~55 chunks each and dominate index build time while
# adding nothing. See Memory.md, Phase 1 surprises.
MAX_PASSAGE_TOKENS = 384


class FixedChunker:
    name = "c1"

    def __init__(
        self,
        embedder: Embedder,
        window: int = WINDOW_TOKENS,
        overlap: int = OVERLAP_TOKENS,
        max_tokens: int = MAX_PASSAGE_TOKENS,
    ) -> None:
        if overlap >= window:
            raise ValueError(f"overlap {overlap} must be less than window {window}")
        self.embedder = embedder
        self.window = window
        self.overlap = overlap
        self.max_tokens = max_tokens
        self.stride = window - overlap
        self.truncated_count = 0

    def params(self) -> dict[str, object]:
        return {
            "strategy": self.name,
            "window_tokens": self.window,
            "overlap_tokens": self.overlap,
            "max_passage_tokens": self.max_tokens,
            "unit": "sub-passage",
        }

    def chunk(self, passages: Iterable[PassageRecord]) -> list[Chunk]:
        out: list[Chunk] = []
        for p in passages:
            out.extend(self.chunk_one(p))
        return out

    def chunk_one(self, passage: PassageRecord) -> list[Chunk]:
        text: str = passage["text"]
        enc = self.embedder.tokenizer.encode(text, add_special_tokens=False)
        offsets = enc.offsets
        n = len(offsets)

        truncated = n > self.max_tokens
        if truncated:
            self.truncated_count += 1
            offsets = offsets[: self.max_tokens]
            n = self.max_tokens

        if n == 0:
            return []

        chunks: list[Chunk] = []
        ordinal = 0
        start = 0
        while start < n:
            end = min(start + self.window, n)
            char_start = offsets[start][0]
            char_end = offsets[end - 1][1]
            piece = text[char_start:char_end].strip()
            if piece:
                chunks.append(
                    Chunk(
                        chunk_id=f"{passage['passage_id']}#{ordinal}",
                        text=piece,
                        passage_id=passage["passage_id"],
                        parallel_id=passage["parallel_id"],
                        language=passage["language"],
                        ordinal=ordinal,
                        token_count=end - start,
                        truncated=truncated,
                    )
                )
                ordinal += 1
            if end >= n:
                break
            start += self.stride
        return chunks
