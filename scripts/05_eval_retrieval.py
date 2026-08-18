"""Retrieval correctness gate. Phase 2.

    python scripts/05_eval_retrieval.py

Why this exists in Phase 2 rather than Phase 3:
    Phase 2's exit criterion is a P50. A P50 produced by a retriever that returns
    garbage is not a good number, it is a meaningless one - and every failure mode
    of an e5 pipeline is silent. Omit the "query: "/"passage: " prefixes, or pool
    on CLS instead of a masked mean, and you get a fast service that confidently
    retrieves the wrong passage with no error anywhere.

    The corpus gives us the ground truth for free: MSMARCO-XI ships is_selected
    flags, which Phase 1 turned into gold_en_ids / gold_hi_ids per query. So the
    check costs almost nothing and is the only thing separating "fast" from "fast
    and correct".

    Phase 3 extends this to compare all eight chunking strategies. This is the
    single-strategy version.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.config import (  # noqa: E402
    DEFAULT_STRATEGY,
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


def evaluate(
    embedder: Embedder, index: DenseIndex, queries: list[dict], lang: str
) -> dict[str, float]:
    """Recall@10, MRR@10 and Hit@1 against gold passages for one query language.

    Retrieval returns chunks; gold is defined over passages. A hit is scored when
    the chunk's parent passage is gold, which is the right granularity - the user
    is shown the passage and the citation resolves to it.
    """
    field = "query_en" if lang == "en" else "query_hi"
    gold_field = "gold_en_ids" if lang == "en" else "gold_hi_ids"

    recall = hit1 = scored = 0
    mrr = 0.0
    per_type: dict[str, list[int]] = defaultdict(list)

    for q in queries:
        gold = set(q[gold_field])
        if not gold:
            continue
        scored += 1

        vec = embedder.encode_one(q[field], "query")
        rows = index.search(vec, K)
        passages = [index.chunk(r)["passage_id"] for r, _ in rows]

        if passages and passages[0] in gold:
            hit1 += 1
        found = [i for i, p in enumerate(passages) if p in gold]
        if found:
            recall += 1
            mrr += 1.0 / (found[0] + 1)
        per_type[q["query_type"]].append(1 if found else 0)

    n = max(scored, 1)
    return {
        "queries": scored,
        f"recall@{K}": recall / n,
        f"mrr@{K}": mrr / n,
        "hit@1": hit1 / n,
        "by_query_type": {t: round(float(np.mean(v)), 3) for t, v in sorted(per_type.items())},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--split", default="dev", choices=["dev", "test"])
    args = parser.parse_args()

    embedder = Embedder(
        ONNX_DIR / INT8_MODEL, ONNX_DIR / TOKENIZER_FILE, threads=ONNX_THREADS_BUILD
    )
    index = DenseIndex(args.strategy)
    index.load()

    queries = [
        q for q in pq.read_table(QUERIES_PARQUET).to_pylist() if q["split"] == args.split
    ][: args.limit]

    print("")
    print(f"  strategy {args.strategy}   chunks {len(index.chunks):,}")
    print(f"  {len(queries)} {args.split}-split queries, k={K}")

    results = {}
    for lang in ("en", "hi"):
        t0 = time.perf_counter()
        results[lang] = evaluate(embedder, index, queries, lang)
        results[lang]["seconds"] = round(time.perf_counter() - t0, 1)

    print("")
    print(f"  {'':<5}{'Recall@10':>11}{'MRR@10':>9}{'Hit@1':>8}")
    for lang, m in results.items():
        print(f"  {lang:<5}{m[f'recall@{K}']:>11.3f}{m[f'mrr@{K}']:>9.3f}{m['hit@1']:>8.3f}")

    print("")
    print("  recall@10 by query type")
    for lang, m in results.items():
        print(f"    {lang}: {m['by_query_type']}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    out = RESULTS_DIR / f"{stamp}-retrieval-{args.strategy}.json"
    out.write_text(
        json.dumps(
            {
                "strategy": args.strategy,
                "split": args.split,
                "k": K,
                "index_meta": index.meta,
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("")
    print(f"  wrote {out.name}")

    # A dense retriever on this corpus should be well clear of this. The gate is
    # deliberately loose: it catches "the embedder is broken", not "the embedder
    # is suboptimal", which is what Phase 3's comparison is for.
    floor = 0.50
    worst = min(m[f"recall@{K}"] for m in results.values())
    if worst < floor:
        print(f"  FAIL recall@{K} {worst:.3f} below {floor}. Check e5 prefixes and pooling.")
        return 1
    print(f"  PASS retrieval is sound (worst recall@{K} {worst:.3f}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
