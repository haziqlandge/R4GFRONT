"""C7: doc2query, query-aligned. Job J4.

Index each query's text as an extra vector pointing at the passage that answers
it, on top of the C1 chunk vectors. The premise is that a stored query matches a
future similar query far better than the passage prose does, which is the
highest-leverage idea available on this corpus (Architecture.md 4) and the one
Memory.md A5 predicts will win.

╔══════════════════════════════════════════════════════════════════════════╗
║  READ THIS BEFORE CHANGING `indexable_splits`.                           ║
║                                                                          ║
║  The benchmark's 250 queries ARE the `test` split. Indexing a test        ║
║  query's own text against its own gold passage puts the exact answer key  ║
║  into the index: searching that query then matches a vector that IS the   ║
║  query, pointing at the passage it is scored on. Recall goes near 1.0 and ║
║  the number is meaningless.                                              ║
║                                                                          ║
║  Phase3-Parallel.md J4 sizes this strategy at "~30,000 extra vectors",    ║
║  i.e. all 15,000 queries x 2 languages - which would include all 1,000    ║
║  test queries and leak. That estimate is the trap, not the target.       ║
║  Default here is `corpus_only` (12,000 queries, 24,000 vectors).          ║
╚══════════════════════════════════════════════════════════════════════════╝

**The deeper problem, which the split filter does not solve.** Real doc2query
generates *synthetic* queries for a passage, so a stored query can resemble a
future unseen query for that same passage. This corpus gives each passage group
exactly one real query, and for an evaluated passage that query IS the evaluation
query. So there is no honest way for a real-query C7 to help a benchmark query:
either the query is indexed (leakage) or the passage is unaugmented (no effect).
Excluding test and dev leaves the bench queries' own passages untouched, so C7
here can only match C1 or add noise.

That makes C7-as-specified unmeasurable on this dataset rather than merely
disappointing, and it puts a real condition on A5: doc2query cannot be validated
here without an LLM to generate queries, which is the same blocker as C4. Build
the honest variant, report that it does not move, and say why - a strategy that
cannot be tested is a finding, not a gap in the table.

`--leaky` exists to quantify what the mistake would have looked like. It is never
the default and its meta.json is stamped `leaky: true`.
"""

from __future__ import annotations

from typing import Iterable

import pyarrow.parquet as pq

from ..config import QUERIES_PARQUET
from ..retrieval.embedder import Embedder
from .base import Chunk, PassageRecord
from .c1_fixed import FixedChunker

# Splits whose query text may enter the index. `test` is the benchmark and `dev`
# is reserved for Phase 5 threshold calibration; indexing either contaminates the
# partition it belongs to (config.py, Rules.md 5).
SAFE_SPLITS = frozenset({"corpus_only"})
ALL_SPLITS = frozenset({"corpus_only", "dev", "test"})


class Doc2QueryChunker:
    name = "c7"

    def __init__(
        self,
        embedder: Embedder,
        indexable_splits: frozenset[str] = SAFE_SPLITS,
        **kwargs: object,
    ) -> None:
        self._c1 = FixedChunker(embedder, **kwargs)  # type: ignore[arg-type]
        self.embedder = embedder
        self.indexable_splits = frozenset(indexable_splits)
        self.query_vectors = 0
        self.skipped_unsafe = 0

    @property
    def truncated_count(self) -> int:
        return self._c1.truncated_count

    @property
    def leaky(self) -> bool:
        """True when a partition reserved for evaluation is being indexed."""
        return bool(self.indexable_splits - SAFE_SPLITS)

    def params(self) -> dict[str, object]:
        return {
            **self._c1.params(),
            "strategy": self.name,
            "unit": "sub-passage + query vector",
            "reuses": "c1",
            "indexable_splits": sorted(self.indexable_splits),
            "leaky": self.leaky,
            "query_vectors": self.query_vectors,
            "skipped_unsafe_queries": self.skipped_unsafe,
        }

    def chunk(self, passages: Iterable[PassageRecord]) -> list[Chunk]:
        passages = list(passages)
        present = {str(p["passage_id"]) for p in passages}

        out: list[Chunk] = []
        for passage in passages:
            out.extend(self._c1.chunk_one(passage))

        parallel_of = {str(p["passage_id"]): str(p["parallel_id"]) for p in passages}

        queries = pq.read_table(
            QUERIES_PARQUET,
            columns=["query_id", "query_en", "query_hi", "gold_en_ids",
                     "gold_hi_ids", "split"],
        ).to_pylist()

        self.query_vectors = 0
        self.skipped_unsafe = 0
        for row in queries:
            if str(row["split"]) not in self.indexable_splits:
                self.skipped_unsafe += 1
                continue
            for language, text_key, gold_key in (
                ("en", "query_en", "gold_en_ids"),
                ("hi", "query_hi", "gold_hi_ids"),
            ):
                text = (row[text_key] or "").strip()
                gold = list(row[gold_key] or ())
                if not text or not gold:
                    continue
                passage_id = str(gold[0])
                # A --limit smoke build holds only a slice of the corpus, so a
                # query may point at a passage that is not in this index.
                if passage_id not in present:
                    continue
                self.query_vectors += 1
                out.append(
                    Chunk(
                        # `q` marks these rows in the id itself, so a chunk that
                        # turns up in a trace is identifiable as query-derived
                        # without a join against meta.
                        chunk_id=f"{passage_id}#q{row['query_id']}",
                        text=text,
                        passage_id=passage_id,
                        parallel_id=parallel_of.get(passage_id, ""),
                        language=language,
                        ordinal=0,
                        token_count=len(
                            self.embedder.tokenizer.encode(
                                text, add_special_tokens=False
                            ).ids
                        ),
                        meta={
                            "source": "query",
                            "query_id": str(row["query_id"]),
                            "split": str(row["split"]),
                        },
                    )
                )
        return out
