"""C5: metadata-aware. Job J13, box BENCH.

Boundaries and payload filters on `language`, `script`, `query_type`,
`is_selected_any` and passage `position`, written into the payload so search can
be PRE-filtered rather than post-filtered.

NEEDS NO NEW EMBEDDINGS. It reuses the C1 vectors already on disk and changes
only the payload and the filter. That is why it sits on the box with no GPU.

`query_type` is the strongest signal available and it replaced the `url` field
that this strategy was originally specified around - MSMARCO-XI has no `url`,
unlike upstream MS MARCO (reversal R1). The distribution is usefully spread:
51% DESCRIPTION, 24% NUMERIC, 9% ENTITY, 7% PERSON, 7% LOCATION.

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


class MetadataChunker:
    """Not implemented. See the module docstring for the job and the traps."""

    name = "c5"

    def __init__(self, embedder: Embedder, **kwargs: object) -> None:
        raise NotImplementedError(
            "c5 is job J13 on BENCH. See the docstring in this file and "
            "Phase3-Parallel.md section 2."
        )

    def params(self) -> dict[str, object]:
        raise NotImplementedError

    def chunk(self, passages: Iterable[PassageRecord]) -> list[Chunk]:
        raise NotImplementedError
