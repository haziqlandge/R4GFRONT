"""Build a dense HNSW index for one chunking strategy. Phase 2, extended in Phase 3.

    python scripts/02_build_indexes.py --strategy c1
    python scripts/02_build_indexes.py --strategy c2 --backend cuda-fp16 --device-tag EMBED

OWNED BY BENCH. Do not edit on another box - see chunking/registry.py.
Strategies come from the registry; adding one means adding a chunker file and
one registry line, never touching this dispatch.

Phase 3 adds seven more strategies; each lands in its own namespace under
artifacts/indexes/<strategy>/ so nothing here needs restructuring.

Two implementation notes worth the words:

  Length-sorted batching. The tokenizer pads each batch to its longest member, so
  a batch mixing a 20-token chunk with a 380-token chunk wastes most of its
  compute on padding. Sorting by token length before batching makes batches
  near-uniform and measured 1.46x faster on this corpus. Vectors are scattered
  back to input order afterwards, so index order still matches chunks.parquet.

  Thread count. Measured on a 12-core box: 8 threads gives 210 chunks/sec, 16
  gives 61. Oversubscription is 3.4x SLOWER, not marginally worse. This is the
  concrete version of the warning in Rules.md 2.2.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import hnswlib
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.chunking import registry  # noqa: E402
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

# J10 / ISSUES.md I10: two passages of 295,890 have text "-" and act as
# attractors for meaningless queries, because a degenerate embedding sits near
# the centroid of the space and is therefore mildly similar to everything. Filter
# them at index build time. The frozen slice itself is NOT changed - Rules.md 5 -
# and the filtered count goes into meta.json so the number is visible.
MIN_PASSAGE_CHARS = 4


def drop_degenerate(passages: list[dict]) -> tuple[list[dict], int]:
    kept = [p for p in passages if len((p["text"] or "").strip()) >= MIN_PASSAGE_CHARS]
    return kept, len(passages) - len(kept)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def embed_all(embedder: Embedder, texts: list[str]) -> np.ndarray:
    """Length-sorted batch embedding. Returns vectors in the ORIGINAL order."""
    lengths = np.fromiter(
        (len(embedder.tokenizer.encode(t, add_special_tokens=False).ids) for t in texts),
        dtype=np.int32,
        count=len(texts),
    )
    order = np.argsort(lengths, kind="stable")
    out = np.empty((len(texts), EMBED_DIM), dtype=np.float32)

    started = time.perf_counter()
    done = 0
    for i in range(0, len(order), BATCH):
        idx = order[i : i + BATCH]
        out[idx] = embedder.encode([texts[j] for j in idx], "passage")
        done += len(idx)
        if done % (BATCH * 250) == 0 or done == len(order):
            rate = done / (time.perf_counter() - started)
            eta = (len(order) - done) / max(rate, 1e-9)
            print(
                f"    {done:>7,}/{len(order):,}  {rate:>6.1f}/s  eta {eta / 60:>5.1f} min",
                flush=True,
            )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", default="c1",
                        help=f"one of: {', '.join(registry.ALL_STRATEGIES)}")
    parser.add_argument("--backend", default="onnx-cpu",
                        choices=["onnx-cpu", "cuda-fp16"],
                        help="cuda-fp16 requires scripts/_gpu_embedder.py (J1)")
    parser.add_argument("--device-tag", default="BENCH",
                        choices=["BENCH", "EMBED", "LLM"],
                        help="which box built this; recorded in meta.json")
    parser.add_argument("--list", action="store_true",
                        help="show registered strategies and who owns each")
    parser.add_argument("--threads", type=int, default=ONNX_THREADS_BUILD)
    parser.add_argument("--limit", type=int, default=0, help="debug: cap passages")
    args = parser.parse_args()

    if args.list:
        print("")
        print(f"  implemented: {', '.join(registry.implemented())}")
        for name, owner in sorted(registry.pending().items()):
            print(f"  pending      {name}  {owner}")
        return 0

    model_path = ONNX_DIR / INT8_MODEL
    embedder = Embedder(model_path, ONNX_DIR / TOKENIZER_FILE, threads=args.threads)

    if args.backend != "onnx-cpu":
        # The GPU path lives in scripts/_gpu_embedder.py (J1, owned by EMBED) and
        # is offline-only. Rules.md 2.1 still bans PyTorch at request time, and
        # retrieval/embedder.py is hot-path code that stays untouched.
        raise SystemExit(
            f"backend '{args.backend}' needs scripts/_gpu_embedder.py, which is "
            "job J1 on EMBED. It must pass its parity gate (D10/A13) before any "
            "GPU-built index is trusted."
        )

    # A wrong --strategy is a CLI mistake, not a crash. Print what is wrong and
    # who owns the missing piece, and exit; a traceback here just buries it.
    try:
        chunker = registry.get(args.strategy)(embedder)
    except (registry.PendingStrategy, KeyError) as exc:
        raise SystemExit(str(exc).strip('"')) from None

    print("")
    print(f"  strategy   {chunker.name}  {chunker.params()}")
    print(f"  backend    {args.backend}   device {args.device_tag}   threads {args.threads}")
    passages = pq.read_table(PASSAGES_PARQUET).to_pylist()
    if args.limit:
        passages = passages[: args.limit]
    passages, dropped = drop_degenerate(passages)
    print(f"  passages   {len(passages):,}  ({dropped} degenerate dropped)")

    t0 = time.perf_counter()
    chunks = chunker.chunk(passages)
    chunk_secs = time.perf_counter() - t0
    print(
        f"  chunks     {len(chunks):,}  "
        f"({len(chunks) / len(passages):.3f}/passage, "
        f"{chunker.truncated_count} truncated, {chunk_secs:.1f}s)"
    )

    print(f"  embedding  {args.threads} threads, batch {BATCH}, length-sorted")
    t0 = time.perf_counter()
    vectors = embed_all(embedder, [c.text for c in chunks])
    embed_secs = time.perf_counter() - t0
    print(f"  embedded   {embed_secs / 60:.1f} min")

    print(f"  hnsw       M={HNSW_M} ef_construction={HNSW_EF_CONSTRUCTION} space=ip")
    t0 = time.perf_counter()
    # space="ip": the embedder already L2-normalises, so inner product IS cosine
    # and hnswlib's internal renormalisation is skipped.
    index = hnswlib.Index(space="ip", dim=EMBED_DIM)
    index.init_index(
        max_elements=len(chunks), M=HNSW_M, ef_construction=HNSW_EF_CONSTRUCTION
    )
    index.set_num_threads(args.threads)
    index.add_items(vectors, np.arange(len(chunks)))
    index_secs = time.perf_counter() - t0
    print(f"  built      {index_secs / 60:.1f} min")

    out_dir = INDEX_DIR / chunker.name
    out_dir.mkdir(parents=True, exist_ok=True)
    index.save_index(str(out_dir / "index.bin"))
    pq.write_table(
        pa.Table.from_pylist([c.model_dump() for c in chunks]),
        out_dir / "chunks.parquet",
        compression="zstd",
    )

    manifest = json.loads(SLICE_MANIFEST.read_text(encoding="utf-8"))
    meta = {
        "strategy": chunker.name,
        "params": chunker.params(),
        "embedder": {
            "model": INT8_MODEL,
            "sha256": file_sha256(model_path),
            "dim": EMBED_DIM,
        },
        "hnsw": {"space": "ip", "M": HNSW_M, "ef_construction": HNSW_EF_CONSTRUCTION},
        "counts": {
            "passages": len(passages),
            "chunks": len(chunks),
            "truncated": chunker.truncated_count,
            "degenerate_dropped": dropped,
            "tokens_embedded": int(sum(c.token_count for c in chunks)),
        },
        # Devices.md 3: wall-clock is an annotation, not a comparison metric.
        # Eight strategies built on three machines across two backends would
        # otherwise produce a table that compares hardware, not strategies.
        "build_env": {
            "device_tag": args.device_tag,
            "backend": args.backend,
            "threads": args.threads,
        },
        "build_seconds": {
            "chunk": round(chunk_secs, 1),
            "embed": round(embed_secs, 1),
            "index": round(index_secs, 1),
        },
        # Binds this index to one exact corpus. An index paired with a different
        # slice would produce plausible but meaningless retrieval numbers.
        "slice_records_sha256": manifest["integrity"]["records_sha256"],
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    size_mb = (out_dir / "index.bin").stat().st_size / 1_048_576
    print("")
    print(f"  wrote {out_dir}  (index.bin {size_mb:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
