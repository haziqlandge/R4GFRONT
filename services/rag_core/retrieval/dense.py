"""hnswlib dense index. Loaded once at startup, searched in-process.

Rules.md 2.1: no hosted vector DB and no disk reads at request time. The index is
read from disk during lifespan startup and stays resident.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import hnswlib
import numpy as np
import pyarrow.parquet as pq

from ..chunking.base import ChunkRecord
from ..config import EMBED_DIM, HNSW_EF_SEARCH, INDEX_DIR
from ..harness.errors import IndexNotReady


class DenseIndex:
    """One chunking strategy's index plus its chunk metadata."""

    def __init__(self, strategy: str, ef_search: int = HNSW_EF_SEARCH) -> None:
        self.strategy = strategy
        self.dir = INDEX_DIR / strategy
        self.ef_search = ef_search
        self.index: hnswlib.Index | None = None
        self.chunks: list[ChunkRecord] = []
        self.meta: dict[str, Any] = {}

    def load(self) -> None:
        index_path = self.dir / "index.bin"
        if not index_path.exists():
            raise IndexNotReady(
                f"{index_path} missing. Run scripts/02_build_indexes.py."
            )

        self.meta = json.loads((self.dir / "meta.json").read_text(encoding="utf-8"))
        self.chunks = pq.read_table(self.dir / "chunks.parquet").to_pylist()

        index = hnswlib.Index(space="ip", dim=EMBED_DIM)
        index.load_index(str(index_path), max_elements=len(self.chunks))
        # ef is NOT serialised with the index - it must be set after every load.
        # Forgetting it leaves the default (10), which silently wrecks recall
        # while looking fast. hnswlib documents this explicitly.
        index.set_ef(self.ef_search)
        index.set_num_threads(1)  # the hot path is one query; threads add overhead
        self.index = index

    @property
    def ready(self) -> bool:
        return self.index is not None

    def search(self, vector: np.ndarray, k: int) -> list[tuple[int, float]]:
        """Returns (row, score) with score as cosine similarity, descending."""
        if self.index is None:
            raise IndexNotReady("dense index not loaded")
        k = min(k, len(self.chunks))
        labels, distances = self.index.knn_query(vector.reshape(1, -1), k=k)
        # space="ip" returns distance = 1 - inner_product; vectors are normalised,
        # so inner product is the cosine similarity.
        return [(int(l), float(1.0 - d)) for l, d in zip(labels[0], distances[0])]

    def chunk(self, row: int) -> ChunkRecord:
        return self.chunks[row]
