"""Latency benchmark harness. Phase 0.

Built before any product code, because every architectural decision from Phase 2
onward is downstream of the numbers this produces. Latency.md section 6 fixes the
methodology; this file implements it and nothing else.

    python scripts/04_bench_latency.py --stub
    python scripts/04_bench_latency.py --stub --breakdown --runs 500

Methodology (Latency.md section 6):
  - 30 warmup runs discarded before measurement starts
  - time.perf_counter_ns(), monotonic, captured inside the process
  - numpy.percentile with method="nearest"; P100 is the true maximum
  - every run writes a dated immutable JSON to bench/results/, never overwritten
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Final

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.config import (  # noqa: E402
    BUDGET_MS,
    PERCENTILE_METHOD,
    PERCENTILES,
    RESULTS_DIR,
    WARMUP_RUNS,
)
from rag_core.harness.trace import Trace, span  # noqa: E402

# ---------------------------------------------------------------------------
# Stub pipeline
# ---------------------------------------------------------------------------

# Midpoints of the "Expected" column in Latency.md section 4. The stub exists to
# validate the rig against a known answer: if these sum to 72.5ms and the harness
# reports a P50 far from 72.5ms, the harness is measuring itself, not the pipeline.
STUB_STAGE_MS: Final[dict[str, float]] = {
    "input_guard": 5.5,
    "embed_query": 10.0,
    "dense_search": 5.0,
    "lexical_search": 3.0,
    "fuse": 0.5,
    "rerank": 32.0,
    "route": 0.5,
    "answer_extractive": 4.0,
    "output_guard": 10.0,
    "serialize": 2.0,
}

STUB_EXPECTED_MS: Final[float] = sum(STUB_STAGE_MS.values())


def _spin_ms(ms: float) -> None:
    """Burn `ms` milliseconds of CPU.

    Deliberately not asyncio.sleep. Two reasons:
      1. Windows timer granularity is ~15.6ms, so asyncio.sleep(0.005) sleeps for
         15ms and every sub-15ms stage in the stub would be indistinguishable.
      2. The real Band A stages are CPU-bound in-process work (ONNX inference,
         HNSW traversal), not awaits. A spin is the honest simulation.
    """
    deadline = time.perf_counter_ns() + int(ms * 1_000_000)
    while time.perf_counter_ns() < deadline:
        pass


async def stub_pipeline() -> Trace:
    """A fake Band A pipeline with the real stage names and known durations."""
    trace = Trace(budget_ms=BUDGET_MS)
    for name, ms in STUB_STAGE_MS.items():
        with span(trace, name):
            _spin_ms(ms)
    trace.finish()
    return trace


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchResult:
    samples_ms: list[float]
    stage_ms: dict[str, list[float]]
    warmup: int
    concurrency: int


async def run_benchmark(
    fn: Callable[[], Awaitable[Trace]],
    runs: int,
    warmup: int = WARMUP_RUNS,
    concurrency: int = 1,
) -> BenchResult:
    """Run `fn` (runs + warmup) times and collect per-run and per-stage timings.

    The first `warmup` results are discarded. Cold ONNX sessions, a cold HNSW page
    cache and first-call import paths inflate P100 in a way that does not represent
    steady state, and including them makes the published tail meaningless.
    """
    samples: list[float] = []
    stages: dict[str, list[float]] = {}
    total = runs + warmup
    completed = 0

    while completed < total:
        batch = min(concurrency, total - completed)
        traces = await asyncio.gather(*(fn() for _ in range(batch)))
        for i, trace in enumerate(traces):
            if completed + i < warmup:
                continue
            samples.append(trace.total_ms)
            for s in trace.spans:
                stages.setdefault(s.name, []).append(s.ms)
        completed += batch

    return BenchResult(
        samples_ms=samples, stage_ms=stages, warmup=warmup, concurrency=concurrency
    )


def summarize(samples: list[float]) -> dict[str, float]:
    arr = np.asarray(samples, dtype=np.float64)
    out: dict[str, float] = {
        f"p{p}": float(np.percentile(arr, p, method=PERCENTILE_METHOD))
        for p in PERCENTILES
    }
    out["mean"] = float(arr.mean())
    out["stddev"] = float(arr.std(ddof=1)) if arr.size > 1 else 0.0
    out["min"] = float(arr.min())
    return out


def _git_sha() -> str:
    """Which commit produced this number. A result without one is not reproducible."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() or "uncommitted"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def write_result(
    label: str,
    band: str,
    result: BenchResult,
    summary: dict[str, float],
    breakdown: bool,
) -> Path:
    """Write a dated, immutable JSON. Rules.md section 5: never overwrite."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    path = RESULTS_DIR / f"{stamp}-band{band.lower()}-{label}.json"

    payload: dict[str, object] = {
        "label": label,
        "band": band,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor(),
        },
        "methodology": {
            "warmup_discarded": result.warmup,
            "samples": len(result.samples_ms),
            "concurrency": result.concurrency,
            "percentile_method": PERCENTILE_METHOD,
            "clock": "time.perf_counter_ns",
        },
        "budget_ms": BUDGET_MS,
        "summary_ms": {k: round(v, 3) for k, v in summary.items()},
    }

    if breakdown:
        payload["stage_median_ms"] = {
            name: round(statistics.median(vals), 3)
            for name, vals in result.stage_ms.items()
        }

    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def report(label: str, band: str, summary: dict[str, float], result: BenchResult, breakdown: bool) -> None:
    print(f"\n  {label}  (Band {band})")
    print(f"  {len(result.samples_ms)} samples, {result.warmup} warmup discarded, "
          f"concurrency {result.concurrency}")
    print("  " + "-" * 44)
    for p in PERCENTILES:
        key = f"p{p}"
        flag = "" if summary[key] <= BUDGET_MS else "  OVER BUDGET"
        print(f"  {key.upper():<6} {summary[key]:>9.2f} ms{flag}")
    print("  " + "-" * 44)
    print(f"  mean   {summary['mean']:>9.2f} ms")
    print(f"  stddev {summary['stddev']:>9.2f} ms")

    if breakdown:
        print("\n  per-stage medians")
        print("  " + "-" * 44)
        for name, vals in result.stage_ms.items():
            print(f"  {name:<20} {statistics.median(vals):>8.2f} ms")


async def main_async(args: argparse.Namespace) -> int:
    if not args.stub:
        print("Only --stub is implemented in Phase 0. The real pipeline lands in "
              "Phase 2 and plugs in via run_benchmark().", file=sys.stderr)
        return 2

    result = await run_benchmark(
        stub_pipeline, runs=args.runs, warmup=args.warmup, concurrency=args.concurrency
    )
    summary = summarize(result.samples_ms)
    report(args.label, args.band, summary, result, args.breakdown)

    path = write_result(args.label, args.band, result, summary, args.breakdown)
    print(f"\n  written to {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}")

    # Rig validation: the stub's stages sum to a known total. If the reported P50
    # drifts far from it, the harness overhead is polluting the measurement.
    drift = abs(summary["p50"] - STUB_EXPECTED_MS)
    print(f"\n  rig check: expected {STUB_EXPECTED_MS:.1f} ms, "
          f"P50 {summary['p50']:.2f} ms, drift {drift:.2f} ms")
    if drift > 5.0:
        print("  FAIL: harness overhead exceeds 5 ms. The rig is measuring itself.",
              file=sys.stderr)
        return 1
    print("  PASS: harness overhead is within 5 ms.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stub", action="store_true",
                        help="bench the known-answer stub pipeline")
    parser.add_argument("--runs", type=int, default=250,
                        help="measured runs after warmup (default 250)")
    parser.add_argument("--warmup", type=int, default=WARMUP_RUNS,
                        help=f"discarded warmup runs (default {WARMUP_RUNS})")
    parser.add_argument("--concurrency", type=int, default=1,
                        help="in-flight requests (Latency.md measures 1 and 8)")
    parser.add_argument("--breakdown", action="store_true",
                        help="also report per-stage medians")
    parser.add_argument("--label", default="stub", help="label for the results file")
    parser.add_argument("--band", default="A", choices=["A", "B", "C"],
                        help="measurement band per Latency.md section 1")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
