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


class LateChunker:
    """Not implemented. See the module docstring for the job and the traps."""

    name = "c8"

    def __init__(self, embedder: Embedder, **kwargs: object) -> None:
        raise NotImplementedError(
            "c8 is job J8 on LLM. See the docstring in this file and "
            "Phase3-Parallel.md section 2."
        )

    def params(self) -> dict[str, object]:
        raise NotImplementedError

    def chunk(self, passages: Iterable[PassageRecord]) -> list[Chunk]:
        raise NotImplementedError
