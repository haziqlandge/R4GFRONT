"""Band B: the generative fallback path, measured end to end. Phase 5.

    python scripts/04b_bench_bandb.py
    python scripts/04b_bench_bandb.py --runs 30 --lang hi

Latency.md 1 defines Band B as Band A plus the Groq generation call. It is
reported SEPARATELY and it is expected to be over budget - Rules.md 1 makes
publishing both sides of the measurement boundary a HARD rule, and D6 chose an
honest 340 ms over a fabricated 190 ms.

WHY THIS IS A SEPARATE SCRIPT rather than a flag on 04_bench_latency.py:
    Band A is deterministic, in-process, and repeatable to a tenth of a
    millisecond. Band B is a network call to a shared free tier whose latency is
    somebody else's queue depth. Mixing them into one harness would invite the two
    to be averaged, or a Band A regression to be blamed on Groq. They are different
    measurements with different error bars and they get different files.

SAMPLE SIZE IS CAPPED AND THE CAP IS THE FINDING:
    ISSUES.md I7 measured Groq's free tier at 12,000 tokens per window. A full
    250-query run needs roughly 250k tokens and will be throttled hard, so the
    default here is 40 queries and the methodology says so rather than discovering
    the throttle mid-run. Assumption A10 is already recorded as FALSE as stated for
    exactly this reason.

    A 429 partway through is not a failed benchmark - it is the circuit breaker
    doing its job, and it is reported as a path outcome rather than swallowed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rag_core.config import (  # noqa: E402
    BENCH_DIR,
    BUDGET_MS,
    DEFAULT_STRATEGY,
    GROQ_MODEL,
    INT8_MODEL,
    ONNX_DIR,
    ONNX_THREADS_SERVING,
    PERCENTILE_METHOD,
    PERCENTILES,
    RERANK_MODEL_FILE,
    RERANK_TOKENIZER_FILE,
    RERANKER,
    RESULTS_DIR,
    TOKENIZER_FILE,
    load_env,
)
from rag_core import config  # noqa: E402
from rag_core.answering.generative import GroqClient  # noqa: E402
from rag_core.harness.pipeline import Context  # noqa: E402
from rag_core.harness.stages import Runtime, build_pipeline  # noqa: E402
from rag_core.harness.trace import Trace  # noqa: E402
from rag_core.retrieval.dense import DenseIndex  # noqa: E402
from rag_core.retrieval.embedder import Embedder  # noqa: E402
from rag_core.retrieval.rerank import CrossEncoder  # noqa: E402

load_env()

# Deliberately small. See the module docstring and ISSUES.md I7.
DEFAULT_RUNS = 40
WARMUP = 2  # enough to warm ONNX and open the TLS connection, no more - each
            # warmup run spends real tokens from a 12,000-token window.


async def build(strategy: str) -> tuple[object, GroqClient]:
    embedder = Embedder(
        ONNX_DIR / INT8_MODEL, ONNX_DIR / TOKENIZER_FILE, threads=ONNX_THREADS_SERVING
    )
    index = DenseIndex(strategy)
    index.load()
    d = ONNX_DIR / f"rerank-{RERANKER}"
    reranker = CrossEncoder(
        d / RERANK_MODEL_FILE, d / RERANK_TOKENIZER_FILE, threads=ONNX_THREADS_SERVING
    )
    groq = GroqClient()
    if not groq.configured:
        raise SystemExit(
            "GROQ_API_KEY is not set. Band B measures the generative path; "
            "without a key there is nothing to measure."
        )
    await groq.start()

    rt = Runtime(embedder, index, reranker=reranker, groq=groq)
    if not rt.load_passage_store():
        rt.build_passage_map(index.chunks)
    return build_pipeline(rt), groq


def load_queries(lang: str, n: int) -> list[str]:
    field = "query_en" if lang == "en" else "query_hi"
    path = BENCH_DIR / "queries_250.jsonl"
    with path.open(encoding="utf-8") as fh:
        rows = [json.loads(line)[field] for line in fh if line.strip()]
    return rows[:n]


async def main_async(args: argparse.Namespace) -> int:
    pipeline, groq = await build(args.strategy)
    queries = load_queries(args.lang, args.runs + WARMUP)

    # Band B is DEFINED as Band A plus the generation call, so the generation call
    # has to happen. The calibrated thresholds send only ~10% of queries down that
    # path (config.ROUTE_TAU_HIGH), which is correct for production and useless for
    # this measurement - it would report Band A with a Groq-shaped rounding error.
    #
    # Raising tau_high forces every non-abstaining query through generation. This is
    # legitimate precisely because it is not silent: the band is labelled B, the
    # override is recorded in the results file, and Latency.md 1 already defines the
    # two bands as different measurements rather than comparable ones. Abstention is
    # deliberately left intact - a query the system would refuse is not a query the
    # generative path should be timed on.
    original_tau_high = config.ROUTE_TAU_HIGH
    config.ROUTE_TAU_HIGH = float("inf")  # type: ignore[misc]

    print("")
    print(f"  Band B: Band A + Groq generation ({GROQ_MODEL})")
    print(f"  {args.runs} queries ({args.lang}), {WARMUP} warmup discarded")
    print(f"  ISSUES.md I7: the free tier is 12,000 tokens/window, so this sample")
    print(f"  is deliberately small and the size is part of the methodology.")
    print("")

    samples: list[float] = []
    paths: dict[str, int] = {}
    breaker_opened_at: int | None = None

    for i, q in enumerate(queries):
        trace = Trace(budget_ms=BUDGET_MS)
        t0 = time.perf_counter()
        ctx = await pipeline.run(Context(query=q, trace=trace))
        elapsed = (time.perf_counter() - t0) * 1000.0

        decision = ctx.data.get("route")
        # ABSTAIN has two distinct causes and reporting them as one number is
        # misleading in a published benchmark: LOW_CONFIDENCE means the reranker
        # score fell under the floor and no network call was made, while
        # UNGROUNDED_OUTPUT means the model was called, read the passages, and
        # declined. The first is a retrieval verdict, the second is a groundedness
        # verdict, and only the second costs a Groq round trip.
        if decision is None:
            path = "UNKNOWN"
        elif decision.decision == "ABSTAIN":
            path = f"ABSTAIN:{decision.abstain_reason or 'UNSPECIFIED'}"
        else:
            path = decision.decision
        if i >= WARMUP:
            samples.append(elapsed)
            paths[path] = paths.get(path, 0) + 1

        if groq.breaker.state != "closed" and breaker_opened_at is None:
            breaker_opened_at = i
            print(f"  breaker opened at query {i} - state {groq.breaker.state}")
            print("  This is the rate limit being handled in code (Rules.md 4),")
            print("  not a benchmark failure. Remaining queries route extractive.")

    await groq.close()
    config.ROUTE_TAU_HIGH = original_tau_high  # type: ignore[misc]

    if not samples:
        print("  no measured samples - every query abstained or the run was empty.")
        return 1

    arr = np.array(samples)
    summary = {
        f"p{p}": float(np.percentile(arr, p, method=PERCENTILE_METHOD))
        for p in PERCENTILES
    }
    summary["mean"] = float(arr.mean())

    print("")
    print(f"  {'P50':>9}{'P70':>9}{'P90':>9}{'P99':>9}{'P100':>9}")
    print(
        f"  {summary['p50']:>9.1f}{summary['p70']:>9.1f}{summary['p90']:>9.1f}"
        f"{summary['p99']:>9.1f}{summary['p100']:>9.1f}   ms"
    )
    print("")
    print("  path distribution over the measured runs:")
    for k, v in sorted(paths.items()):
        print(f"    {k:<12}{v:>5}  {v / len(samples):>6.1%}")

    over = summary["p50"] > BUDGET_MS
    print("")
    print(
        f"  Band B P50 is {summary['p50']:.0f} ms against a {BUDGET_MS:.0f} ms budget"
        f" - {'OVER, as designed and as published' if over else 'inside budget'}."
    )
    print("  Latency.md 3: this path is reported, not hidden. The extractive path")
    print("  is the one that meets the target.")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    out = RESULTS_DIR / f"{stamp}-bandb-{args.lang}.json"
    out.write_text(
        json.dumps(
            {
                "generated_utc": datetime.now(timezone.utc).isoformat(),
                "band": "B",
                "model": GROQ_MODEL,
                "reranker": RERANKER,
                "strategy": args.strategy,
                "language": args.lang,
                "runs": len(samples),
                "warmup": WARMUP,
                "forced_generative": True,
                "production_tau_high": original_tau_high,
                "summary_ms": summary,
                "path_distribution": paths,
                "breaker_opened_at_query": breaker_opened_at,
                "samples_ms": samples,
                "note": (
                    "Sample size is capped by Groq's 12,000-token free-tier window "
                    "(ISSUES.md I7, assumption A10). Not comparable to Band A, which "
                    "is in-process and deterministic."
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("")
    print(f"  wrote {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    ap.add_argument("--lang", default="en", choices=["en", "hi"])
    ap.add_argument("--strategy", default=DEFAULT_STRATEGY)
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
