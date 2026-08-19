"""Phase 5: calibrate the routing thresholds on rerank scores. Not on guesses.

    python scripts/06_calibrate_routing.py
    python scripts/06_calibrate_routing.py --limit 200 --depth 20

OWNED BY BENCH. Quality numbers transfer across machines (Devices.md 2).

Phases.md is explicit: "Plot rerank top-1 score against answer correctness and pick
the thresholds off the curve. Do not guess them." This is that script.

WHY THE SIGNAL IS THE RERANK SCORE AND NEVER THE DENSE SCORE
    ISSUES.md I3, measured on the live endpoint: a correct English match scores
    0.9193, a correct Hindi match 0.9050, and pure gibberish scores 0.8624. A 0.05
    margin between "right answer" and "meaningless input" cannot carry an
    abstention floor. A bi-encoder compares two embeddings that never met; a
    cross-encoder reads the pair together. Architecture.md 3.6 chose the reranker
    score for exactly this and this script is where that choice gets its numbers.

    Architecture.md 7 Layer 2 names a confidence floor of 0.35. That number was
    written for a normalised score and is not on this model's scale, which is raw
    logits running roughly -11 to +11. It is superseded by whatever comes out here.

THE THREE POPULATIONS
    Calibration needs to separate "the top hit is right" from "the top hit is
    wrong" AND from "nothing here could be right". Three populations are scored,
    all on the dev partition, all through the identical retrieve-then-rerank path:

    answerable   real query, its own candidates. Label = is top-1 actually gold.
                 This is the signal that separates EXTRACTIVE from GENERATIVE:
                 the extractive path returns top-1 verbatim, so its threshold is
                 the score above which top-1 is usually right.

    mismatched   real, well-formed query scored against ANOTHER query's candidate
                 pool. Genuinely unanswerable from those passages, and free -
                 no labelling, no LLM, no hand-written adversarial set. This is
                 the honest model of Phase 6's "unanswerable-from-corpus"
                 category, and it is a much harder negative than gibberish
                 because the query is real language.

    gibberish    the I3 probe, kept because it is the case dense scoring failed on
                 and the comparison is the evidence that moving the signal to the
                 reranker was the right call.

    Rules.md 5 is HARD: thresholds are fitted on the DEV partition. The 1,000-query
    test split and bench/queries_250.jsonl are never touched here - tuning against
    a benchmark you then publish is fitting the number to the test set.
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

from _progress import Progress  # noqa: E402

from rag_core.config import (  # noqa: E402
    INT8_MODEL,
    ONNX_DIR,
    PASSAGES_PARQUET,
    QUERIES_PARQUET,
    RERANK_MODEL_FILE,
    RERANK_TOKENIZER_FILE,
    RERANK_TOP_K,
    RERANKER,
    RESULTS_DIR,
    TOKENIZER_FILE,
    load_env,
)
from rag_core.retrieval.dense import DenseIndex  # noqa: E402
from rag_core.retrieval.embedder import Embedder  # noqa: E402
from rag_core.retrieval.rerank import CrossEncoder  # noqa: E402

load_env()

STRATEGY = "c1"
DEFAULT_QUERIES = 250
DEDUP_POOL_MULT = 4

# I3's probe, plus two more in the same spirit. Deliberately few: gibberish is the
# easy negative and the mismatched population carries the real weight.
GIBBERISH = [
    "zxqwv fhqwhgads plorbnak",
    "qqq zzz vvv bbb nnn mmm",
    "फ्लॉर्ब ज़्क्विं प्लोरबनक",
]

# Target precision for the extractive path: when we answer with the top passage
# verbatim and make no LLM call, that passage should be the gold one this often.
# Set at 0.75 rather than higher because the alternative above the threshold is not
# "a better answer", it is a 352 ms Groq call (Latency.md 2) - so the bar is "good
# enough that paying 100x the latency is not obviously better", not "certain".
EXTRACTIVE_TARGET_PRECISION = 0.75

# Below this, answering at all is worse than saying so. Fitted as the score that
# keeps most genuinely-unanswerable queries out while surrendering few answerable
# ones: abstention recall on the mismatched population at a bounded cost in
# false abstentions on the answerable one.
ABSTAIN_TARGET_FPR = 0.05  # at most 5% of ANSWERABLE queries may be abstained on


def load_queries(limit: int) -> list[dict]:
    rows = [q for q in pq.read_table(QUERIES_PARQUET).to_pylist() if q["split"] == "dev"]
    rows = [q for q in rows if q["gold_en_ids"] and q["gold_hi_ids"]]
    return rows[:limit]


def collect(
    queries: list[dict], depth: int
) -> dict[str, dict[str, np.ndarray]]:
    """Score all three populations through one identical code path."""
    embedder = Embedder(
        ONNX_DIR / INT8_MODEL, ONNX_DIR / TOKENIZER_FILE, threads=2
    )
    sub = ONNX_DIR / f"rerank-{RERANKER}"
    ce = CrossEncoder(sub / RERANK_MODEL_FILE, sub / RERANK_TOKENIZER_FILE, threads=2)
    index = DenseIndex(STRATEGY)
    index.load()
    text_by_id = {
        p["passage_id"]: p["text"]
        for p in pq.read_table(
            PASSAGES_PARQUET, columns=["passage_id", "text"]
        ).to_pylist()
    }

    def candidates(query_text: str) -> list[str]:
        vec = embedder.encode_one(query_text, "query")
        seen: set[str] = set()
        distinct: list[str] = []
        for row, _ in index.search(vec, depth * DEDUP_POOL_MULT):
            pid = index.chunk(row)["passage_id"]
            if pid not in seen:
                seen.add(pid)
                distinct.append(pid)
            if len(distinct) >= depth:
                break
        return distinct

    def top1(query_text: str, pool: list[str]) -> tuple[str, float, float]:
        pairs = [(pid, text_by_id[pid]) for pid in pool]
        ranked, _ = ce.rerank(query_text, pairs)
        gap = ranked[0][1] - ranked[1][1] if len(ranked) > 1 else 0.0
        return ranked[0][0], ranked[0][1], gap

    out: dict[str, dict[str, np.ndarray]] = {}

    for lang in ("en", "hi"):
        qfield = "query_en" if lang == "en" else "query_hi"
        gfield = "gold_en_ids" if lang == "en" else "gold_hi_ids"

        pools = []
        prog = Progress(len(queries), f"retrieve {lang}")
        t0 = time.perf_counter()
        for i, q in enumerate(queries):
            pools.append(candidates(q[qfield]))
            prog.report(i + 1, time.perf_counter() - t0)

        # -- answerable: query against its OWN candidates
        scores, gaps, correct = [], [], []
        prog = Progress(len(queries), f"answerable {lang}")
        t0 = time.perf_counter()
        for i, q in enumerate(queries):
            pid, s, g = top1(q[qfield], pools[i])
            scores.append(s)
            gaps.append(g)
            correct.append(1.0 if pid in set(q[gfield]) else 0.0)
            prog.report(i + 1, time.perf_counter() - t0)
        out[f"answerable_{lang}"] = {
            "score": np.array(scores),
            "gap": np.array(gaps),
            "correct": np.array(correct),
        }

        # -- mismatched: query against a DIFFERENT query's candidates.
        # Offset by half the list rather than shuffling, so the pairing is
        # deterministic and reproducible across runs without carrying a seed.
        shift = len(queries) // 2
        scores, gaps = [], []
        prog = Progress(len(queries), f"mismatched {lang}")
        t0 = time.perf_counter()
        for i, q in enumerate(queries):
            other = pools[(i + shift) % len(queries)]
            # Guard: if the other pool happens to contain this query's gold, it is
            # not a negative. Rare, but it would quietly poison the threshold.
            if set(q[gfield]) & set(other):
                prog.report(i + 1, time.perf_counter() - t0)
                continue
            _, s, g = top1(q[qfield], other)
            scores.append(s)
            gaps.append(g)
            prog.report(i + 1, time.perf_counter() - t0)
        out[f"mismatched_{lang}"] = {
            "score": np.array(scores),
            "gap": np.array(gaps),
        }

    # -- gibberish: the I3 probe
    scores = []
    for g in GIBBERISH:
        _, s, _ = top1(g, candidates(g))
        scores.append(s)
    out["gibberish"] = {"score": np.array(scores)}

    return out


def precision_curve(
    scores: np.ndarray, correct: np.ndarray, thresholds: np.ndarray
) -> list[tuple[float, float, float]]:
    """(threshold, precision above it, fraction of queries above it)."""
    rows = []
    for t in thresholds:
        sel = scores >= t
        n = int(sel.sum())
        prec = float(correct[sel].mean()) if n else float("nan")
        rows.append((float(t), prec, n / len(scores)))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=DEFAULT_QUERIES)
    ap.add_argument("--depth", type=int, default=RERANK_TOP_K)
    args = ap.parse_args()

    queries = load_queries(args.limit)
    print("")
    print(f"  {len(queries)} DEV queries (Rules.md 5: never the test/bench split)")
    print(f"  reranker {RERANKER}, strategy {STRATEGY}, depth {args.depth}")
    print("")

    pops = collect(queries, args.depth)

    ans = {
        "score": np.concatenate([pops["answerable_en"]["score"], pops["answerable_hi"]["score"]]),
        "correct": np.concatenate([pops["answerable_en"]["correct"], pops["answerable_hi"]["correct"]]),
    }
    mis = np.concatenate([pops["mismatched_en"]["score"], pops["mismatched_hi"]["score"]])
    gib = pops["gibberish"]["score"]

    print("")
    print("  score distributions (rerank top-1 logit)")
    print(f"  {'population':<22}{'n':>6}{'p05':>8}{'p25':>8}{'median':>9}{'p75':>8}{'p95':>8}")
    for name, arr in (
        ("answerable, correct", ans["score"][ans["correct"] == 1.0]),
        ("answerable, wrong", ans["score"][ans["correct"] == 0.0]),
        ("mismatched (no answer)", mis),
        ("gibberish", gib),
    ):
        if len(arr) == 0:
            continue
        q = np.percentile(arr, [5, 25, 50, 75, 95])
        print(f"  {name:<22}{len(arr):>6}{q[0]:>8.2f}{q[1]:>8.2f}{q[2]:>9.2f}{q[3]:>8.2f}{q[4]:>8.2f}")

    lo, hi = float(np.percentile(ans["score"], 1)), float(np.percentile(ans["score"], 99))
    grid = np.linspace(lo, hi, 60)

    print("")
    print("  extractive threshold: precision of top-1 above each cut")
    print(f"  {'cut':>8}{'precision':>12}{'coverage':>11}")
    curve = precision_curve(ans["score"], ans["correct"], grid)
    tau_high = None
    for t, prec, cov in curve:
        if not np.isnan(prec) and prec >= EXTRACTIVE_TARGET_PRECISION and tau_high is None:
            tau_high = t
    for t, prec, cov in curve[::6]:
        mark = ""
        if tau_high is not None and abs(t - tau_high) < 1e-9:
            mark = "  <- tau_high"
        print(f"  {t:>8.2f}{prec:>12.3f}{cov:>11.1%}{mark}")

    # tau_low: keep false abstentions on answerable queries under the target, then
    # report how much of the unanswerable population that catches.
    tau_low = float(np.percentile(ans["score"], ABSTAIN_TARGET_FPR * 100))
    caught = float((mis < tau_low).mean())
    caught_gib = float((gib < tau_low).mean())

    print("")
    print(f"  abstain threshold at {ABSTAIN_TARGET_FPR:.0%} false-abstention on answerable queries")
    print(f"    tau_low  = {tau_low:.3f}")
    print(f"    catches  {caught:.1%} of genuinely-unanswerable queries")
    print(f"    catches  {caught_gib:.1%} of gibberish")

    if tau_high is None:
        tau_high = float(np.percentile(ans["score"], 75))
        print("")
        print(f"  WARNING no cut reaches {EXTRACTIVE_TARGET_PRECISION:.0%} precision.")
        print(f"  tau_high falls back to the p75 score ({tau_high:.3f}) and the")
        print("  extractive path cannot be the confident default - see D2's reversal")
        print("  condition and assumption A6 in Memory.md.")

    print("")
    print("  RESULT")
    print(f"    ABSTAIN     score <  {tau_low:.3f}")
    print(f"    GENERATIVE  {tau_low:.3f} <= score < {tau_high:.3f}")
    print(f"    EXTRACTIVE  score >= {tau_high:.3f}")
    routed = {
        "abstain": float((ans["score"] < tau_low).mean()),
        "generative": float(((ans["score"] >= tau_low) & (ans["score"] < tau_high)).mean()),
        "extractive": float((ans["score"] >= tau_high).mean()),
    }
    print("")
    print("  path distribution over answerable dev queries:")
    for k, v in routed.items():
        print(f"    {k:<12}{v:>7.1%}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    out = RESULTS_DIR / f"{stamp}-routing-calibration.json"
    out.write_text(
        json.dumps(
            {
                "generated_utc": datetime.now(timezone.utc).isoformat(),
                "queries": len(queries),
                "reranker": RERANKER,
                "strategy": STRATEGY,
                "depth": args.depth,
                "targets": {
                    "extractive_precision": EXTRACTIVE_TARGET_PRECISION,
                    "abstain_false_rate": ABSTAIN_TARGET_FPR,
                },
                "thresholds": {"tau_low": tau_low, "tau_high": tau_high},
                "unanswerable_caught": caught,
                "gibberish_caught": caught_gib,
                "path_distribution": routed,
                "precision_curve": [
                    {"cut": t, "precision": p, "coverage": c} for t, p, c in curve
                ],
                "scores": {
                    "answerable": ans["score"].tolist(),
                    "answerable_correct": ans["correct"].tolist(),
                    "mismatched": mis.tolist(),
                    "gibberish": gib.tolist(),
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
