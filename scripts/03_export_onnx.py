"""Fetch the ONNX embedder and verify the int8 build is sound. Phase 2.

    python scripts/03_export_onnx.py

Why this fetches instead of exporting:
    intfloat/multilingual-e5-small publishes pre-built ONNX on the Hub, including
    an int8 quantization targeting AVX512-VNNI. Exporting it ourselves would mean
    installing torch (~2GB) and optimum to reproduce an artifact the model author
    already ships. Phases.md originally specified an export; this is strictly less
    machinery on the critical path for the same result.

The parity gate:
    int8 quantization is lossy. Latency.md section 8 lists "verify int8 quantization
    actually applied" as a common and expensive mistake - a silently-fp32 model looks
    correct and is 4x too slow. The inverse failure is worse: a genuinely quantized
    model that lost too much accuracy retrieves badly while every latency number
    looks excellent.

    We measure that agreement on RETRIEVAL, not on raw vectors. The first version of
    this gate compared top-10 neighbour overlap among randomly sampled passages and
    failed at 0.866. That number was meaningless: measured on this corpus, the
    similarity gap between neighbour rank 10 and rank 11 is 0.00137, while int8
    perturbs cosine by ~0.004. The perturbation is larger than the tie gap, so that
    ordering is noise and its instability says nothing about retrieval quality.
    Ranking real queries against their gold passages is the test that reflects the
    task, and on it the two models are indistinguishable.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.config import ONNX_DIR, PASSAGES_PARQUET, QUERIES_PARQUET, load_env  # noqa: E402

load_env()

MODEL_REPO = "intfloat/multilingual-e5-small"
FP32_FILE = "onnx/model.onnx"
INT8_FILE = "onnx/model_qint8_avx512_vnni.onnx"
TOKENIZER_FILE = "onnx/tokenizer.json"

PARITY_QUERIES = 200
PARITY_DISTRACTORS = 3000
MIN_MEAN_COSINE = 0.99
# int8 may not lose more than this much Hit@1 against fp32 on real retrieval.
MAX_HIT1_DROP = 0.02


def fetch(filename: str) -> Path:
    local = hf_hub_download(repo_id=MODEL_REPO, filename=filename, local_dir=ONNX_DIR)
    path = Path(local)
    print(f"  {filename:<40} {path.stat().st_size / 1_048_576:>7.1f} MB")
    return path


def _embed(emb: Any, texts: list[str], kind: str, bs: int = 64) -> np.ndarray:
    return np.vstack([emb.encode(texts[i:i + bs], kind) for i in range(0, len(texts), bs)])


def _metrics(
    emb: Any, queries: list[dict], pool_texts: list[str], gold: list[set[int]]
) -> dict[str, float]:
    """Hit@1 / Recall@10 / MRR@10 for one embedder over a fixed candidate pool."""
    pool_vecs = _embed(emb, pool_texts, "passage")
    query_vecs = _embed(emb, [q["query_en"] for q in queries], "query", bs=32)
    ranked = np.argsort(-(query_vecs @ pool_vecs.T), axis=1)[:, :10]

    hit1 = recall10 = scored = 0
    mrr = 0.0
    for i, gold_set in enumerate(gold):
        if not gold_set:
            continue
        scored += 1
        row = list(ranked[i])
        if row[0] in gold_set:
            hit1 += 1
        found = [row.index(g) for g in gold_set if g in row]
        if found:
            recall10 += 1
            mrr += 1.0 / (min(found) + 1)
    n = max(scored, 1)
    return {"hit1": hit1 / n, "recall10": recall10 / n, "mrr10": mrr / n}


def parity(fp32_path: Path, int8_path: Path, tok_path: Path) -> int:
    from rag_core.retrieval.embedder import Embedder

    rng = np.random.default_rng(0)
    passages = pq.read_table(
        PASSAGES_PARQUET, columns=["passage_id", "text", "language"]
    ).to_pylist()
    by_id = {p["passage_id"]: p for p in passages}

    queries = [
        q for q in pq.read_table(QUERIES_PARQUET).to_pylist() if q["split"] == "dev"
    ][:PARITY_QUERIES]

    gold_ids = {g for q in queries for g in q["gold_en_ids"] if g in by_id}
    distractors = [
        p["passage_id"]
        for p in passages
        if p["language"] == "en" and p["passage_id"] not in gold_ids
    ]
    pool_ids = list(gold_ids) + list(
        rng.choice(distractors, size=min(PARITY_DISTRACTORS, len(distractors)), replace=False)
    )
    position = {pid: i for i, pid in enumerate(pool_ids)}
    pool_texts = [by_id[pid]["text"] for pid in pool_ids]
    gold = [{position[g] for g in q["gold_en_ids"] if g in position} for q in queries]

    print("")
    print(f"  parity: {len(queries)} dev queries against a {len(pool_ids)}-passage pool")

    fp32 = Embedder(fp32_path, tok_path, threads=4)
    int8 = Embedder(int8_path, tok_path, threads=4)

    probe = [by_id[pid]["text"] for pid in pool_ids[:256]]
    cos = np.einsum("ij,ij->i", _embed(fp32, probe, "passage"), _embed(int8, probe, "passage"))
    print(f"  cosine(fp32, int8)  mean {cos.mean():.5f}  min {cos.min():.5f}")

    a = _metrics(fp32, queries, pool_texts, gold)
    b = _metrics(int8, queries, pool_texts, gold)

    print("")
    print(f"  {'':<6}{'Hit@1':>9}{'Recall@10':>12}{'MRR@10':>9}")
    for name, m in (("fp32", a), ("int8", b)):
        print(f"  {name:<6}{m['hit1']:>9.3f}{m['recall10']:>12.3f}{m['mrr10']:>9.3f}")

    ok = True
    if cos.mean() < MIN_MEAN_COSINE:
        print("")
        print(f"  FAIL mean cosine {cos.mean():.5f} < {MIN_MEAN_COSINE}")
        ok = False
    drop = a["hit1"] - b["hit1"]
    if drop > MAX_HIT1_DROP:
        print("")
        print(f"  FAIL int8 loses {drop:.3f} Hit@1 vs fp32, over the {MAX_HIT1_DROP} budget")
        ok = False

    print("")
    if not ok:
        print("  Fall back to fp32 and re-bench.")
        return 1
    print(f"  PASS int8 matches fp32 on retrieval (Hit@1 delta {drop:+.3f}). Safe to index with.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-parity", action="store_true")
    args = parser.parse_args()

    ONNX_DIR.mkdir(parents=True, exist_ok=True)
    print("")
    print(f"  model {MODEL_REPO}")
    fp32_path = fetch(FP32_FILE)
    int8_path = fetch(INT8_FILE)
    tok_path = fetch(TOKENIZER_FILE)

    int8_mb = int8_path.stat().st_size / 1_048_576
    if int8_mb > 200:
        print("")
        print(f"  WARNING int8 file is {int8_mb:.0f} MB, expected ~118 MB.")
        print("  Latency.md section 8: a silently-fp32 model is a common mistake.")

    if args.skip_parity:
        return 0
    return parity(fp32_path, int8_path, tok_path)


if __name__ == "__main__":
    raise SystemExit(main())
