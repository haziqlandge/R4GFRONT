"""C5: metadata-aware. Same vectors as C1, different payload. Job J13.

The retrieval UNIT is identical to C1 - same spans, same text, therefore the same
embeddings. What changes is that every chunk carries filter keys, so search can
be restricted before the vector comparison instead of after it.

That identity is the point, and it is also load-bearing: because C5's chunk list
is C1's chunk list, `02c_build_derived.py` copies C1's index rather than spending
30 minutes re-embedding text that has not changed. The builder verifies the two
chunk lists match exactly before it does that, because if this class ever stops
producing C1's spans, silently reusing C1's vectors would attach every chunk to
the wrong text.

Filter keys, per Phase3-Parallel.md J13:

  language          en / hi           already on Chunk, repeated here for uniform access
  script            Latn / Deva
  query_type        DESCRIPTION, NUMERIC, ENTITY, LOCATION, PERSON, ...
  is_selected_any   the passage is answer-bearing for its query
  position          rank of the passage within its query group

Memory.md R1: `query_type` replaced the `url` field from the original plan, which
does not exist in this corpus. The distribution is usefully spread - 51%
DESCRIPTION, 24% NUMERIC - so it is a filter that actually partitions.

What this strategy can and cannot show. A metadata filter cannot improve
unfiltered retrieval; with no filter applied C5 IS C1 and must score identically.
Its value is conditional recall - "answer-bearing Hindi passages only" - and a
latency win from searching a smaller candidate set. The comparison table should
read C5 as a capability, not as a recall number, and J15 should say so rather
than printing a row that looks like a tie and inviting the reader to conclude the
strategy does nothing.
"""

from __future__ import annotations

from typing import Iterable

import pyarrow.parquet as pq

from ..config import QUERIES_PARQUET
from ..retrieval.embedder import Embedder
from .base import Chunk, PassageRecord
from .c1_fixed import FixedChunker

# The keys written into Chunk.meta. Declared here so the indexer and the eval
# harness agree on the vocabulary rather than each spelling it out.
FILTER_KEYS = ("language", "script", "query_type", "is_selected_any", "position")


class MetadataChunker:
    name = "c5"

    def __init__(self, embedder: Embedder, **kwargs: object) -> None:
        # Composition, not inheritance: C5 is "C1 plus a payload", and delegating
        # keeps the spans provably identical instead of merely intended to be.
        self._c1 = FixedChunker(embedder, **kwargs)  # type: ignore[arg-type]
        self.embedder = embedder
        self._query_type: dict[str, str] = {}

    @property
    def truncated_count(self) -> int:
        return self._c1.truncated_count

    def params(self) -> dict[str, object]:
        return {
            **self._c1.params(),
            "strategy": self.name,
            "unit": "sub-passage",
            "reuses": "c1",
            "filter_keys": list(FILTER_KEYS),
        }

    def _load_query_types(self) -> None:
        """query_type lives on the query, not the passage, so it needs the join."""
        if self._query_type:
            return
        table = pq.read_table(QUERIES_PARQUET, columns=["query_id", "query_type"])
        self._query_type = {
            str(row["query_id"]): str(row["query_type"]) for row in table.to_pylist()
        }

    def chunk(self, passages: Iterable[PassageRecord]) -> list[Chunk]:
        self._load_query_types()
        out: list[Chunk] = []
        for passage in passages:
            meta = {
                "language": str(passage["language"]),
                "script": str(passage["script"]),
                "query_type": self._query_type.get(str(passage["query_id"]), "UNKNOWN"),
                "is_selected_any": str(passage["is_selected_any"]),
                "position": str(passage["position"]),
            }
            for chunk in self._c1.chunk_one(passage):
                # model_copy keeps the frozen model frozen; every other field,
                # including chunk_id and ordinal, is carried through untouched so
                # the list stays identical to C1's.
                out.append(chunk.model_copy(update={"meta": meta}))
        return out
