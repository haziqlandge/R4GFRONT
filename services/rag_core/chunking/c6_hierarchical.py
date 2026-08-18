"""C6: hierarchical parent-child. Job J14.

Children are the C1 chunks, unchanged and already embedded. The parent is the
`query_id` passage group - roughly ten passages that MS MARCO retrieved for the
same query - and it is returned as generation context once a child matches.

**The parent layer is a lookup table, not a second index.** Nothing about the
parent is embedded or searched. Retrieval finds a child exactly as C1 does, then
expands to the parent to build the context handed to the answer stage. That is
why this strategy costs no new embeddings and belongs on the GPU-less box
(Phase3-Parallel.md 1).

Why the query group is the right parent here. Architecture.md 4.1 already
observed that this corpus has no long documents to split - the usual
parent-child setup (parent = section, child = paragraph) has nothing to work
with when passages top out at 205 words. The query group is the only genuine
supra-passage structure the data contains: ten passages about the same question,
which is exactly the shape of "more context around the match" that a
parent-child strategy is supposed to supply.

The honest risk, which the comparison table must not hide. A parent of ten
passages is roughly 2,000 words of context. Retrieval metrics measured on the
child (Recall@10, MRR) are IDENTICAL to C1's by construction, because the child
index IS C1's index - so a results table that lists C6's Recall@10 next to C1's
is comparing a number to itself. C6's effect is on answer quality and on prompt
size, neither of which Phase 3 measures. J15 should report it as a tie with a
footnote, and Phase 5 should measure whether the extra context helps or merely
costs tokens.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from ..retrieval.embedder import Embedder
from .base import Chunk, PassageRecord
from .c1_fixed import FixedChunker

# Cap on passages returned as parent context. The query groups in this slice run
# to about ten passages; a runaway group would otherwise put thousands of words
# into a prompt with a 160-token answer cap behind it.
MAX_PARENT_PASSAGES = 10


class HierarchicalChunker:
    name = "c6"

    def __init__(self, embedder: Embedder, **kwargs: object) -> None:
        self._c1 = FixedChunker(embedder, **kwargs)  # type: ignore[arg-type]
        self.embedder = embedder
        # query_id -> ordered passage_ids. Written beside the index by the
        # builder as parents.json; this is the whole "parent layer".
        self.parents: dict[str, list[str]] = {}

    @property
    def truncated_count(self) -> int:
        return self._c1.truncated_count

    def params(self) -> dict[str, object]:
        return {
            **self._c1.params(),
            "strategy": self.name,
            "unit": "sub-passage child, query-group parent",
            "reuses": "c1",
            "max_parent_passages": MAX_PARENT_PASSAGES,
        }

    def chunk(self, passages: Iterable[PassageRecord]) -> list[Chunk]:
        passages = list(passages)

        # Parent groups are per (query_id, language): the en and hi passages for
        # one query are translations of each other, not additional context, and
        # mixing them would hand a Hindi answer stage ten English passages.
        groups: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
        for passage in passages:
            key = (str(passage["query_id"]), str(passage["language"]))
            groups[key].append((int(passage["position"]), str(passage["passage_id"])))

        self.parents = {}
        parent_of: dict[str, str] = {}
        for (query_id, language), members in groups.items():
            parent_id = f"{query_id}:{language}"
            ordered = [pid for _, pid in sorted(members)][:MAX_PARENT_PASSAGES]
            self.parents[parent_id] = ordered
            for passage_id in ordered:
                parent_of[passage_id] = parent_id

        out: list[Chunk] = []
        for passage in passages:
            passage_id = str(passage["passage_id"])
            parent_id = parent_of.get(passage_id, "")
            siblings = len(self.parents.get(parent_id, ()))
            for chunk in self._c1.chunk_one(passage):
                out.append(
                    chunk.model_copy(
                        update={
                            "meta": {
                                "parent_id": parent_id,
                                "parent_passages": str(siblings),
                            }
                        }
                    )
                )
        return out
