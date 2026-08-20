"""Why the reranker costs twice as much inside the service as it does alone.

Measured on the deployed n2-standard-4: the cross-encoder scores one pair in
~18 ms standalone, so depth 3 should be ~55 ms - and the live service reports a
rerank stage median of ~118 ms. The stage does nothing else expensive, so the
difference has to be contention, not work.

The suspect is ONNX Runtime's intra-op thread pool. `rag_core` holds TWO
sessions, the embedder and the cross-encoder, each configured with
`intra_op_num_threads = ONNX_THREADS_SERVING`. That is 8 worker threads on 4
vCPUs, and ORT's pool SPINS after finishing a task rather than sleeping
immediately - so the embedder's threads can still be burning cores while the
reranker runs, on a request that uses both.

Four arms, same pairs, same order:

    A  reranker alone                       control
    B  embedder + reranker, embed each time  what the service actually does
    C  B with session.intra_op.allow_spinning = 0
    D  B with the embedder pinned to 1 thread

If B is far above A and C or D recovers it, the fix is a session option rather
than a bigger machine.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import numpy as np
import onnxruntime as ort

ROOT: Final[Path] = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from rag_core.config import (  # noqa: E402
    ARTIFACTS_DIR,
    INT8_MODEL,
    ONNX_DIR,
    ONNX_THREADS_SERVING,
    RERANK_MODEL_FILE,
    RERANK_TOKENIZER_FILE,
    RERANKER,
    RESULTS_DIR,
    TOKENIZER_FILE,
)
from rag_core.retrieval.embedder import Embedder  # noqa: E402
from rag_core.retrieval.rerank import CrossEncoder  # noqa: E402

WARM: Final[int] = 10
DEPTH: int = 3


def load_pairs(n: int, seed: int) -> list[tuple[str, list[str]]]:
    import pyarrow.parquet as pq

    queries = [
        json.loads(line)
        for line in (ROOT / "bench" / "queries_250.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    texts = pq.read_table(ARTIFACTS_DIR / "passages.parquet", columns=["text"]).column("text").to_pylist()
    rng = random.Random(seed)
    out: list[tuple[str, list[str]]] = []
    for i in range(n):
        q = rng.choice(queries)
        field = "query_en" if i % 2 == 0 else "query_hi"
        out.append((str(q.get(field) or q["query_en"]), [str(rng.choice(texts)) for _ in range(DEPTH)]))
    return out


def build_reranker(threads: int, spin: bool) -> CrossEncoder:
    rerank_dir = ONNX_DIR / f"rerank-{RERANKER}"
    enc = CrossEncoder(rerank_dir / RERANK_MODEL_FILE, rerank_dir / RERANK_TOKENIZER_FILE, threads=threads)
    if not spin:
        enc.session = _rebuild(rerank_dir / RERANK_MODEL_FILE, threads, spin=False)
    return enc


def _rebuild(model_path: Path, threads: int, spin: bool) -> ort.InferenceSession:
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = threads
    opts.inter_op_num_threads = 1
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    if not spin:
        # Stop a finished pool from busy-waiting on cores the other session needs.
        opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        opts.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return ort.InferenceSession(str(model_path), sess_options=opts, providers=["CPUExecutionProvider"])


def pct(values: list[float]) -> dict[str, float]:
    arr = np.asarray(values, dtype=float)
    return {
        "p50": round(float(np.percentile(arr, 50, method="nearest")), 2),
        "p90": round(float(np.percentile(arr, 90, method="nearest")), 2),
        "p99": round(float(np.percentile(arr, 99, method="nearest")), 2),
        "p100": round(float(arr.max()), 2),
    }


def arm(name: str, pairs: list[tuple[str, list[str]]], *, with_embedder: bool,
        spin: bool, embed_threads: int, rerank_threads: int) -> dict[str, Any]:
    enc = build_reranker(rerank_threads, spin)
    emb: Embedder | None = None
    if with_embedder:
        emb = Embedder(ONNX_DIR / INT8_MODEL, ONNX_DIR / TOKENIZER_FILE, threads=embed_threads)
        if not spin:
            emb.session = _rebuild(ONNX_DIR / INT8_MODEL, embed_threads, spin=False)

    rerank_ms: list[float] = []
    embed_ms: list[float] = []
    for i, (q, ps) in enumerate(pairs):
        if emb is not None:
            t0 = time.perf_counter_ns()
            emb.encode([q], "query")
            e = (time.perf_counter_ns() - t0) / 1e6
        else:
            e = 0.0
        t1 = time.perf_counter_ns()
        enc.score(q, ps)
        r = (time.perf_counter_ns() - t1) / 1e6
        if i >= WARM:
            rerank_ms.append(r)
            embed_ms.append(e)

    row: dict[str, Any] = {
        "arm": name,
        "with_embedder": with_embedder,
        "spinning": spin,
        "embed_threads": embed_threads if with_embedder else None,
        "rerank_threads": rerank_threads,
        "n": len(rerank_ms),
        "rerank_depth3_ms": pct(rerank_ms),
    }
    if with_embedder:
        row["embed_ms"] = pct(embed_ms)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--threads", type=int, default=ONNX_THREADS_SERVING)
    ap.add_argument("--label", default="contention")
    ap.add_argument("--depth", type=int, default=3)
    args = ap.parse_args()

    global DEPTH
    DEPTH = args.depth
    pairs = load_pairs(args.n, args.seed)
    t = args.threads
    rows = [
        arm("A reranker alone", pairs, with_embedder=False, spin=True, embed_threads=t, rerank_threads=t),
        arm("B embedder+reranker, spinning", pairs, with_embedder=True, spin=True, embed_threads=t, rerank_threads=t),
        arm("C same, spinning off", pairs, with_embedder=True, spin=False, embed_threads=t, rerank_threads=t),
        arm("D same, embedder 1 thread", pairs, with_embedder=True, spin=True, embed_threads=1, rerank_threads=t),
        arm("E embedder 1 thread, no spin", pairs, with_embedder=True, spin=False, embed_threads=1, rerank_threads=t),
        arm("F embedder 2 threads", pairs, with_embedder=True, spin=True, embed_threads=2, rerank_threads=t),
    ]
    for r in rows:
        e = r.get("embed_ms")
        print(f"{r['arm']:<32} rerank d3 P50 {r['rerank_depth3_ms']['p50']:>7} "
              f"P90 {r['rerank_depth3_ms']['p90']:>7} P100 {r['rerank_depth3_ms']['p100']:>7}"
              + (f"   embed P50 {e['p50']:>6}" if e else ""), flush=True)

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "what": "does the embedder's ORT thread pool steal cores from the cross-encoder",
        "host": "deployed box",
        "depth": DEPTH,
        "n": args.n,
        "warmup": WARM,
        "rows": rows,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    path = RESULTS_DIR / f"{stamp}-ort-{args.label}.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
