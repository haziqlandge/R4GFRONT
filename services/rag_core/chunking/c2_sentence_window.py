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

from typing import Iterable

from ..retrieval.embedder import Embedder
from .base import Chunk, PassageRecord


class SentenceWindowChunker:
    """Not implemented. See the module docstring for the job and the traps."""

    name = "c2"

    def __init__(self, embedder: Embedder, **kwargs: object) -> None:
        raise NotImplementedError(
            "c2 is job J2 on EMBED. See the docstring in this file and "
            "Phase3-Parallel.md section 2."
        )

    def params(self) -> dict[str, object]:
        raise NotImplementedError

    def chunk(self, passages: Iterable[PassageRecord]) -> list[Chunk]:
        raise NotImplementedError
