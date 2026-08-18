"""C3: semantic breakpoint. Job J3, box EMBED.

Split where the cosine distance between consecutive sentences exceeds the 92nd
percentile. Chunks follow meaning rather than character count.

REUSES J2's sentence embeddings from `artifacts/sentences.parquet`. That reuse
is the entire reason C2 and C3 are assigned to the same box - recomputing them
doubles the cost of both jobs and buys nothing.

TRAP: compute the 92nd percentile ONCE over the whole corpus and record it in
`params()`, not per passage. Passages here are p50 48 words, so a per-passage
percentile over two or three sentence gaps is noise, not a threshold.

Depends on: J2.

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


class SemanticChunker:
    """Not implemented. See the module docstring for the job and the traps."""

    name = "c3"

    def __init__(self, embedder: Embedder, **kwargs: object) -> None:
        raise NotImplementedError(
            "c3 is job J3 on EMBED. See the docstring in this file and "
            "Phase3-Parallel.md section 2."
        )

    def params(self) -> dict[str, object]:
        raise NotImplementedError

    def chunk(self, passages: Iterable[PassageRecord]) -> list[Chunk]:
        raise NotImplementedError
