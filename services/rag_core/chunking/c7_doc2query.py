"""C7: doc2query / query-aligned. Job J4, box EMBED.

MSMARCO-XI ships query-passage pairs. Index the PAIRED QUERY text as an
additional vector pointing at its passage, on top of the C1 chunk vectors.
Roughly 30,000 extra vectors on a 379,242 base - nearly free.

Architecture.md 4 flags this as the highest-leverage strategy for this corpus
because it directly closes the vocabulary gap between how people ask and how
passages are written. Assumption A5 predicts it wins.

VERIFY RATHER THAN ASSUME. A prediction that turns out right is only worth
something if it could have turned out wrong.

TRAP, and it is easy to get backwards: the query vector must carry the
`passage: ` prefix, NOT `query: `. It is being indexed as a REPRESENTATION OF A
PASSAGE, not used as a search query. Getting this wrong costs recall and raises
nothing - the same silent-failure class as the pooling and prefix mistakes
called out in `retrieval/embedder.py`.

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


class Doc2QueryChunker:
    """Not implemented. See the module docstring for the job and the traps."""

    name = "c7"

    def __init__(self, embedder: Embedder, **kwargs: object) -> None:
        raise NotImplementedError(
            "c7 is job J4 on EMBED. See the docstring in this file and "
            "Phase3-Parallel.md section 2."
        )

    def params(self) -> dict[str, object]:
        raise NotImplementedError

    def chunk(self, passages: Iterable[PassageRecord]) -> list[Chunk]:
        raise NotImplementedError
