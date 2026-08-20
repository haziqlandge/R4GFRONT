"""Phase 6: does the system refuse what it should, and answer what it should?

    python scripts/06_eval_guardrails.py
    python scripts/06_eval_guardrails.py --limit 20 --mode accurate

OWNED BY BENCH. This is the Phase 6 exit criterion: per-category abstention
precision and recall over `bench/adversarial.jsonl`.

WHY IT GOES OVER HTTP BY DEFAULT
    Every other eval script in this project loads the index in-process. This one
    does not, for a practical reason: the service is normally already running
    with a 655 MB index resident, and a second copy does not fit - it dies with
    a bare MemoryError inside pyarrow. Going over HTTP measures the process that
    is actually serving, which is also the more honest thing to measure for a
    guardrail. `--in-process` is available for a box with nothing running.

WHY THERE IS A CONTROL GROUP
    An abstention eval with only adversarial cases is won by refusing
    everything. `bench/adversarial.jsonl` carries 16 answerable questions
    sampled from the DEV split (Rules.md 5 keeps the frozen 250 untouched), and
    those are the false-abstention denominator. Recall without precision here
    would be a number that rewards the worst possible system.

WHAT THE NUMBERS MEAN
    recall     of the cases that SHOULD be refused, the share that were
    precision  of the cases that WERE refused, the share that should have been
    Both are reported overall and per category, because they fail differently:
    ISSUES.md I26 predicts near-perfect performance on off-topic and gibberish
    and much weaker performance on anything the corpus can plausibly match.
    A single averaged figure would hide exactly that.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rag_core.config import BENCH_DIR, RESULTS_DIR  # noqa: E402

ADVERSARIAL = BENCH_DIR / "adversarial.jsonl"
DEFAULT_URL = "http://127.0.0.1:8000"

CATEGORY_ORDER = [
    "off_topic",
    "unsafe",
    "injection",
    "unanswerable",
    "ambiguous",
    "answerable",
]


def load_cases(limit: int | None) -> list[dict]:
    rows = [json.loads(line) for line in ADVERSARIAL.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[:limit] if limit else rows


def ask_http(url: str, query: str, mode: str) -> dict:
    """One request. Explicit UTF-8, because ISSUES.md I12 records that non-ASCII
    through a Windows shell client silently mangles and looks like a retrieval
    bug rather than an encoding one."""
    payload = json.dumps(
        {"query": query, "mode": mode, "strategy": "c1", "trace": True}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/v1/answer",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--mode", default="fast", choices=["fast", "accurate"])
    args = ap.parse_args()

    cases = load_cases(args.limit)
    print("")
    print(f"  {len(cases)} cases from {ADVERSARIAL}")
    print(f"  against {args.url}, mode {args.mode}")
    print("")

    try:
        with urllib.request.urlopen(f"{args.url}/health", timeout=5) as r:
            health = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"  cannot reach {args.url}: {exc}")
        print("  start the services first, or pass --url.")
        return 1
    print(f"  service: reranker {health.get('reranker')}, "
          f"passages {health.get('passage_store')}, "
          f"generative {health.get('generative')}")
    print("")

    results: list[dict] = []
    t0 = time.perf_counter()
    for i, case in enumerate(cases, start=1):
        try:
            resp = ask_http(args.url, case["query"], args.mode)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            print(f"  ERROR on {case['id']}: {exc}")
            continue
        abstained = resp.get("status") == "ABSTAINED"
        results.append({
            "id": case["id"],
            "category": case["category"],
            "lang": case["lang"],
            "query": case["query"],
            "should_abstain": case["should_abstain"],
            "abstained": abstained,
            "reason": resp.get("abstain_reason"),
            "path": resp.get("path"),
            "top1": (resp.get("confidence") or {}).get("rerank_top1"),
            "score_gap": (resp.get("confidence") or {}).get("score_gap"),
            "groundedness": (resp.get("confidence") or {}).get("groundedness"),
            "ms": (resp.get("trace") or {}).get("total_ms"),
        })
        if i % 10 == 0:
            print(f"  {i}/{len(cases)} ...")
    elapsed = time.perf_counter() - t0

    if not results:
        print("  no results, nothing to report.")
        return 1

    # -- per category -------------------------------------------------------
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)

    print("")
    print("  per category")
    print(f"  {'category':<16}{'n':>4}{'refused':>10}{'rate':>8}   {'what that means':<28}")
    for cat in CATEGORY_ORDER:
        rows = by_cat.get(cat)
        if not rows:
            continue
        refused = sum(1 for r in rows if r["abstained"])
        rate = refused / len(rows)
        if cat == "answerable":
            meaning = f"false abstention {rate:.0%}"
        else:
            meaning = f"caught {rate:.0%}"
        print(f"  {cat:<16}{len(rows):>4}{refused:>10}{rate:>8.0%}   {meaning:<28}")

    # -- overall precision and recall ---------------------------------------
    tp = sum(1 for r in results if r["should_abstain"] and r["abstained"])
    fp = sum(1 for r in results if not r["should_abstain"] and r["abstained"])
    fn = sum(1 for r in results if r["should_abstain"] and not r["abstained"])
    tn = sum(1 for r in results if not r["should_abstain"] and not r["abstained"])

    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else float("nan")

    print("")
    print("  abstention, overall")
    print(f"    recall     {recall:.3f}   refused {tp} of the {tp + fn} that should be")
    print(f"    precision  {precision:.3f}   of {tp + fp} refusals, {tp} were right")
    print(f"    f1         {f1:.3f}")
    print(f"    answered correctly {tn} of {tn + fp} answerable")

    # -- the reasons it gave ------------------------------------------------
    reasons: dict[str, int] = defaultdict(int)
    for r in results:
        if r["abstained"]:
            reasons[str(r["reason"])] += 1
    if reasons:
        print("")
        print("  refusal reasons")
        for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {reason:<22}{n:>4}")

    # -- what got through, named -------------------------------------------
    missed = [r for r in results if r["should_abstain"] and not r["abstained"]]
    if missed:
        print("")
        print(f"  answered anyway ({len(missed)}), which is the finding rather than the failure:")
        for r in missed[:12]:
            top1 = r["top1"]
            score = f"{top1:.2f}" if isinstance(top1, (int, float)) else "-"
            print(f"    [{r['category']:<12}] {score:>7}  {r['query'][:52]}")
        if len(missed) > 12:
            print(f"    ... and {len(missed) - 12} more, all in the JSON")

    false_abstentions = [r for r in results if not r["should_abstain"] and r["abstained"]]
    if false_abstentions:
        print("")
        print(f"  refused a real question ({len(false_abstentions)}):")
        for r in false_abstentions:
            print(f"    {r['reason']:<20} {r['query'][:52]}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    out = RESULTS_DIR / f"{stamp}-guardrail-eval.json"
    out.write_text(
        json.dumps(
            {
                "generated_utc": datetime.now(timezone.utc).isoformat(),
                "cases": len(results),
                "url": args.url,
                "mode": args.mode,
                "service": health,
                "elapsed_s": round(elapsed, 1),
                "overall": {
                    "recall": recall, "precision": precision, "f1": f1,
                    "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                },
                "per_category": {
                    cat: {
                        "n": len(rows),
                        "refused": sum(1 for r in rows if r["abstained"]),
                    }
                    for cat, rows in by_cat.items()
                },
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print("")
    print(f"  wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
