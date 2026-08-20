"""Band A against the DEPLOYED service, over its public HTTPS origin. Phase 7.

Latency.md section 6 requires published figures to come from the deployed
service. This measures it from outside without needing a shell on the box.

The number it publishes is `trace.total_ms`, which `rag_core` measures with
`time.perf_counter_ns` INSIDE the process, at the same stage boundaries the
in-process harness uses. The network hop is measured too, separately, as
`wall_ms` - it is not Band A and is never mixed into it, but it is what a judge
clicking the live URL actually waits for, so it is worth having.

    python scripts/07_bench_deployed.py --lang both --passes 2 --label baseline

Methodology (Latency.md 6):
  - warmup runs discarded before measurement starts
  - percentiles by numpy nearest rank; P100 is the true maximum
  - one request at a time unless --concurrency says otherwise
  - every run writes a dated immutable JSON to bench/results/
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import httpx
import numpy as np

ROOT: Final[Path] = Path(__file__).resolve().parents[1]
QUERIES: Final[Path] = ROOT / "bench" / "queries_250.jsonl"
RESULTS: Final[Path] = ROOT / "bench" / "results"
DEFAULT_URL: Final[str] = "https://shrutirag.duckdns.org/api/core"
PERCENTILES: Final[list[int]] = [50, 70, 90, 95, 99, 100]
BUDGET_MS: Final[float] = 200.0


def load_queries(limit: int | None) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in QUERIES.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[:limit] if limit else rows


def pct(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    arr = np.asarray(values, dtype=float)
    out = {f"p{p}": round(float(np.percentile(arr, p, method="nearest")), 2) for p in PERCENTILES}
    out["mean"] = round(float(arr.mean()), 2)
    out["sd"] = round(float(arr.std(ddof=1)), 2) if len(arr) > 1 else 0.0
    return out


async def one(client: httpx.AsyncClient, url: str, query: str, lang: str, mode: str) -> dict[str, Any]:
    payload = {"query": query, "language": lang, "mode": mode, "trace": True}
    started = time.perf_counter_ns()
    try:
        r = await client.post(f"{url}/v1/answer", json=payload)
    except Exception as exc:  # network faults are data, not a crash
        return {"http": 0, "error": str(exc)[:200],
                "wall_ms": (time.perf_counter_ns() - started) / 1e6}
    wall_ms = (time.perf_counter_ns() - started) / 1e6
    rec: dict[str, Any] = {"http": r.status_code, "wall_ms": round(wall_ms, 2)}
    if r.status_code != 200:
        rec["body"] = r.text[:200]
        return rec
    body = r.json()
    trace = body.get("trace") or {}
    rec["total_ms"] = round(float(trace.get("total_ms", 0.0)), 2)
    rec["status"] = body.get("status")
    rec["path"] = body.get("path")
    rec["abstain_reason"] = body.get("abstain_reason")
    rec["rerank_top1"] = (body.get("confidence") or {}).get("rerank_top1")
    rec["stages"] = {s["name"]: round(float(s["ms"]), 3) for s in trace.get("stages", [])}
    rec["skipped"] = [s["name"] for s in trace.get("stages", []) if s.get("status") == "skipped"]
    # The rerank stage notes a truncated rerank as "deadline: scored N of M".
    rec["partial"] = [f'{s["name"]}: {s["detail"]}' for s in trace.get("stages", [])
                      if (s.get("detail") or "").startswith("deadline:")]
    return rec


async def run_lang(client: httpx.AsyncClient, url: str, rows: list[dict[str, Any]],
                   lang: str, passes: int, warmup: int, mode: str,
                   concurrency: int) -> dict[str, Any]:
    field = "query_en" if lang == "en" else "query_hi"
    queries = [r[field] for r in rows if r.get(field)]
    ids = [r["query_id"] for r in rows if r.get(field)]

    # Warmup, discarded. ONNX sessions and the HNSW page cache are cold on first use.
    for q in queries[:warmup]:
        await one(client, url, q, lang, mode)

    records: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(concurrency)

    async def guarded(q: str, qid: int, p: int) -> None:
        async with sem:
            rec = await one(client, url, q, lang, mode)
            rec["query_id"] = qid
            rec["pass"] = p
            records.append(rec)

    for p in range(passes):
        if concurrency == 1:
            for q, qid in zip(queries, ids):
                await guarded(q, qid, p)
        else:
            await asyncio.gather(*(guarded(q, qid, p) for q, qid in zip(queries, ids)))
        print(f"  {lang} pass {p + 1}/{passes} done ({len(records)} samples)", flush=True)

    ok = [r for r in records if r["http"] == 200]
    totals = [r["total_ms"] for r in ok]
    walls = [r["wall_ms"] for r in ok]

    stage_ms: dict[str, list[float]] = defaultdict(list)
    for r in ok:
        for name, ms in r.get("stages", {}).items():
            stage_ms[name].append(ms)

    over = [r for r in ok if r["total_ms"] > BUDGET_MS]
    worst = sorted(ok, key=lambda r: -r["total_ms"])[:12]
    paths: dict[str, int] = defaultdict(int)
    for r in ok:
        paths[str(r.get("path"))] += 1

    return {
        "n": len(ok),
        "n_requested": len(records),
        "non_200": [{"http": r["http"], "query_id": r.get("query_id"),
                     "body": r.get("body") or r.get("error")} for r in records if r["http"] != 200][:10],
        "band_a_total_ms": pct(totals),
        "client_wall_ms": pct(walls),
        "over_budget": {
            "count": len(over),
            "share": round(len(over) / len(ok), 4) if ok else None,
            "query_ids": sorted({int(r["query_id"]) for r in over})[:40],
        },
        "stage_median_ms": {k: round(statistics.median(v), 2) for k, v in sorted(stage_ms.items())},
        "stage_p100_ms": {k: round(max(v), 2) for k, v in sorted(stage_ms.items())},
        "path_distribution": dict(paths),
        "partial_reranks": sum(1 for r in ok if r.get("partial")),
        "skipped_stages": sum(1 for r in ok if r.get("skipped")),
        "worst_12": [{"query_id": r["query_id"], "total_ms": r["total_ms"],
                      "wall_ms": r["wall_ms"], "stages": r["stages"]} for r in worst],
    }


async def main_async(args: argparse.Namespace) -> int:
    rows = load_queries(args.limit)
    langs = ["en", "hi"] if args.lang == "both" else [args.lang]

    async with httpx.AsyncClient(timeout=30.0, http2=False) as client:
        health = (await client.get(f"{args.url}/health")).json()
        print(f"health: {json.dumps(health)}", flush=True)

        out: dict[str, Any] = {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "band": "A",
            "measured_from": "public HTTPS origin; band A is the in-process trace.total_ms",
            "url": args.url,
            "health": health,
            "note": args.note,
            "method": {
                "queries": len(rows),
                "passes": args.passes,
                "warmup": args.warmup,
                "concurrency": args.concurrency,
                "mode": args.mode,
                "percentile": "nearest",
                "clock": "time.perf_counter_ns in-process (band A) / client-side (wall)",
                "budget_ms": BUDGET_MS,
            },
            "langs": {},
        }
        for lang in langs:
            print(f"running {lang} ...", flush=True)
            out["langs"][lang] = await run_lang(
                client, args.url, rows, lang, args.passes, args.warmup,
                args.mode, args.concurrency,
            )

    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    path = RESULTS / f"{stamp}-banda-deployed-{args.label}.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    for lang, res in out["langs"].items():
        a = res["band_a_total_ms"]
        w = res["client_wall_ms"]
        print(f"\n{lang}  n={res['n']}")
        print(f"  band A  P50 {a['p50']:>7}  P70 {a['p70']:>7}  P90 {a['p90']:>7}  "
              f"P99 {a['p99']:>7}  P100 {a['p100']:>7}")
        print(f"  wall    P50 {w['p50']:>7}  P70 {w['p70']:>7}  P90 {w['p90']:>7}  "
              f"P99 {w['p99']:>7}  P100 {w['p100']:>7}")
        print(f"  over 200 ms: {res['over_budget']['count']}/{res['n']} "
              f"({(res['over_budget']['share'] or 0) * 100:.2f}%)")
        print(f"  stages: {res['stage_median_ms']}")
    print(f"\nwrote {path}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--lang", default="both", choices=["en", "hi", "both"])
    p.add_argument("--passes", type=int, default=1)
    p.add_argument("--warmup", type=int, default=30)
    p.add_argument("--limit", type=int, default=None, help="use only the first N queries")
    p.add_argument("--concurrency", type=int, default=1)
    p.add_argument("--mode", default="fast", choices=["fast", "accurate"])
    p.add_argument("--label", default="run")
    p.add_argument("--note", default="")
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
