"""Dense vs BM25 vs RRF fusion, per language. Job J12.

    python scripts/05b_eval_fusion.py

OWNED BY BENCH, like 05_eval_retrieval.py, which J15 will extend to nDCG and to
every strategy. This script exists separately and deliberately narrowly: it
answers one question that ISSUES.md I17 raised and that Phase 3 cannot defer -
does fusion narrow the en/hi retrieval gap, or inherit it?

**Methodology is identical to 05_eval_retrieval.py**, on purpose. That harness
takes the top-10 CHUNKS and maps them to passage ids without deduplicating, so a
passage contributing three chunks occupies three of the ten slots. Deduplicating
to ten distinct passages instead would search deeper for the same k and score
strictly higher. Both are defensible; mixing them in one table is not, and the
published dense numbers (en Recall@10 0.870) were measured the first way. See
ISSUES.md I6 for the last time a comparison was quietly made against two
different baselines.

All three retrievers are therefore scored through the same function here, over
the same queries, with the same k.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.config import (  # noqa: E402
    DENSE_TOP_K,
    DEFAULT_STRATEGY,
    INDEX_DIR,
    INT8_MODEL,
    LEXICAL_DIRNAME,
    LEXICAL_TOP_K,
    ONNX_DIR,
    ONNX_THREADS_BUILD,
    RESULTS_DIR,
    RRF_K,
    TOKENIZER_FILE,
)
from rag_core.retrieval.dense import DenseIndex  # noqa: E402
from rag_core.retrieval.embedder import Embedder  # noqa: E402
from rag_core.retrieval.fusion import reciprocal_rank_fusion  # noqa: E402
from rag_core.retrieval.lexical import BM25Index  # noqa: E402

K = 10

# Recall at the depths that actually matter architecturally. Recall@10 measures
# fusion as a final ranker, which is not its job: it feeds the cross-encoder,
# which reranks exactly 20 (Architecture.md 3.6). Whether gold is INSIDE that
# candidate set is the question - the reranker fixes order, it cannot recover a
# document that never arrived. 50 is included because it is what the retrievers
# return, and because it shows where fusion's contribution actually lands.
DEPTHS = (10, 20, 50)
RERANK_DEPTH = 20

BENCH_QUERIES = Path(__file__).resolve().parents[1] / "bench" / "queries_250.jsonl"


def score(rows_per_query: list[list[int]], golds: list[set[str]],
          passage_of_row: list[str]) -> dict[str, float]:
    """Recall@10, MRR@10, Hit@1 from top-K chunk rows, plus recall at each depth.

    Ranking metrics use the same rules as 05_eval_retrieval.py so the numbers are
    comparable to the published ones; the recall@depth series is what J12 is
    actually decided on.
    """
    recall = hit1 = 0
    mrr = 0.0
    at_depth = {d: 0 for d in DEPTHS}
    for rows, gold in zip(rows_per_query, golds):
        passages = [passage_of_row[r] for r in rows[:K]]
        if passages and passages[0] in gold:
            hit1 += 1
        found = [i for i, p in enumerate(passages) if p in gold]
        if found:
            recall += 1
            mrr += 1.0 / (found[0] + 1)
        for depth in DEPTHS:
            if gold & {passage_of_row[r] for r in rows[:depth]}:
                at_depth[depth] += 1
    n = max(len(golds), 1)
    return {
        f"recall@{K}": recall / n,
        f"mrr@{K}": mrr / n,
        "hit@1": hit1 / n,
        **{f"recall@{d}": at_depth[d] / n for d in DEPTHS},
    }


def main() -> int:
    queries = [json.loads(line) for line in
               BENCH_QUERIES.read_text(encoding="utf-8").splitlines()]

    embedder = Embedder(
        model_path=ONNX_DIR / INT8_MODEL,
        tokenizer_path=ONNX_DIR / TOKENIZER_FILE,
        threads=ONNX_THREADS_BUILD,
    )
    dense = DenseIndex(DEFAULT_STRATEGY)
    dense.load()
    lexical = BM25Index()
    lexical.load(INDEX_DIR / DEFAULT_STRATEGY / LEXICAL_DIRNAME)

    # The invariant fusion rests on. Cheap to check, catastrophic to assume.
    if lexical.n_chunks != len(dense.chunks):
        print(f"  ABORT row misalignment: dense {len(dense.chunks):,} chunks, "
              f"lexical {lexical.n_chunks:,}. Rebuild with 02b_build_lexical.py.")
        return 1

    passage_of_row = [c["passage_id"] for c in dense.chunks]
    print(f"  strategy   {DEFAULT_STRATEGY}   chunks {len(dense.chunks):,}   "
          f"aligned")
    print(f"  fusion     RRF k={RRF_K}, dense top-{DENSE_TOP_K} + "
          f"lexical top-{LEXICAL_TOP_K}")
    print(f"  queries    {len(queries)}   metric: top-{K} chunks -> passage ids, "
          f"no dedup (matches 05)")

    results: dict[str, dict[str, dict[str, float]]] = {}
    for lang in ("en", "hi"):
        field = f"query_{lang}"
        gold_field = f"gold_{lang}_ids"
        golds = [set(q[gold_field]) for q in queries]

        dense_rows: list[list[int]] = []
        lex_rows: list[list[int]] = []
        fused_rows: list[list[int]] = []
        t0 = time.perf_counter()
        for q in queries:
            vec = embedder.encode_one(q[field], "query")
            d = dense.search(vec, DENSE_TOP_K)
            l = lexical.search(q[field], LEXICAL_TOP_K)
            # Not truncated to K: the depth metrics need the full fused list.
            f = reciprocal_rank_fusion([d, l], k=RRF_K)
            dense_rows.append([r for r, _ in d])
            lex_rows.append([r for r, _ in l])
            fused_rows.append([r for r, _ in f])
        secs = time.perf_counter() - t0

        results[lang] = {
            "dense": score(dense_rows, golds, passage_of_row),
            "bm25": score(lex_rows, golds, passage_of_row),
            "fused": score(fused_rows, golds, passage_of_row),
        }
        print(f"  {lang} evaluated in {secs:.1f}s")

    print("")
    print("                 Recall@10    MRR@10     Hit@1")
    for retriever in ("dense", "bm25", "fused"):
        for lang in ("en", "hi"):
            m = results[lang][retriever]
            print(f"  {retriever:<6} {lang}    {m['recall@10']:>9.3f} "
                  f"{m['mrr@10']:>9.3f} {m['hit@1']:>9.3f}")

    print("")
    print("  recall at depth  (the reranker reads the top "
          f"{RERANK_DEPTH}, Architecture.md 3.6)")
    print("                 " + "".join(f"@{d:<9}" for d in DEPTHS))
    for retriever in ("dense", "bm25", "fused"):
        for lang in ("en", "hi"):
            m = results[lang][retriever]
            print(f"  {retriever:<6} {lang}    "
                  + "".join(f"{m[f'recall@{d}']:<10.3f}" for d in DEPTHS))

    print("")
    print("  en/hi gap on Recall@10  (ISSUES.md I17)")
    gaps = {}
    for retriever in ("dense", "bm25", "fused"):
        gap = (results["en"][retriever]["recall@10"]
               - results["hi"][retriever]["recall@10"])
        gaps[retriever] = gap
        print(f"    {retriever:<6} {gap:+.3f}")

    # The J12 question, answered against the depth the reranker actually reads
    # rather than against Recall@10, which measures fusion as a final ranker -
    # a job it does not have.
    print("")
    print(f"  fused vs dense at the depth that decides it (@{RERANK_DEPTH})")
    verdicts = []
    for lang in ("en", "hi"):
        delta = (results[lang]["fused"][f"recall@{RERANK_DEPTH}"]
                 - results[lang]["dense"][f"recall@{RERANK_DEPTH}"])
        verdicts.append(delta)
        print(f"    {lang}   {delta:+.3f}")
    if max(verdicts) <= 0.005:
        print(f"  VERDICT: fusion does NOT earn its place at @{RERANK_DEPTH}. "
              f"Its gain appears only at @50, which the reranker never reads.")
    else:
        print(f"  VERDICT: fusion improves the candidate set at @{RERANK_DEPTH}.")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    out = RESULTS_DIR / f"{stamp}-fusion-j12.json"
    out.write_text(json.dumps({
        "job": "J12",
        "strategy": DEFAULT_STRATEGY,
        "rrf_k": RRF_K,
        "dense_top_k": DENSE_TOP_K,
        "lexical_top_k": LEXICAL_TOP_K,
        "k": K,
        "queries": len(queries),
        "methodology": "top-K chunks mapped to passage ids, no dedup; matches 05_eval_retrieval.py",
        "results": results,
        "recall_gap_en_minus_hi": gaps,
    }, indent=2), encoding="utf-8")
    print(f"  wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
