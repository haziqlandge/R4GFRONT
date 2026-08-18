"""C8: late chunking. Job J8, box LLM.

Encode the FULL passage once, keeping the token-level `last_hidden_state`, then
mean-pool per chunk span. Each chunk vector therefore carries whole-passage
context, which is the context-loss problem naive chunking creates.

USE C1's SPANS. Then C8 versus C1 is a clean single-variable comparison: same
spans, different context. Different spans would confound the two effects and
the comparison would answer nothing.

This needs the encoder's PRE-POOLING output. The ONNX graph already emits it -
the existing `Embedder` does the masked mean itself in Python
(`Embedder._mean_pool`), so the tensor you need is what `session.run()` returns
before that call.

Apply the 99.5th-percentile length cap from Architecture.md 4.1, which is
specified and still not implemented anywhere.

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

from typing import Iterable

from ..retrieval.embedder import Embedder
from .base import Chunk, PassageRecord
from .c1_fixed import MAX_PASSAGE_TOKENS, OVERLAP_TOKENS, WINDOW_TOKENS


class LateChunker:
    """C1's spans, recorded with their token offsets so the builder can pool them
    out of a whole-passage encode.

    Deliberately delegates the span maths to the same window/stride arithmetic C1
    uses rather than reimplementing it. If the two drifted apart, C8 vs C1 would
    silently stop being a single-variable comparison - which is the one property
    this strategy exists to have.

    This class does NOT embed. The late-chunking pass lives in
    scripts/02d_build_late.py, because it needs the encoder's pre-pooling output
    and the standard build path embeds each chunk's text independently - which is
    precisely the context loss C8 is testing against.
    """

    name = "c8"

    def __init__(
        self,
        embedder: Embedder,
        window: int = WINDOW_TOKENS,
        overlap: int = OVERLAP_TOKENS,
        max_tokens: int = MAX_PASSAGE_TOKENS,
        **kwargs: object,
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
            "unit": "sub-passage, whole-passage context",
            "pooling": "mean over span of full-passage last_hidden_state",
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
                        # Token positions within the PASSAGE, not the chunk. The
                        # builder pools last_hidden_state[tok_start:tok_end].
                        meta={"tok_start": str(start), "tok_end": str(end)},
                    )
                )
                ordinal += 1
            if end >= n:
                break
            start += self.stride
        return chunks
