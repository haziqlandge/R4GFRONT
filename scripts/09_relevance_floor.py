"""Where should the abstention floor sit? Measured on both sides of it.

The complaint this answers: the system answers questions the corpus cannot
answer, with a passage that shares a word or two and nothing else. "Who is
Narendra Modi" comes back with a passage about Venkaiah Naidu.

That is `ISSUES.md` I26 in the wild - `tau_low = -1.103` is an out-of-domain
detector, and these questions are not out of domain. The corpus HAS Indian
politics passages; it just does not have that answer. So the floor never fires.

Raising the floor trades coverage for precision, and the only honest way to pick
a value is to measure both sides:

  SET A, in-corpus     the frozen 250, both languages, where gold is known. Gives
                       precision (is top-1 the gold passage) and coverage at each
                       candidate floor.
  SET B, near-miss     questions a MS MARCO slice plausibly cannot answer but
                       which retrieve SOMETHING - general knowledge, current
                       affairs, named people. This is the set the complaint is
                       about, and no existing eval covers it: the Phase 6
                       adversarial set has gibberish and off-topic, both of which
                       already score far below the floor.

    python scripts/09_relevance_floor.py --label floor-sweep

Writes a dated JSON to bench/results/ like every other measurement here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import httpx
import numpy as np

ROOT: Final[Path] = Path(__file__).resolve().parents[1]
QUERIES: Final[Path] = ROOT / "bench" / "queries_250.jsonl"
RESULTS: Final[Path] = ROOT / "bench" / "results"
DEFAULT_URL: Final[str] = "https://shrutirag.duckdns.org/api/core"

# Questions that are NOT gibberish and NOT off-topic-in-the-obvious-sense. Each
# one retrieves something plausible from this corpus and none of them has its
# answer in it. That is precisely the band the existing guardrail eval misses.
NEAR_MISS: Final[list[tuple[str, str]]] = [
    # Written blind, from general knowledge, WITHOUT looking at the corpus first.
    # That is the point: these are the questions a visitor actually types, not
    # questions reverse-engineered from what happens to be indexed.
    ("en", "who is narendra modi"),
    ("en", "who is the prime minister of india"),
    ("en", "who is the president of the united states"),
    ("en", "who won the ipl in 2024"),
    ("en", "what is the population of india"),
    ("en", "how do i reset my iphone"),
    ("en", "what is the price of bitcoin today"),
    ("en", "who directed the movie oppenheimer"),
    ("en", "what is chatgpt"),
    ("en", "when is the next solar eclipse"),
    ("en", "who is the ceo of google"),
    ("en", "what is the capital of france"),
    ("en", "how do i apply for an indian passport"),
    ("en", "what is quantum entanglement"),
    ("en", "who wrote the indian constitution"),
    ("en", "how do i cook biryani"),
    ("en", "what is the weather in mumbai today"),
    ("en", "who is virat kohli"),
    ("en", "what is a large language model"),
    ("en", "when did india gain independence"),
    ("en", "who invented the light bulb"),
    ("en", "what is the tallest building in the world"),
    ("en", "how many players are there in a cricket team"),
    ("en", "what causes an earthquake"),
    ("en", "who painted the mona lisa"),
    ("en", "what is the currency of japan"),
    ("en", "how far is the sun from the earth"),
    ("en", "what language is spoken in brazil"),
    ("en", "who is the founder of microsoft"),
    ("en", "what is the longest river in the world"),
    ("hi", "नरेंद्र मोदी कौन है"),
    ("hi", "भारत के प्रधानमंत्री कौन हैं"),
    ("hi", "अमेरिका के राष्ट्रपति कौन हैं"),
    ("hi", "विराट कोहली कौन है"),
    ("hi", "भारत का संविधान किसने लिखा"),
    ("hi", "बिरयानी कैसे बनाते हैं"),
    ("hi", "मुंबई का मौसम कैसा है"),
    ("hi", "चैटजीपीटी क्या है"),
    ("hi", "गूगल का सीईओ कौन है"),
    ("hi", "आईपीएल 2024 कौन जीता"),
    ("hi", "भारत की जनसंख्या कितनी है"),
    ("hi", "भारत को आजादी कब मिली"),
    ("hi", "ताजमहल किसने बनवाया"),
    ("hi", "दुनिया की सबसे लंबी नदी कौन सी है"),
    ("hi", "जापान की मुद्रा क्या है"),
    ("hi", "मोना लिसा किसने बनाई"),
    ("hi", "भूकंप क्यों आता है"),
    ("hi", "बल्ब का आविष्कार किसने किया"),
    ("hi", "क्रिकेट टीम में कितने खिलाड़ी होते हैं"),
    ("hi", "दुनिया की सबसे ऊंची इमारत कौन सी है"),
    ("hi", "सूरज पृथ्वी से कितनी दूर है"),
    ("hi", "ब्राजील में कौन सी भाषा बोली जाती है"),
    ("hi", "माइक्रोसॉफ्ट के संस्थापक कौन हैं"),
    ("hi", "हिंदी दिवस कब मनाया जाता है"),
    ("hi", "भारत का राष्ट्रीय पशु कौन सा है"),
    ("hi", "चंद्रयान कब लॉन्च हुआ"),
    ("hi", "गंगा नदी कहाँ से निकलती है"),
    ("hi", "कंप्यूटर का आविष्कार किसने किया"),
    ("hi", "योग दिवस कब मनाया जाता है"),
    ("hi", "सबसे बड़ा ग्रह कौन सा है"),
]

FLOORS: Final[list[float]] = [-1.103, 0.0, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0]


async def ask(client: httpx.AsyncClient, url: str, q: str, lang: str) -> dict[str, Any]:
    r = await client.post(f"{url}/v1/answer",
                          json={"query": q, "language": lang, "mode": "fast", "trace": True})
    b = r.json()
    cites = b.get("citations", [])
    top = cites[0] if cites else None
    return {
        "status": b.get("status"),
        "score": (b.get("confidence") or {}).get("rerank_top1"),
        "gap": (b.get("confidence") or {}).get("score_gap"),
        "top_id": top.get("passage_id") if top else None,
        "top_lang": (top.get("passage_id") or ":").rsplit(":", 1)[-1] if top else None,
        "answer": " ".join((b.get("answer") or "").split())[:200],
    }


async def main_async(args: argparse.Namespace) -> int:
    rows = [json.loads(l) for l in QUERIES.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        rows = rows[: args.limit]

    in_corpus: list[dict[str, Any]] = []
    near_miss: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        print(f"set A, in-corpus: {len(rows) * 2} queries ...", flush=True)
        for row in rows:
            for lang, field, gold_key in (("en", "query_en", "gold_en_ids"),
                                          ("hi", "query_hi", "gold_hi_ids")):
                q = row.get(field)
                if not q:
                    continue
                res = await ask(client, args.url, q, lang)
                gold = set(row.get(gold_key) or [])
                res.update(query=q, lang=lang, query_id=row["query_id"],
                           gold_hit=bool(res["top_id"] and res["top_id"] in gold))
                in_corpus.append(res)

        print(f"set B, near-miss: {len(NEAR_MISS)} queries ...", flush=True)
        for lang, q in NEAR_MISS:
            res = await ask(client, args.url, q, lang)
            res.update(query=q, lang=lang)
            near_miss.append(res)

    answered_a = [r for r in in_corpus if r["score"] is not None]
    answered_b = [r for r in near_miss if r["score"] is not None]

    print("\n" + "=" * 92)
    print(f"{'floor':>7} | {'in-corpus kept':>15} {'precision':>10} | {'near-miss refused':>18} | {'net':>22}")
    print("-" * 92)
    sweep = []
    for T in FLOORS:
        kept = [r for r in answered_a if r["score"] >= T]
        hits = sum(1 for r in kept if r["gold_hit"])
        prec = hits / len(kept) if kept else 0.0
        cov = len(kept) / len(answered_a) if answered_a else 0.0
        refused_b = [r for r in answered_b if r["score"] < T]
        ref = len(refused_b) / len(answered_b) if answered_b else 0.0
        sweep.append({"floor": T, "coverage": round(cov, 4), "precision": round(prec, 4),
                      "near_miss_refused": round(ref, 4),
                      "in_corpus_kept": len(kept), "near_miss_refused_n": len(refused_b)})
        print(f"{T:>7.2f} | {len(kept):>6} ({cov * 100:>5.1f}%) {prec * 100:>9.1f}% | "
              f"{len(refused_b):>3} of {len(answered_b)} ({ref * 100:>5.1f}%) | "
              f"{'<-- shipped' if abs(T + 1.103) < 1e-6 else ''}")
    print("=" * 92)

    a_scores = np.array([r["score"] for r in answered_a], dtype=float)
    b_scores = np.array([r["score"] for r in answered_b], dtype=float)
    print(f"\nin-corpus score  P10 {np.percentile(a_scores, 10):.2f}  P25 {np.percentile(a_scores, 25):.2f}  "
          f"P50 {np.percentile(a_scores, 50):.2f}  P75 {np.percentile(a_scores, 75):.2f}")
    print(f"near-miss score  P10 {np.percentile(b_scores, 10):.2f}  P25 {np.percentile(b_scores, 25):.2f}  "
          f"P50 {np.percentile(b_scores, 50):.2f}  P75 {np.percentile(b_scores, 75):.2f}  "
          f"MAX {b_scores.max():.2f}")

    mism = [r for r in answered_a if r["top_lang"] and r["top_lang"] != r["lang"]]
    print(f"\nlanguage mismatches in set A: {len(mism)} of {len(answered_a)} "
          f"({len(mism) / len(answered_a) * 100:.1f}%)")

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "what": "abstention floor sweep against in-corpus and near-miss questions",
        "url": args.url,
        "note": args.note,
        "n_in_corpus": len(answered_a),
        "n_near_miss": len(answered_b),
        "sweep": sweep,
        "language_mismatch_in_corpus": len(mism),
        # The raw rows are kept, not just the summary. Every threshold question
        # asked later ("what if the floor were 3.5") is answerable from these
        # without paying for another 560 requests against the live service.
        "in_corpus": in_corpus,
        "near_miss": sorted(near_miss, key=lambda r: -(r["score"] or -99)),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    path = RESULTS / f"{stamp}-relevance-floor-{args.label}.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {path}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--label", default="sweep")
    p.add_argument("--note", default="")
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
