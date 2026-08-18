"""Build C1-derived indexes without re-embedding. Jobs J13, J14, and C7.

    python scripts/02c_build_derived.py --strategy c5
    python scripts/02c_build_derived.py --strategy c7
    python scripts/02c_build_derived.py --strategy c7 --leaky   # see below

OWNED BY BENCH, like 02_build_indexes.py.

C5, C6 and C7 all keep C1's chunk spans exactly: C5 changes only the payload, C6
adds a parent lookup table, C7 appends query vectors. Their chunk text is C1's
chunk text, so their vectors are C1's vectors, so re-running the embedder over
379,240 unchanged strings would spend about 30 minutes per strategy to reproduce
numbers that already exist on disk. This script copies them instead:

  c5, c6   copy C1's index.bin verbatim, write a new chunks.parquet
  c7       load C1's index, embed ONLY the ~24,000 query rows, append them

**The integrity gate is the reason this is safe.** Before reusing anything, the
derived chunk list is compared against C1's chunks.parquet id by id. Reusing
C1's vectors under a chunk list that has drifted would attach every vector to
the wrong text and produce an index that looks fine, loads fine, and retrieves
nonsense. The check is cheap; the failure it prevents is not.

--leaky (C7 only) additionally indexes the dev and test query text. It exists to
measure what the leak would have been worth and must never produce the C7 that
enters the comparison table. See c7_doc2query.py.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import hnswlib
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts_helpers import chunks_to_table  # noqa: E402
from rag_core.chunking import registry  # noqa: E402
from rag_core.chunking.c7_doc2query import ALL_SPLITS  # noqa: E402
from rag_core.config import (  # noqa: E402
    EMBED_DIM,
    HNSW_EF_CONSTRUCTION,
    HNSW_M,
    INDEX_DIR,
    INT8_MODEL,
    ONNX_DIR,
    ONNX_THREADS_BUILD,
    PASSAGES_PARQUET,
    SLICE_MANIFEST,
    TOKENIZER_FILE,
)
from rag_core.retrieval.embedder import Embedder  # noqa: E402

BATCH = 32
BASE = "c1"

# Must match 02_build_indexes.py (J10 / ISSUES.md I10). Replicated rather than
# imported because that file is a script, not a module; the chunk-id gate below
# catches any drift between the two immediately.
MIN_PASSAGE_CHARS = 4


def drop_degenerate(passages: list[dict]) -> tuple[list[dict], int]:
    kept = [p for p in passages if len(p["text"].strip()) >= MIN_PASSAGE_CHARS]
    return kept, len(passages) - len(kept)


def embed_texts(embedder: Embedder, texts: list[str]) -> np.ndarray:
    """Length-sorted batching, original order out. Same shape as 02's embed_all."""
    lengths = np.fromiter(
        (len(embedder.tokenizer.encode(t, add_special_tokens=False).ids) for t in texts),
        dtype=np.int32,
        count=len(texts),
    )
    order = np.argsort(lengths, kind="stable")
    out = np.empty((len(texts), EMBED_DIM), dtype=np.float32)
    started = time.perf_counter()
    for i in range(0, len(order), BATCH):
        idx = order[i : i + BATCH]
        out[idx] = embedder.encode([texts[j] for j in idx], "passage")
        done = min(i + BATCH, len(order))
        if done % (BATCH * 100) == 0 or done == len(order):
            rate = done / (time.perf_counter() - started)
            print(f"    {done:>7,}/{len(order):,}  {rate:>6.1f}/s", flush=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", required=True,
                        help="c5, c6 or c7")
    parser.add_argument("--leaky", action="store_true",
                        help="C7 only: also index dev/test queries. Never the default.")
    args = parser.parse_args()

    name = args.strategy
    derived = registry.REUSES_C1_VECTORS | registry.EXTENDS_C1_VECTORS
    if name not in derived:
        print(f"  {name} is not a C1-derived strategy. "
              f"Derived: {', '.join(sorted(derived))}.")
        print(f"  Use scripts/02_build_indexes.py --strategy {name}.")
        return 1

    base_dir = INDEX_DIR / BASE
    if not (base_dir / "index.bin").exists():
        print(f"  {base_dir}/index.bin missing. Build c1 first.")
        return 1

    embedder = Embedder(
        model_path=ONNX_DIR / INT8_MODEL,
        tokenizer_path=ONNX_DIR / TOKENIZER_FILE,
        threads=ONNX_THREADS_BUILD,
    )

    kwargs: dict[str, object] = {}
    if name == "c7" and args.leaky:
        kwargs["indexable_splits"] = ALL_SPLITS
    chunker = registry.get(name)(embedder, **kwargs)

    print("")
    print(f"  strategy   {chunker.name}  (derived from {BASE}, no full re-embed)")
    passages = pq.read_table(PASSAGES_PARQUET).to_pylist()
    passages, dropped = drop_degenerate(passages)
    print(f"  passages   {len(passages):,}  ({dropped} degenerate dropped)")

    t0 = time.perf_counter()
    chunks = chunker.chunk(passages)
    chunk_secs = time.perf_counter() - t0
    print(f"  chunks     {len(chunks):,}  ({chunk_secs:.1f}s)")

    # ---- the integrity gate -------------------------------------------------
    base_ids = pq.read_table(base_dir / "chunks.parquet",
                             columns=["chunk_id"]).column("chunk_id").to_pylist()
    derived_ids = [c.chunk_id for c in chunks]
    shared = derived_ids[: len(base_ids)]
    if shared != base_ids:
        first = next((i for i, (a, b) in enumerate(zip(shared, base_ids)) if a != b),
                     min(len(shared), len(base_ids)))
        print("")
        print(f"  ABORT chunk list has drifted from {BASE} at row {first:,}.")
        print(f"        {BASE}: {base_ids[first] if first < len(base_ids) else '<end>'}")
        print(f"        {name}: {shared[first] if first < len(shared) else '<end>'}")
        print("        Reusing c1's vectors under a different chunk list would bind")
        print("        every vector to the wrong text. Build this strategy with")
        print("        scripts/02_build_indexes.py instead.")
        return 1
    extra = len(derived_ids) - len(base_ids)
    print(f"  gate       PASS  first {len(base_ids):,} chunk ids identical to {BASE}"
          + (f", +{extra:,} appended" if extra else ""))

    # A --leaky build is a diagnostic and must NEVER overwrite the canonical
    # index, for the same reason --limit writes to <strategy>-smoke/ in
    # 02_build_indexes.py: this project has already lost a finished index to a
    # throwaway run once. A leaky c7 sitting at artifacts/indexes/c7/ would be
    # indistinguishable from the real one at a glance and would quietly put a
    # fabricated number into the comparison table.
    out_dir = INDEX_DIR / (f"{name}-leaky" if getattr(chunker, "leaky", False) else name)
    out_dir.mkdir(parents=True, exist_ok=True)
    if getattr(chunker, "leaky", False):
        print(f"  LEAKY      writing to {out_dir.name}/ (canonical {name}/ untouched)")
    embed_secs = 0.0
    index_secs = 0.0

    if extra == 0:
        # Vectors are unchanged: copy the built graph rather than rebuilding it.
        t0 = time.perf_counter()
        shutil.copyfile(base_dir / "index.bin", out_dir / "index.bin")
        index_secs = time.perf_counter() - t0
        print(f"  index      copied from {BASE} ({index_secs:.1f}s, 0 embeddings)")
    else:
        new_texts = [c.text for c in chunks[len(base_ids):]]
        print(f"  embedding  {len(new_texts):,} new rows only "
              f"({ONNX_THREADS_BUILD} threads)")
        t0 = time.perf_counter()
        vectors = embed_texts(embedder, new_texts)
        embed_secs = time.perf_counter() - t0
        print(f"  embedded   {embed_secs:.1f}s")

        t0 = time.perf_counter()
        index = hnswlib.Index(space="ip", dim=EMBED_DIM)
        index.load_index(str(base_dir / "index.bin"), max_elements=len(chunks))
        index.add_items(vectors, np.arange(len(base_ids), len(chunks)))
        index_secs = time.perf_counter() - t0
        print(f"  index      loaded {BASE} + appended {extra:,} ({index_secs:.1f}s)")
        index.save_index(str(out_dir / "index.bin"))

    pq.write_table(
        chunks_to_table(chunks),
        out_dir / "chunks.parquet",
        compression="zstd",
    )

    # C6's parent layer. A lookup table beside the index, not a second index.
    if name == "c6":
        parents = getattr(chunker, "parents", {})
        (out_dir / "parents.json").write_text(
            json.dumps(parents, ensure_ascii=False), encoding="utf-8"
        )
        sizes = [len(v) for v in parents.values()]
        print(f"  parents    {len(parents):,} groups, "
              f"mean {sum(sizes) / max(len(sizes), 1):.1f} passages each")

    manifest = json.loads(SLICE_MANIFEST.read_text(encoding="utf-8"))
    base_meta = json.loads((base_dir / "meta.json").read_text(encoding="utf-8"))
    meta = {
        "strategy": chunker.name,
        "params": chunker.params(),
        "embedder": base_meta["embedder"],
        "hnsw": {"space": "ip", "M": HNSW_M, "ef_construction": HNSW_EF_CONSTRUCTION},
        "counts": {
            "passages": len(passages),
            "chunks": len(chunks),
            "truncated": chunker.truncated_count,
            "degenerate_dropped": dropped,
            "appended_vectors": extra,
        },
        "derived_from": {
            "strategy": BASE,
            "reused_vectors": len(base_ids),
            "chunk_ids_verified_identical": True,
        },
        "build_env": {
            "device_tag": "BENCH",
            "backend": "onnx-cpu",
            "threads": ONNX_THREADS_BUILD,
        },
        "build_seconds": {
            "chunk": round(chunk_secs, 1),
            "embed": round(embed_secs, 1),
            "index": round(index_secs, 1),
        },
        "slice_records_sha256": manifest["integrity"]["records_sha256"],
    }
    if name == "c7":
        meta["leaky"] = bool(getattr(chunker, "leaky", False))
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    size_mb = (out_dir / "index.bin").stat().st_size / 1_048_576
    print("")
    print(f"  wrote {out_dir}  (index.bin {size_mb:.0f} MB)")
    if meta.get("leaky"):
        print("  WARNING this index is LEAKY. Diagnostic only - never publish it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
