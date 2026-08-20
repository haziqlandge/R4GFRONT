"""Cross-encoder cost sweep, run ON the deployed box. Phase 7.

`Latency.md` 8 lists thread pinning and sequence truncation as levers 4 and 6 and
says to measure rather than guess. Rerank is ~88% of Band A on the deployed
n2-standard-4, so these two dials are the only ones large enough to matter
without buying a bigger machine.

This isolates the reranker: no HTTP, no index, no uvicorn. It scores real
(query, passage) pairs drawn from the frozen artifacts at each thread count and
each truncation length, and reports the per-pair distribution plus what a
depth-`k` rerank would cost.

    python scripts/07b_rerank_sweep.py --pairs 150 --threads 1,2,3,4 --tokens 256

Percentiles are nearest rank, matching Latency.md 6. The first 20 pairs of every
configuration are discarded: a fresh ORT session is cold and including it would
make whichever configuration ran first look worst.
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

ROOT: Final[Path] = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from rag_core.config import (  # noqa: E402
    ARTIFACTS_DIR,
    ONNX_DIR,
    RERANK_MODEL_FILE,
    RERANK_TOKENIZER_FILE,
    RERANKER,
    RESULTS_DIR,
)
from rag_core.retrieval.rerank import CrossEncoder  # noqa: E402

WARM: Final[int] = 20


def load_pairs(n_pairs: int, seed: int) -> list[tuple[str, str]]:
    """Real queries against real passages, at the real length distribution."""
    import pyarrow.parquet as pq

    queries = [
        json.loads(line)
        for line in (ROOT / "bench" / "queries_250.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    tbl = pq.read_table(ARTIFACTS_DIR / "passages.parquet", columns=["text"])
    texts = tbl.column("text").to_pylist()
    del tbl

    rng = random.Random(seed)
    pairs: list[tuple[str, str]] = []
    while len(pairs) < n_pairs:
        q = rng.choice(queries)
        field = "query_en" if len(pairs) % 2 == 0 else "query_hi"
        query = q.get(field) or q["query_en"]
        pairs.append((str(query), str(rng.choice(texts))))
    return pairs


def pct(values: list[float]) -> dict[str, float]:
    arr = np.asarray(values, dtype=float)
    return {
        "p50": round(float(np.percentile(arr, 50, method="nearest")), 2),
        "p90": round(float(np.percentile(arr, 90, method="nearest")), 2),
        "p99": round(float(np.percentile(arr, 99, method="nearest")), 2),
        "p100": round(float(arr.max()), 2),
        "mean": round(float(arr.mean()), 2),
    }


def measure(pairs: list[tuple[str, str]], threads: int, max_tokens: int) -> dict[str, Any]:
    rerank_dir = ONNX_DIR / f"rerank-{RERANKER}"
    enc = CrossEncoder(
        model_path=rerank_dir / RERANK_MODEL_FILE,
        tokenizer_path=rerank_dir / RERANK_TOKENIZER_FILE,
        threads=threads,
        max_tokens=max_tokens,
    )
    per_pair: list[float] = []
    tok_lens: list[int] = []
    for i, (q, p) in enumerate(pairs):
        t0 = time.perf_counter_ns()
        enc.score(q, [p])
        ms = (time.perf_counter_ns() - t0) / 1e6
        if i >= WARM:
            per_pair.append(ms)
            tok_lens.append(len(enc.tokenizer.encode(q, p).ids))
    d = pct(per_pair)
    d["n"] = len(per_pair)
    d["threads"] = threads
    d["max_tokens"] = max_tokens
    d["token_len_p50"] = int(np.percentile(tok_lens, 50, method="nearest"))
    d["token_len_p100"] = int(max(tok_lens))
    d["truncated_share"] = round(sum(1 for t in tok_lens if t >= max_tokens) / len(tok_lens), 3)
    # What a depth-k rerank costs if pairs are independent draws from this
    # distribution. p50 of the SUM is not the sum of the p50s, so this is
    # simulated rather than multiplied.
    rng = np.random.default_rng(0)
    arr = np.asarray(per_pair)
    for k in (3, 5):
        sums = rng.choice(arr, size=(4000, k), replace=True).sum(axis=1)
        d[f"depth{k}_ms"] = {
            "p50": round(float(np.percentile(sums, 50, method="nearest")), 2),
            "p90": round(float(np.percentile(sums, 90, method="nearest")), 2),
            "p99": round(float(np.percentile(sums, 99, method="nearest")), 2),
            "p100": round(float(sums.max()), 2),
        }
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pairs", type=int, default=150)
    ap.add_argument("--threads", default="1,2,3,4")
    ap.add_argument("--tokens", default="256")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--label", default="sweep")
    args = ap.parse_args()

    pairs = load_pairs(args.pairs, args.seed)
    thread_list = [int(x) for x in args.threads.split(",")]
    token_list = [int(x) for x in args.tokens.split(",")]

    rows: list[dict[str, Any]] = []
    for mt in token_list:
        for th in thread_list:
            row = measure(pairs, th, mt)
            rows.append(row)
            print(
                f"threads {th}  max_tokens {mt:>3}  "
                f"pair P50 {row['p50']:>6} P90 {row['p90']:>6} P100 {row['p100']:>7}  "
                f"depth3 P50 {row['depth3_ms']['p50']:>6} P99 {row['depth3_ms']['p99']:>7}  "
                f"trunc {row['truncated_share']:.2f}",
                flush=True,
            )

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "what": "cross-encoder per-pair cost by ONNX thread count and truncation length",
        "host": "deployed box",
        "note": "the live service was up during this sweep; it was idle apart from health polls",
        "pairs": args.pairs,
        "warmup_pairs": WARM,
        "rows": rows,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    path = RESULTS_DIR / f"{stamp}-rerank-sweep-{args.label}.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
