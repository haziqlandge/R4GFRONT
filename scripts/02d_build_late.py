"""Build the C8 late-chunking index. Job J8.

    python scripts/02d_build_late.py

OWNED BY BENCH, like the other 02* scripts.

Why this is a separate script rather than a --strategy on 02_build_indexes.py:
that script embeds each chunk's text INDEPENDENTLY, which is exactly the context
loss C8 exists to test against. Late chunking inverts the order - encode the
whole passage once, then mean-pool each span out of the token-level hidden
states - so every chunk vector is computed in the presence of its neighbours.

Same spans as C1 (guaranteed by tests/test_late_chunker.py), so C8 vs C1 isolates
one variable: context, not segmentation.
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
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _progress import Progress  # noqa: E402
from scripts_helpers import chunks_to_table  # noqa: E402

from rag_core.chunking.c8_late import LateChunker  # noqa: E402
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

BATCH_PASSAGES = 16
MIN_PASSAGE_CHARS = 4


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def late_embed(
    embedder: Embedder, passages: list[dict], chunker: LateChunker
) -> tuple[list, np.ndarray]:
    """Encode each passage whole, then mean-pool per C1 span.

    The pooling is the entire point. `Embedder.encode` would mean-pool the WHOLE
    sequence and return one vector; here we reach the pre-pooling
    last_hidden_state and take a masked mean over each span's token range, so a
    chunk's vector reflects the passage it sits in.
    """
    all_chunks: list = []
    vectors: list[np.ndarray] = []

    started = time.perf_counter()
    progress = Progress(total=len(passages), label="late")
    done = 0

    for i in range(0, len(passages), BATCH_PASSAGES):
        batch = passages[i : i + BATCH_PASSAGES]
        batch_chunks = [chunker.chunk_one(p) for p in batch]

        texts = ["passage: " + p["text"] for p in batch]
        encodings = embedder.tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)

        feeds: dict[str, np.ndarray] = {"input_ids": input_ids}
        if "attention_mask" in embedder._input_names:
            feeds["attention_mask"] = attention_mask
        if "token_type_ids" in embedder._input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids)

        hidden = embedder.session.run(None, feeds)[0]  # (b, seq, 384)

        for row, chunks in enumerate(batch_chunks):
            if not chunks:
                continue
            # "passage: " prefix occupies leading tokens, and the tokenizer adds a
            # BOS. Chunk spans are indices into the RAW passage tokens, so shift
            # past the prefix; clamp so a truncated sequence can never read padding.
            prefix_len = len(
                embedder.tokenizer.encode("passage: ", add_special_tokens=False).ids
            )
            offset = 1 + prefix_len
            seq_len = int(attention_mask[row].sum())
            for c in chunks:
                s = offset + int(c.meta["tok_start"])
                e = offset + int(c.meta["tok_end"])
                s, e = min(s, seq_len - 1), min(e, seq_len)
                if e <= s:
                    continue
                span = hidden[row, s:e, :]
                pooled = span.mean(axis=0)
                norm = float(np.linalg.norm(pooled))
                vectors.append((pooled / max(norm, 1e-12)).astype(np.float32))
                all_chunks.append(c)

        done += len(batch)
        progress.report(done, time.perf_counter() - started,
                        extra={"chunks": f"{len(all_chunks):,}"})

    return all_chunks, np.vstack(vectors) if vectors else np.empty((0, EMBED_DIM), np.float32)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--threads", type=int, default=ONNX_THREADS_BUILD)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    embedder = Embedder(ONNX_DIR / INT8_MODEL, ONNX_DIR / TOKENIZER_FILE, threads=args.threads)
    chunker = LateChunker(embedder)

    passages = pq.read_table(PASSAGES_PARQUET).to_pylist()
    if args.limit:
        passages = passages[: args.limit]
    before = len(passages)
    passages = [p for p in passages if len((p["text"] or "").strip()) >= MIN_PASSAGE_CHARS]
    dropped = before - len(passages)

    print("")
    print(f"  strategy   c8  {chunker.params()}")
    print(f"  passages   {len(passages):,}  ({dropped} degenerate dropped)")
    print(f"  method     whole-passage encode, mean-pool per C1 span")

    t0 = time.perf_counter()
    chunks, vectors = late_embed(embedder, passages, chunker)
    embed_secs = time.perf_counter() - t0
    print(f"  chunks     {len(chunks):,}  ({chunker.truncated_count} truncated)")
    print(f"  embedded   {embed_secs / 60:.1f} min")

    t0 = time.perf_counter()
    index = hnswlib.Index(space="ip", dim=EMBED_DIM)
    index.init_index(max_elements=len(chunks), M=HNSW_M, ef_construction=HNSW_EF_CONSTRUCTION)
    index.set_num_threads(args.threads)
    index.add_items(vectors, np.arange(len(chunks)))
    index_secs = time.perf_counter() - t0
    print(f"  built      {index_secs / 60:.1f} min")

    out_dir = INDEX_DIR / ("c8-smoke" if args.limit else "c8")
    out_dir.mkdir(parents=True, exist_ok=True)
    index.save_index(str(out_dir / "index.bin"))
    pq.write_table(chunks_to_table(chunks), out_dir / "chunks.parquet", compression="zstd")

    manifest = json.loads(SLICE_MANIFEST.read_text(encoding="utf-8"))
    (out_dir / "meta.json").write_text(json.dumps({
        "strategy": "c8",
        "params": chunker.params(),
        "embedder": {"model": INT8_MODEL, "sha256": file_sha256(ONNX_DIR / INT8_MODEL), "dim": EMBED_DIM},
        "hnsw": {"space": "ip", "M": HNSW_M, "ef_construction": HNSW_EF_CONSTRUCTION},
        "counts": {
            "passages": len(passages), "chunks": len(chunks),
            "truncated": chunker.truncated_count, "degenerate_dropped": dropped,
            "tokens_embedded": int(sum(c.token_count for c in chunks)),
        },
        "build_seconds": {"embed": round(embed_secs, 1), "index": round(index_secs, 1)},
        "build_env": {"device_tag": "BENCH", "backend": "onnx-cpu", "threads": args.threads},
        "slice_records_sha256": manifest["integrity"]["records_sha256"],
    }, indent=2), encoding="utf-8")

    print("")
    print(f"  wrote {out_dir}  (index.bin {(out_dir / 'index.bin').stat().st_size / 1_048_576:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
