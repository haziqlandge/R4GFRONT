"""J15: the Phase 3 strategy comparison. One process, one query set, all strategies.

    python scripts/05c_compare_strategies.py
    python scripts/05c_compare_strategies.py --strategies c1,c8 --limit 100
    python scripts/05c_compare_strategies.py --dry-run

OWNED BY BENCH.

This exists because of ISSUES.md I21. The Phase 3 comparison had been assembled
from separately-dated eval JSONs, and those runs had used different --limit
values, so the table compared sample sizes rather than strategies: c1 at 250
queries scored 0.896 and c8 at 500 scored 0.870, which read as a real difference
and was not. On identical settings both score 0.870.

The fix is structural, not a re-run. Everything that could differ between
strategies is hoisted out of the per-strategy loop and computed exactly once:

  the query list          - frozen, filtered once, identical order for everyone
  the embedder            - one instance, one thread count
  the query VECTORS       - embedded once and reused, so no strategy is scored
                            against a different encoding of the same question
  k, language handling,
  gold-id logic           - one code path

A strategy therefore cannot be evaluated under different conditions than its
neighbours, because there are no per-strategy conditions left to get wrong.

Output carries per-query arrays, not just means, so deltas are genuinely paired
(same query minus itself) rather than two independent samples differenced.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _compare import paired_bootstrap, per_query_metrics  # noqa: E402
from _progress import Progress  # noqa: E402

from rag_core.chunking import registry  # noqa: E402
from rag_core.config import (  # noqa: E402
    INDEX_DIR,
    INT8_MODEL,
    ONNX_DIR,
    ONNX_THREADS_BUILD,
    QUERIES_PARQUET,
    RESULTS_DIR,
    TOKENIZER_FILE,
)
from rag_core.retrieval.dense import DenseIndex  # noqa: E402
from rag_core.retrieval.embedder import Embedder  # noqa: E402

K = 10
DEFAULT_QUERIES = 500
BASELINE = "c1"

# Serving-footprint estimate for the Devices.md 6 RAM filter: index.bin plus the
# chunk store is what must be resident alongside the models.
MODELS_MB = 175.0   # int8 embedder + reranker
RUNTIME_MB = 200.0  # python, fastapi, numpy


def available_strategies() -> list[str]:
    return [s for s in registry.ALL_STRATEGIES if (INDEX_DIR / s / "index.bin").exists()]


def load_queries(limit: int) -> list[dict]:
    """Frozen, filtered ONCE. Every strategy sees this exact list in this order.

    A query with no gold cannot be scored, and it is removed here rather than
    inside the scorer so that all strategies get identical array lengths and
    pairing holds.
    """
    rows = [q for q in pq.read_table(QUERIES_PARQUET).to_pylist() if q["split"] == "dev"]
    rows = [q for q in rows if q["gold_en_ids"] and q["gold_hi_ids"]]
    return rows[:limit]


def index_costs(strategy: str) -> dict[str, float]:
    """Machine-invariant cost columns per Devices.md 3. Wall-clock is deliberately
    NOT a comparison column: across three machines it compares hardware."""
    d = INDEX_DIR / strategy
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    index_mb = os.path.getsize(d / "index.bin") / 1_048_576
    chunks_mb = os.path.getsize(d / "chunks.parquet") / 1_048_576
    counts = meta.get("counts", {})
    return {
        "passages": float(counts.get("passages", 0)),
        "chunks": float(counts.get("chunks", 0)),
        "tokens_embedded": float(counts.get("tokens_embedded", 0)),
        "index_mb": round(index_mb, 1),
        "serving_mb": round(index_mb + chunks_mb + MODELS_MB + RUNTIME_MB, 1),
    }


# Chunks retrieved before deduplicating to distinct passages. Must exceed
# K * max(chunks per passage) so no strategy is starved.
DEDUP_POOL = 60


def evaluate(
    strategy: str,
    qvecs: dict[str, np.ndarray],
    queries: list[dict],
    dedup: bool = True,
) -> dict[str, dict[str, np.ndarray]]:
    """Score one strategy using PRE-COMPUTED query vectors.

    `dedup` collapses retrieved CHUNKS to distinct PASSAGES before scoring, and
    it is on by default because without it the comparison is biased by chunk
    granularity rather than by retrieval quality.

    Chunks per passage differs 2.4x across these strategies (C1 1.28, C2 3.13).
    At a fixed k=10 chunks, C2 can only ever surface about 3 distinct passages
    where C1 surfaces about 8 - so C2 loses on a structural artifact of how
    finely it cuts text, not on whether it finds the right passage. Gold is
    defined over passages, the answer is shown as a passage, and a real pipeline
    deduplicates before reranking, so passages are the honest unit.
    """
    index = DenseIndex(strategy)
    index.load()
    out: dict[str, dict[str, np.ndarray]] = {}
    for lang in ("en", "hi"):
        gold_field = "gold_en_ids" if lang == "en" else "gold_hi_ids"
        ranked = []
        for i in range(len(queries)):
            rows = index.search(qvecs[lang][i], DEDUP_POOL if dedup else K)
            pids = [index.chunk(r)["passage_id"] for r, _ in rows]
            if dedup:
                seen: set[str] = set()
                distinct = []
                for pid in pids:
                    if pid not in seen:
                        seen.add(pid)
                        distinct.append(pid)
                    if len(distinct) >= K:
                        break
                pids = distinct
            ranked.append(pids[:K])
        gold = [set(q[gold_field]) for q in queries]
        out[lang] = per_query_metrics(ranked, gold, k=K)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strategies", default="", help="comma list; default = all built")
    ap.add_argument("--limit", type=int, default=DEFAULT_QUERIES)
    ap.add_argument("--threads", type=int, default=ONNX_THREADS_BUILD)
    ap.add_argument("--dry-run", action="store_true", help="print inputs and exit")
    ap.add_argument("--no-dedup", action="store_true",
                    help="score raw top-K chunks; biased toward coarse chunkers")
    args = ap.parse_args()

    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    if not strategies:
        strategies = available_strategies()
    missing = [s for s in strategies if not (INDEX_DIR / s / "index.bin").exists()]
    if missing:
        raise SystemExit(f"no index built for: {', '.join(missing)}")

    queries = load_queries(args.limit)

    print("")
    print("  INPUTS (identical for every strategy below)")
    print(f"    queries          {len(queries)}   dev split, gold present in both languages")
    print(f"    k                {K}")
    print(f"    embedder         {INT8_MODEL}, threads {args.threads}")
    print("    languages        en, hi    gold: gold_en_ids / gold_hi_ids")
    print(f"    scoring unit     {'distinct passages (deduped from top-60 chunks)' if not args.no_dedup else 'raw top-K chunks'}")
    print("")
    print("  INDEXES UNDER TEST")
    print(f"    {'strategy':<10}{'passages':>10}{'chunks':>11}{'index MB':>10}{'serving MB':>12}")
    costs: dict[str, dict[str, float]] = {}
    for s in strategies:
        c = index_costs(s)
        costs[s] = c
        print(f"    {s:<10}{int(c['passages']):>10,}{int(c['chunks']):>11,}"
              f"{c['index_mb']:>10.0f}{c['serving_mb']:>12.0f}")
    if args.dry_run:
        return 0

    embedder = Embedder(ONNX_DIR / INT8_MODEL, ONNX_DIR / TOKENIZER_FILE, threads=args.threads)
    print("")
    print("  embedding queries once, reused by every strategy")
    qvecs = {
        "en": np.vstack([embedder.encode_one(q["query_en"], "query") for q in queries]),
        "hi": np.vstack([embedder.encode_one(q["query_hi"], "query") for q in queries]),
    }

    results: dict[str, dict[str, dict[str, np.ndarray]]] = {}
    started = time.perf_counter()
    progress = Progress(total=len(strategies), label="eval", min_interval_s=0.0)
    for i, s in enumerate(strategies, 1):
        results[s] = evaluate(s, qvecs, queries, dedup=not args.no_dedup)
        progress.report(i, time.perf_counter() - started, extra={"done": s})

    print("")
    print(f"  RESULTS   {len(queries)} queries, k={K}")
    print("")
    print(f"  {'strategy':<10}{'lang':<5}{'Recall@10':>11}{'MRR@10':>9}{'nDCG@10':>9}{'Hit@1':>8}")
    for s in strategies:
        for lang in ("en", "hi"):
            m = results[s][lang]
            print(f"  {s:<10}{lang:<5}{m['recall10'].mean():>11.3f}{m['mrr10'].mean():>9.3f}"
                  f"{m['ndcg10'].mean():>9.3f}{m['hit1'].mean():>8.3f}")

    if BASELINE in results:
        print("")
        print(f"  PAIRED DELTAS vs {BASELINE}   same queries, 4000 bootstrap resamples")
        print("")
        print(f"  {'strategy':<10}{'lang':<5}{'dRecall@10':>12}{'95% CI':>24}   significant")
        for s in strategies:
            if s == BASELINE:
                continue
            for lang in ("en", "hi"):
                mean, lo, hi = paired_bootstrap(
                    results[s][lang]["recall10"], results[BASELINE][lang]["recall10"]
                )
                sig = "YES" if (lo > 0 or hi < 0) else "no"
                print(f"  {s:<10}{lang:<5}{mean:>+12.4f}   [{lo:>+7.4f}, {hi:>+7.4f}]   {sig}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    out = RESULTS_DIR / f"{stamp}-comparison-j15.json"
    payload = {
        "job": "J15",
        "queries": len(queries),
        "k": K,
        "split": "dev",
        "embedder": INT8_MODEL,
        "scoring_unit": "raw_chunks" if args.no_dedup else "distinct_passages",
        "note": (
            "single process, one query set, query vectors embedded once and shared; "
            "per-query arrays retained so deltas are genuinely paired; chunks "
            "deduplicated to distinct passages so strategies with different "
            "chunks-per-passage are compared fairly"
        ),
        "strategies": {
            s: {
                "costs": costs[s],
                "metrics": {
                    lang: {k: float(v.mean()) for k, v in results[s][lang].items()}
                    for lang in ("en", "hi")
                },
                "per_query": {
                    lang: {k: v.tolist() for k, v in results[s][lang].items()}
                    for lang in ("en", "hi")
                },
            }
            for s in strategies
        },
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("")
    print(f"  wrote {out.name}   includes per-query arrays for re-analysis")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
