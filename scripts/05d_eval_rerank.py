"""Phase 5: does the reranker actually lift Hit@1, and which reranker. J-style harness.

    python scripts/05d_eval_rerank.py
    python scripts/05d_eval_rerank.py --limit 100 --depths 20
    python scripts/05d_eval_rerank.py --no-latency

OWNED BY BENCH. Retrieval quality transfers across machines (Devices.md 2); the
latency section at the end does NOT and is tagged accordingly.

This answers the question Phase 5 exists for. ISSUES.md I2: dense-only Hit@1 is
0.356 en / 0.224 hi against Recall@10 0.878 - the right passage is nearly always
retrieved and is first only a third of the time, and the extractive path returns
the first one. Assumption A6 ("extractive answers are good enough to be the
default") rests entirely on a cross-encoder fixing that. D2's reversal condition
says that if it does not, extractive becomes a "fast mode" toggle rather than the
default. So this is a gate on the architecture, not a tuning run.

It also settles the model choice. Rules.md 3.3 marks the reranker SOFT - benchmark
before deviating - and its default, ms-marco-MiniLM-L-6-v2, is English-only while
half this corpus is Hindi.

Method, inherited wholesale from J15 (scripts/05c_compare_strategies.py) because
ISSUES.md I21 and I22 were both measurement artifacts rather than real findings.
Everything that could differ between the arms is hoisted out of the loop and
computed exactly once:

  the query list         - frozen, filtered once, identical order for every arm
  the query VECTORS      - embedded once, so no arm is scored against a different
                           encoding of the same question
  the CANDIDATE LISTS    - retrieved once at max depth and deduplicated to distinct
                           passages, so every arm reranks the identical input
  gold-id logic, k       - one code path

The baseline is therefore the same candidate list in dense order, which makes every
delta a genuine paired difference: same query, same candidates, only the ordering
function changes. Deltas are reported with a paired bootstrap CI, per ISSUES.md I23.

One thing this deliberately does NOT measure: Recall@10 below depth 10. Reranking
reorders a fixed candidate set, so at depth 10 it cannot add a passage that dense
retrieval missed - recall is bounded by the pool and only Hit@1/MRR/nDCG move. That
is the point: retrieval is already good (I2), ranking is what is broken.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _compare import paired_bootstrap, per_query_metrics  # noqa: E402
from _progress import Progress  # noqa: E402

from rag_core.config import (  # noqa: E402
    INT8_MODEL,
    ONNX_DIR,
    ONNX_THREADS_SERVING,
    PASSAGES_PARQUET,
    QUERIES_PARQUET,
    RERANK_MODEL_FILE,
    RERANK_TOKENIZER_FILE,
    RERANKERS,
    RESULTS_DIR,
    TOKENIZER_FILE,
    load_env,
)
from rag_core.retrieval.dense import DenseIndex  # noqa: E402
from rag_core.retrieval.embedder import Embedder  # noqa: E402
from rag_core.retrieval.rerank import CrossEncoder  # noqa: E402

load_env()

K = 10
DEFAULT_QUERIES = 300
DEFAULT_DEPTHS = (10, 20, 50)
STRATEGY = "c1"  # the Phase 3 default; the reranker is evaluated on top of it

# Chunks pulled before deduplicating to distinct passages. Must exceed
# max(depths) * chunks-per-passage so the deepest arm is not starved.
DEDUP_POOL_MULT = 4

LATENCY_QUERIES = 60
LATENCY_WARMUP = 10


def load_queries(limit: int) -> list[dict]:
    """Frozen and filtered ONCE, so every arm sees identical array lengths."""
    rows = [q for q in pq.read_table(QUERIES_PARQUET).to_pylist() if q["split"] == "dev"]
    rows = [q for q in rows if q["gold_en_ids"] and q["gold_hi_ids"]]
    return rows[:limit]


def build_candidates(
    queries: list[dict], max_depth: int
) -> tuple[dict[str, list[list[str]]], dict[str, str]]:
    """Retrieve once, at max depth, for both languages. Every arm reranks THIS.

    Returns (candidate passage ids per language per query, passage text by id).
    """
    embedder = Embedder(
        ONNX_DIR / INT8_MODEL, ONNX_DIR / TOKENIZER_FILE, threads=ONNX_THREADS_SERVING
    )
    index = DenseIndex(STRATEGY)
    index.load()

    text_by_id = {
        p["passage_id"]: p["text"]
        for p in pq.read_table(
            PASSAGES_PARQUET, columns=["passage_id", "text"]
        ).to_pylist()
    }

    out: dict[str, list[list[str]]] = {}
    for lang in ("en", "hi"):
        qfield = "query_en" if lang == "en" else "query_hi"
        prog = Progress(len(queries), f"retrieve {lang}")
        lists: list[list[str]] = []
        t0 = time.perf_counter()
        for i, q in enumerate(queries):
            vec = embedder.encode_one(q[qfield], "query")
            seen: set[str] = set()
            distinct: list[str] = []
            for row, _ in index.search(vec, max_depth * DEDUP_POOL_MULT):
                pid = index.chunk(row)["passage_id"]
                if pid not in seen:
                    seen.add(pid)
                    distinct.append(pid)
                if len(distinct) >= max_depth:
                    break
            lists.append(distinct)
            prog.report(i + 1, time.perf_counter() - t0)
        out[lang] = lists
    return out, text_by_id


def score_arm(
    ranked: dict[str, list[list[str]]], queries: list[dict]
) -> dict[str, dict[str, np.ndarray]]:
    res = {}
    for lang in ("en", "hi"):
        gold_field = "gold_en_ids" if lang == "en" else "gold_hi_ids"
        gold = [set(q[gold_field]) for q in queries]
        res[lang] = per_query_metrics(ranked[lang], gold, k=K)
    return res


def rerank_arm(
    ce: CrossEncoder,
    queries: list[dict],
    cands: dict[str, list[list[str]]],
    text_by_id: dict[str, str],
    depth: int,
    label: str,
) -> dict[str, list[list[str]]]:
    out: dict[str, list[list[str]]] = {}
    for lang in ("en", "hi"):
        qfield = "query_en" if lang == "en" else "query_hi"
        prog = Progress(len(queries), f"{label} d{depth} {lang}")
        ranked: list[list[str]] = []
        t0 = time.perf_counter()
        for i, q in enumerate(queries):
            pool = cands[lang][i][:depth]
            pairs = [(pid, text_by_id[pid]) for pid in pool]
            reordered, _ = ce.rerank(q[qfield], pairs)
            ranked.append([pid for pid, _ in reordered][:K])
            prog.report(i + 1, time.perf_counter() - t0)
        out[lang] = ranked
    return out


def measure_latency(
    ce: CrossEncoder, queries: list[dict], cands: dict[str, list[list[str]]],
    text_by_id: dict[str, str], depth: int,
) -> dict[str, float]:
    """Per-call rerank latency at the SERVING thread count, one query at a time.

    Devices.md 2: this number does not transfer off BENCH and is never published
    as a product figure - Latency.md 6 requires those come from the deployed box.
    It is here to pick a depth off the accuracy-vs-latency curve, which is a
    relative comparison and survives being local.
    """
    samples: list[float] = []
    for i, q in enumerate(queries[: LATENCY_QUERIES + LATENCY_WARMUP]):
        pool = cands["en"][i][:depth]
        pairs = [(pid, text_by_id[pid]) for pid in pool]
        t0 = time.perf_counter()
        ce.rerank(q["query_en"], pairs)
        elapsed = (time.perf_counter() - t0) * 1000.0
        if i >= LATENCY_WARMUP:  # cold ONNX inflates the first calls
            samples.append(elapsed)
    arr = np.array(samples)
    return {
        "p50": float(np.percentile(arr, 50, method="nearest")),
        "p90": float(np.percentile(arr, 90, method="nearest")),
        "p99": float(np.percentile(arr, 99, method="nearest")),
        "p100": float(arr.max()),
        "n": len(arr),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=DEFAULT_QUERIES)
    ap.add_argument("--models", default="all", help="comma list of config.RERANKERS keys")
    ap.add_argument("--depths", default=",".join(str(d) for d in DEFAULT_DEPTHS))
    ap.add_argument("--no-latency", action="store_true")
    args = ap.parse_args()

    keys = list(RERANKERS) if args.models == "all" else args.models.split(",")
    depths = [int(d) for d in args.depths.split(",")]
    max_depth = max(depths)

    queries = load_queries(args.limit)
    print("")
    print(f"  {len(queries)} dev queries, strategy {STRATEGY}, candidate depth {max_depth}")
    print(f"  models {keys}   depths {depths}")
    print("")

    cands, text_by_id = build_candidates(queries, max_depth)

    # Baseline: the identical candidate list, in dense order. Same queries, same
    # candidates, only the ordering function differs - so every delta is paired.
    baseline_ranked = {lang: [c[:K] for c in cands[lang]] for lang in ("en", "hi")}
    arms: dict[str, dict[str, dict[str, np.ndarray]]] = {
        "dense (no rerank)": score_arm(baseline_ranked, queries)
    }
    latency: dict[str, dict[str, float]] = {}

    for key in keys:
        sub = ONNX_DIR / f"rerank-{key}"
        model = sub / RERANK_MODEL_FILE
        if not model.exists():
            print(f"  SKIP {key}: {model} missing, run scripts/03b_export_reranker.py")
            continue
        ce = CrossEncoder(model, sub / RERANK_TOKENIZER_FILE, threads=ONNX_THREADS_SERVING)
        for depth in depths:
            label = f"{key} d{depth}"
            ranked = rerank_arm(ce, queries, cands, text_by_id, depth, key)
            arms[label] = score_arm(ranked, queries)
            if not args.no_latency:
                latency[label] = measure_latency(ce, queries, cands, text_by_id, depth)

    # -- report -------------------------------------------------------------
    base = arms["dense (no rerank)"]
    print("")
    print(f"  {'arm':<22}{'en Hit@1':>10}{'hi Hit@1':>10}{'en MRR':>9}{'hi MRR':>9}{'en nDCG':>10}")
    for label, m in arms.items():
        print(
            f"  {label:<22}{m['en']['hit1'].mean():>10.3f}{m['hi']['hit1'].mean():>10.3f}"
            f"{m['en']['mrr10'].mean():>9.3f}{m['hi']['mrr10'].mean():>9.3f}"
            f"{m['en']['ndcg10'].mean():>10.3f}"
        )

    print("")
    print(f"  paired Hit@1 delta vs dense baseline, same queries, 4000 resamples")
    print(f"  {'arm':<22}{'en delta':>22}{'hi delta':>22}")
    deltas: dict[str, dict[str, list[float]]] = {}
    for label, m in arms.items():
        if label == "dense (no rerank)":
            continue
        row = {}
        cells = []
        for lang in ("en", "hi"):
            mean, lo, hi = paired_bootstrap(m[lang]["hit1"], base[lang]["hit1"])
            row[lang] = [mean, lo, hi]
            sig = " " if lo <= 0.0 <= hi else "*"
            cells.append(f"{mean:+.3f} [{lo:+.3f},{hi:+.3f}]{sig}")
        deltas[label] = row
        print(f"  {label:<22}{cells[0]:>22}{cells[1]:>22}")
    print("      * = 95% CI excludes zero")

    if latency:
        print("")
        print(f"  rerank stage latency, BENCH only, {ONNX_THREADS_SERVING} threads, en, single query")
        print(f"  {'arm':<22}{'P50':>8}{'P90':>8}{'P99':>8}{'P100':>8}   vs 60ms budget")
        for label, s in latency.items():
            verdict = "OK" if s["p99"] < 60.0 else "OVER BUDGET"
            print(
                f"  {label:<22}{s['p50']:>8.1f}{s['p90']:>8.1f}{s['p99']:>8.1f}"
                f"{s['p100']:>8.1f}   {verdict}"
            )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    out = RESULTS_DIR / f"{stamp}-rerank-phase5.json"
    out.write_text(
        json.dumps(
            {
                "generated_utc": datetime.now(timezone.utc).isoformat(),
                "queries": len(queries),
                "strategy": STRATEGY,
                "depths": depths,
                "k": K,
                "metrics": {
                    label: {
                        lang: {k: float(v.mean()) for k, v in m[lang].items()}
                        for lang in ("en", "hi")
                    }
                    for label, m in arms.items()
                },
                "paired_hit1_delta_vs_dense": deltas,
                "latency_bench_only": latency,
                "per_query_hit1": {
                    label: {lang: m[lang]["hit1"].tolist() for lang in ("en", "hi")}
                    for label, m in arms.items()
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("")
    print(f"  wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
