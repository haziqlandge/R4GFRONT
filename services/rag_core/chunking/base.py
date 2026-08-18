"""Chunker protocol. Every strategy implements this and nothing else.

Rules.md 6: one chunker per file, all behind the same protocol. Adding a ninth
strategy must never mean adding an `if` to an existing one.

Architecture.md 4.1 reframes what "chunking" means on this corpus: English
passages top out at 205 words, so nothing here splits long documents. The
strategies differ in what they choose as the retrieval UNIT - sub-passage
(C1, C2, C3, C4, C8), supra-passage (C6), or an alternative representation
of the same text (C5, C7).
"""

from __future__ import annotations

from typing import Any, Iterable, Protocol, runtime_checkable

from pydantic import BaseModel

# Rows as they come out of parquet. Typed aliases rather than bare `dict` so
# mypy --strict passes and so the shape is documented at every call site.
PassageRecord = dict[str, Any]
ChunkRecord = dict[str, Any]


class Chunk(BaseModel):
    """One indexed unit.

    `passage_id` is what a citation resolves to, so a chunk always knows the
    passage it came from even when the chunk is a fragment of it.
    """

    model_config = {"frozen": True}

    chunk_id: str  # f"{passage_id}#{ordinal}"
    text: str  # verbatim slice of the source passage
    passage_id: str
    parallel_id: str  # links the en/hi twins, see Architecture.md 4.2
    language: str
    ordinal: int  # position of this chunk within its passage
    token_count: int
    truncated: bool = False  # source passage was capped before chunking


@runtime_checkable
class Chunker(Protocol):
    """Implemented by c1_fixed.py through c8_late.py."""

    name: str

    def chunk(self, passages: Iterable[PassageRecord]) -> list[Chunk]:
        """Turn Passage records (as dicts from passages.parquet) into Chunks."""
        ...

    def params(self) -> dict[str, object]:
        """Recorded into the index meta.json so a built index is reproducible."""
        ...
