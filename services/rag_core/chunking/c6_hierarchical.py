"""C6: hierarchical parent-child. Job J14, box BENCH.

Children are embedded and searched; the parent is returned as generation
context. Retrieval precision with generation context.

NEEDS NO NEW EMBEDDINGS. The children ARE the C1 chunks already on disk, and
the parent layer is a lookup table keyed on `query_id`, not a second index.
That is why it sits on the box with no GPU.

The parent must sit ABOVE the passage, not below it. Passages here are p50 48
words (decision D8), so a parent-below-passage tree would be splitting things
that are already short. The natural parent is the `query_id` group - roughly ten
passages that share a source query.

Depends on: the C1 index existing. Nothing else.

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


class HierarchicalChunker:
    """Not implemented. See the module docstring for the job and the traps."""

    name = "c6"

    def __init__(self, embedder: Embedder, **kwargs: object) -> None:
        raise NotImplementedError(
            "c6 is job J14 on BENCH. See the docstring in this file and "
            "Phase3-Parallel.md section 2."
        )

    def params(self) -> dict[str, object]:
        raise NotImplementedError

    def chunk(self, passages: Iterable[PassageRecord]) -> list[Chunk]:
        raise NotImplementedError
