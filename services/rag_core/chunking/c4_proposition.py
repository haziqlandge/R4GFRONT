"""C4: proposition / atomic fact. Jobs J6 (generate) + J7 (index), box LLM.

An offline LLM pass decomposes each passage into standalone factual assertions.
The highest-precision retrieval unit available: expensive offline, free at
query time.

NEVER GROQ. Decision D11: 295,890 passages at ~80 output tokens each is ~24M
output tokens against a 12,000-token free-tier window. That is not slow, it is
arithmetically impossible. This runs on a local 3B-7B instruct model at 4-bit.
The Groq quota is reserved for the Phase 5 runtime fallback and Band B.

This job runs unattended overnight, so it needs the properties an unattended
job needs:
  - SHARD AND CHECKPOINT. Write `artifacts/propositions/shard_NN.parquet` as
    each completes. A crash at 90% must not cost the run.
  - CAP OUTPUT LENGTH per passage. Feeding a degenerate 4,093-word passage to
    an LLM invites a matching degenerate output (ISSUES.md I1).
  - CONSTRAIN THE FORMAT: one proposition per line, no preamble, no numbering.
    Then validate the parse and COUNT REJECTS. A generation job with no validity
    metric has an unknown error rate.
  - Log the reject count into `params()` so it reaches `meta.json`. If 8% of
    passages produced unparseable output, that belongs in the comparison table,
    not in terminal scrollback.

HONEST RISK (assumption A14): an LLM restating a machine-translated Hindi
passage is a lossy pass over already-lossy text. C4 may produce a WORSE index
than C1 while costing far more. That is a legitimate and interesting finding.
Do not bury it.

Sizing check before J7: expect 2-3x C1's chunk count. Check the projected
serving footprint against Devices.md 6 before assuming C4 is even eligible to
win - a strategy that beats everything on recall and does not fit in 8 GB is a
README finding, not the default.

Depends on: J5 CUDA stack.

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


class PropositionChunker:
    """Not implemented. See the module docstring for the job and the traps."""

    name = "c4"

    def __init__(self, embedder: Embedder, **kwargs: object) -> None:
        raise NotImplementedError(
            "c4 is job J6/J7 on LLM. See the docstring in this file and "
            "Phase3-Parallel.md section 2."
        )

    def params(self) -> dict[str, object]:
        raise NotImplementedError

    def chunk(self, passages: Iterable[PassageRecord]) -> list[Chunk]:
        raise NotImplementedError
