"""Does an LLM's context-sufficiency judgement predict OUR wrong answers?

This is the correctness signal `ISSUES.md` I31 went looking for and did not find.
Measured there, over 466 in-corpus queries: absolute rerank score AUC 0.606,
margin-over-second AUC 0.586, where 0.500 is a coin flip. Neither can carry a
threshold, so the system currently answers confidently and has no idea when it
is wrong (I26: 62.1% wrong under strict labelling).

WHY THIS SIGNAL IS DIFFERENT FROM THE ONE A COUNCIL REJECTED
A council review of a proposal to fact-check our answers against an external LLM
killed it, correctly: the corpus peaks in 2017, so a current model disagrees
hardest on the answers MOST faithful to the corpus, and the flag ends up
anti-correlated with correctness.

Context sufficiency is not that question. It never asks "is this true", only
"do these passages answer this question". Staleness does not poison it: the
passages about India's population DO answer "what is the population of India",
whatever the number says, so a stale-but-faithful answer is judged sufficient
and survives. A rival system (pucho.me) uses exactly this mechanism to decline
after a confidently wrong extraction, which is what suggested measuring it.

The base rate is already known - `DONT-FORGET.md` 10 records gpt-oss-20b
returning INSUFFICIENT_CONTEXT on 50% of queries when handed our top-3. What has
never been measured is whether that judgement CORRELATES with our top-1 being
wrong. A signal that fires on half of everything is useless if it fires at
random.

RATE LIMIT. `ISSUES.md` I7: 12,000 tokens per window, about 12 calls. That makes
live per-query verification inoperable and it does NOT prevent an offline study -
this paces itself and backs off, and 120 queries takes minutes, not days.

    python scripts/11_llm_judge.py --limit 120

Writes a dated immutable JSON to bench/results/ like every other measurement.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import httpx
import numpy as np

ROOT: Final[Path] = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from rag_core.config import GROQ_MODEL, GROQ_URL, USER_AGENT, load_env  # noqa: E402

QUERIES: Final[Path] = ROOT / "bench" / "queries_250.jsonl"
RESULTS: Final[Path] = ROOT / "bench" / "results"
ANSWER_URL: Final[str] = "https://shrutirag.duckdns.org/api/core/v1/answer"

# Sufficiency only. It is never shown the answer we produced and never asked
# whether that answer is true - only whether the passages we retrieved contain
# what the question asks for. Asking anything more would reintroduce the
# staleness problem the council identified.
JUDGE_PROMPT: Final[str] = (
    "You judge whether a set of retrieved passages contains the answer to a question.\n"
    "You are NOT judging whether the passages are up to date, correct about the world, "
    "or well written. Old facts still count as answering the question.\n"
    "Reply with exactly one word: SUFFICIENT or INSUFFICIENT."
)


async def judge(client: httpx.AsyncClient, key: str, query: str, passages: list[str]) -> str | None:
    body = "\n\n".join(f"[{i + 1}] {p[:700]}" for i, p in enumerate(passages))
    # max_tokens 64 and reasoning_effort low, NOT the 8 tokens this obviously
    # needs. gpt-oss-20b is a REASONING model: it spends tokens in a `reasoning`
    # field first, so a tight cap returns finish_reason "length" with an EMPTY
    # content string and the study silently collects zero samples. config.py
    # already records this exact trap for qwen3.6-27b, one model over.
    # Measured: the verdict costs 53 completion tokens, so 64 is the floor.
    payload = {
        "model": GROQ_MODEL,
        "temperature": 0,
        "max_tokens": 64,
        "reasoning_effort": "low",
        "messages": [
            {"role": "system", "content": JUDGE_PROMPT},
            {"role": "user", "content": f"Question: {query}\n\nPassages:\n{body}\n\nSUFFICIENT or INSUFFICIENT?"},
        ],
    }
    for attempt in range(6):
        try:
            r = await client.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT},
                json=payload,
                timeout=45.0,
            )
        except httpx.HTTPError:
            await asyncio.sleep(5.0 * (attempt + 1))
            continue
        if r.status_code == 429:
            # The documented failure mode, not an error. Wait out the window.
            wait = float(r.headers.get("retry-after", 20)) + 2.0
            print(f"    429, waiting {wait:.0f}s", flush=True)
            await asyncio.sleep(wait)
            continue
        if r.status_code >= 400:
            return None
        text = (r.json()["choices"][0]["message"]["content"] or "").strip().upper()
        if "INSUFF" in text:
            return "INSUFFICIENT"
        if "SUFF" in text:
            return "SUFFICIENT"
        return None
    return None


def auc(pos: list[float], neg: list[float]) -> float:
    if not pos or not neg:
        return float("nan")
    return sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))


async def main_async(args: argparse.Namespace) -> int:
    load_env()
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        print("GROQ_API_KEY is not set.")
        return 1

    rows = [json.loads(x) for x in QUERIES.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = rows[: args.limit]

    out_rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient() as client:
        for i, row in enumerate(rows, 1):
            lang = "en" if i % 2 else "hi"
            query = row["query_en"] if lang == "en" else row["query_hi"]
            if not query:
                continue

            r = await client.post(
                ANSWER_URL,
                json={"query": query, "language": lang, "mode": "fast", "trace": True},
                timeout=30.0,
            )
            b = r.json()
            cites = b.get("citations", [])
            if not cites or b.get("status") != "ANSWERED":
                continue

            gold_heads = {
                g.rsplit(":", 1)[0]
                for g in (row.get("gold_en_ids") or []) + (row.get("gold_hi_ids") or [])
            }
            top_id = cites[0]["passage_id"]
            correct = top_id.rsplit(":", 1)[0] in gold_heads

            verdict = await judge(client, key, query, [c["text"] for c in cites[:3]])
            if verdict is None:
                continue

            out_rows.append({
                "query_id": row["query_id"], "lang": lang, "query": query,
                "top_id": top_id, "correct": correct, "verdict": verdict,
                "score": (b.get("confidence") or {}).get("rerank_top1"),
                "gap": (b.get("confidence") or {}).get("score_gap"),
            })
            mark = "OK " if correct else "BAD"
            print(f"  {len(out_rows):>3}/{args.limit}  {mark}  {verdict:<12} {query[:44]}", flush=True)
            await asyncio.sleep(args.delay)

    n = len(out_rows)
    if n < 20:
        print(f"\nonly {n} usable samples; not enough to conclude anything.")
        return 1

    correct_rows = [r for r in out_rows if r["correct"]]
    wrong_rows = [r for r in out_rows if not r["correct"]]
    suff = lambda rs: sum(1 for r in rs if r["verdict"] == "SUFFICIENT") / len(rs) if rs else 0.0

    # The judge as a binary detector of OUR wrongness.
    tp = sum(1 for r in wrong_rows if r["verdict"] == "INSUFFICIENT")
    fp = sum(1 for r in correct_rows if r["verdict"] == "INSUFFICIENT")
    fn = len(wrong_rows) - tp
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    judge_auc = auc(
        [1.0 if r["verdict"] == "SUFFICIENT" else 0.0 for r in correct_rows],
        [1.0 if r["verdict"] == "SUFFICIENT" else 0.0 for r in wrong_rows],
    )
    score_auc = auc([r["score"] for r in correct_rows if r["score"] is not None],
                    [r["score"] for r in wrong_rows if r["score"] is not None])

    print("\n" + "=" * 76)
    print(f"samples {n}   our top-1 correct {len(correct_rows)}   wrong {len(wrong_rows)}")
    print(f"judge says SUFFICIENT when we are RIGHT : {suff(correct_rows)*100:5.1f}%")
    print(f"judge says SUFFICIENT when we are WRONG : {suff(wrong_rows)*100:5.1f}%")
    print()
    print(f"  judge AUC vs our correctness   {judge_auc:.3f}")
    print(f"  rerank score AUC, same sample  {score_auc:.3f}")
    print(f"  (I31 measured score AUC 0.606 over 466; 0.500 is a coin flip)")
    print()
    print(f"  as a detector of a wrong answer: precision {precision:.3f}  recall {recall:.3f}")
    print("=" * 76)
    verdict_line = (
        "USABLE SIGNAL - the judge separates right from wrong better than our own scores."
        if judge_auc > score_auc + 0.05
        else "NOT A USABLE SIGNAL - no better than what we already have."
    )
    print("\n" + verdict_line)

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "what": "does an LLM context-sufficiency judgement predict our own wrong top-1",
        "model": GROQ_MODEL,
        "note": args.note,
        "n": n,
        "n_correct": len(correct_rows),
        "n_wrong": len(wrong_rows),
        "sufficient_when_right": round(suff(correct_rows), 4),
        "sufficient_when_wrong": round(suff(wrong_rows), 4),
        "judge_auc": round(judge_auc, 4),
        "rerank_score_auc_same_sample": round(score_auc, 4),
        "detector_precision": round(precision, 4),
        "detector_recall": round(recall, 4),
        "verdict": verdict_line,
        "rows": out_rows,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    path = RESULTS / f"{stamp}-llm-judge-{args.label}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {path}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=120)
    p.add_argument("--delay", type=float, default=2.0, help="seconds between calls, to pace the window")
    p.add_argument("--label", default="sufficiency")
    p.add_argument("--note", default="")
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
