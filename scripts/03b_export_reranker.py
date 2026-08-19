"""Fetch the ONNX cross-encoder rerankers and gate them on real reranking. Phase 5.

    python scripts/03b_export_reranker.py
    python scripts/03b_export_reranker.py --models mono          # English only
    python scripts/03b_export_reranker.py --skip-parity          # fetch, no gate

OWNED BY BENCH.

Why this fetches instead of exporting:
    Phases.md says "export and quantize ms-marco-MiniLM-L-6-v2 to ONNX int8". Both
    candidate models already publish ONNX on the Hub including an int8 build
    quantized for AVX512-VNNI - the identical situation 03_export_onnx.py found for
    the embedder. Exporting ourselves means installing torch (~2GB) and optimum to
    reproduce an artifact the author already ships. Same decision, same reason.

Why TWO models are fetched:
    Rules.md 3.3 lists the reranker as SOFT - "benchmark before deviating". The
    default there is cross-encoder/ms-marco-MiniLM-L-6-v2, which is an ENGLISH-ONLY
    BERT (22.7M params, 30k WordPiece vocab). Half of this corpus is Hindi.

    The alternative is cross-encoder/mmarco-mMiniLMv2-L12-H384-v1: XLM-R based,
    trained on mMARCO (MS MARCO machine-translated into 13 languages), with Hindi
    explicitly among them. Our corpus is MSMARCO-XI - MS MARCO machine-translated
    into Indic languages. The training distribution and the corpus are the same
    construction.

    That is an argument, not a measurement, so this script measures it. Both models
    are scored on the same candidate lists in both languages and the choice is made
    on the numbers. Cost of asking the question properly: one extra 23 MB download.

The parity gate:
    Same shape as 03_export_onnx.py and for the same reason. int8 quantization is
    lossy in two directions: a silently-fp32 model looks correct and is 4x too slow,
    and a genuinely-quantized model that lost too much accuracy reranks badly while
    every latency number looks excellent.

    Measured on RERANKING, not on raw logit agreement. A cross-encoder's absolute
    logit is uncalibrated and drifts freely under quantization; what the pipeline
    consumes is the ORDER it induces over a candidate list, and - in Phase 6 - the
    top-1 score as a confidence signal. So the gate scores both builds over real
    dense-retrieved candidates against gold and compares Hit@1, plus the rank
    correlation of the orderings they produce.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.config import (  # noqa: E402
    INT8_MODEL,
    ONNX_DIR,
    ONNX_THREADS_SERVING,
    QUERIES_PARQUET,
    RERANKERS,
    TOKENIZER_FILE,
    load_env,
)
from rag_core.retrieval.dense import DenseIndex  # noqa: E402
from rag_core.retrieval.embedder import Embedder  # noqa: E402

load_env()

FP32_FILE = "onnx/model.onnx"
INT8_FILE = "onnx/model_qint8_avx512_vnni.onnx"
RERANK_TOKENIZER = "tokenizer.json"

PARITY_QUERIES = 150
CANDIDATE_DEPTH = 20

# int8 may not lose more than this much Hit@1 against fp32 on real reranking.
MAX_HIT1_DROP = 0.02

# ...and it must reproduce most of fp32's ordering. Kendall tau over the candidate
# list, averaged. Ranking is what the model is FOR, so this is the direct test.
#
# What calibrated this number (Rules.md 6), because Phase 2 was burned by exactly
# the opposite case. There, a neighbour-overlap gate failed at 0.866 and the
# diagnosis was that the int8 perturbation (~0.004 cosine) EXCEEDED the rank-10 to
# rank-11 gap (0.00137) - the gate was measuring noise and was replaced.
#
# The same diagnostic was run here before trusting this gate. Median |fp32 - int8|
# logit perturbation against the median adjacent-rank logit gap, 60 queries x 2
# languages x 20 candidates:
#
#     model    perturb   adj gap   ratio
#     mono      0.0840    0.0901    0.93
#     multi     0.2104    0.3637    0.58
#
# Both ratios are under 1.0, so unlike the Phase 2 case the ordering here is real
# signal and tau is a legitimate gate. The ratios also explain the tau results
# rather than merely accompanying them: mono's logit scale is 4x more compressed,
# so identical quantization error does proportionally more damage to its ordering,
# and it lands at tau 0.877 where multi reaches 0.938. 0.90 sits between them by
# construction - it is the line that admits a model with quantization headroom and
# rejects one without. mono fails it, and independently fails the language
# comparison, so nothing rests on the exact placement.
MIN_MEAN_TAU = 0.90


def fetch(repo: str, filename: str, subdir: str) -> Path:
    local = hf_hub_download(
        repo_id=repo, filename=filename, local_dir=ONNX_DIR / subdir
    )
    path = Path(local)
    print(f"  {filename:<40} {path.stat().st_size / 1_048_576:>7.1f} MB")
    return path


def _kendall_tau(a: np.ndarray, b: np.ndarray) -> float:
    """Kendall tau-a between two score vectors over the same items.

    Hand-rolled rather than scipy.stats.kendalltau because the lists are 20 items
    and this avoids a scipy import in a script that otherwise needs none. Ties are
    counted as discordant, which is the conservative direction for a gate.
    """
    n = len(a)
    if n < 2:
        return 1.0
    concordant = 0
    total = 0
    for i in range(n):
        for j in range(i + 1, n):
            total += 1
            if (a[i] - a[j]) * (b[i] - b[j]) > 0:
                concordant += 1
    return (2.0 * concordant / total) - 1.0


def build_candidates(
    queries: list[dict], depth: int
) -> tuple[list[list[str]], list[list[str]], list[set[str]], list[str]]:
    """Retrieve real dense candidates, so the gate scores what the pipeline scores.

    A reranker gate built on random passages measures nothing: the whole job of a
    cross-encoder is to reorder a list that a bi-encoder already thinks is roughly
    right. Handing it easy negatives makes any model look perfect.

    Returns (candidate_texts, candidate_ids, gold_sets, langs) flattened over both
    languages, so English and Hindi are scored by exactly the same code path.
    """
    embedder = Embedder(
        ONNX_DIR / INT8_MODEL, ONNX_DIR / TOKENIZER_FILE, threads=ONNX_THREADS_SERVING
    )
    index = DenseIndex("c1")
    index.load()

    passages = pq.read_table(
        Path(QUERIES_PARQUET).parent / "passages.parquet",
        columns=["passage_id", "text"],
    ).to_pylist()
    text_by_id = {p["passage_id"]: p["text"] for p in passages}

    texts: list[list[str]] = []
    ids: list[list[str]] = []
    golds: list[set[str]] = []
    langs: list[str] = []

    for lang in ("en", "hi"):
        qfield = "query_en" if lang == "en" else "query_hi"
        gfield = "gold_en_ids" if lang == "en" else "gold_hi_ids"
        for q in queries:
            vec = embedder.encode_one(q[qfield], "query")
            seen: set[str] = set()
            distinct: list[str] = []
            for row, _ in index.search(vec, depth * 4):
                pid = index.chunk(row)["passage_id"]
                if pid not in seen:
                    seen.add(pid)
                    distinct.append(pid)
                if len(distinct) >= depth:
                    break
            texts.append([text_by_id[p] for p in distinct])
            ids.append(distinct)
            golds.append(set(q[gfield]))
            langs.append(lang)

    return texts, ids, golds, langs


def score_all(
    reranker: object, queries: list[str], cand_texts: list[list[str]]
) -> list[np.ndarray]:
    out = []
    for q, cands in zip(queries, cand_texts):
        out.append(reranker.score(q, cands))  # type: ignore[attr-defined]
    return out


def hit1(
    scores: list[np.ndarray], cand_ids: list[list[str]], golds: list[set[str]]
) -> float:
    hits = scored = 0
    for s, ids, gold in zip(scores, cand_ids, golds):
        if not gold:
            continue
        scored += 1
        if ids[int(np.argmax(s))] in gold:
            hits += 1
    return hits / max(scored, 1)


def parity(key: str, repo: str, fp32_path: Path, int8_path: Path, tok: Path) -> int:
    from rag_core.retrieval.rerank import CrossEncoder

    rows = [
        q
        for q in pq.read_table(QUERIES_PARQUET).to_pylist()
        if q["split"] == "dev" and q["gold_en_ids"] and q["gold_hi_ids"]
    ][:PARITY_QUERIES]

    print("")
    print(f"  parity: {len(rows)} dev queries x 2 languages, {CANDIDATE_DEPTH} real dense candidates")
    cand_texts, cand_ids, golds, langs = build_candidates(rows, CANDIDATE_DEPTH)
    flat_queries = [q["query_en"] for q in rows] + [q["query_hi"] for q in rows]

    results = {}
    for name, path in (("fp32", fp32_path), ("int8", int8_path)):
        ce = CrossEncoder(path, tok, threads=4)
        t0 = time.perf_counter()
        results[name] = score_all(ce, flat_queries, cand_texts)
        print(f"  scored {name:<5} in {time.perf_counter() - t0:>6.1f}s")

    taus = [
        _kendall_tau(a, b) for a, b in zip(results["fp32"], results["int8"])
    ]
    mean_tau = float(np.mean(taus))

    print("")
    print(f"  {'':<6}{'Hit@1 en':>11}{'Hit@1 hi':>11}")
    per_model = {}
    for name in ("fp32", "int8"):
        by_lang = {}
        for lang in ("en", "hi"):
            sel = [i for i, l in enumerate(langs) if l == lang]
            by_lang[lang] = hit1(
                [results[name][i] for i in sel],
                [cand_ids[i] for i in sel],
                [golds[i] for i in sel],
            )
        per_model[name] = by_lang
        print(f"  {name:<6}{by_lang['en']:>11.3f}{by_lang['hi']:>11.3f}")

    print("")
    print(f"  kendall tau(fp32, int8) over candidate orderings: mean {mean_tau:.3f}")

    ok = True
    drop_en = per_model["fp32"]["en"] - per_model["int8"]["en"]
    drop_hi = per_model["fp32"]["hi"] - per_model["int8"]["hi"]
    for lang, drop in (("en", drop_en), ("hi", drop_hi)):
        if drop > MAX_HIT1_DROP:
            print(f"  FAIL int8 loses {drop:.3f} Hit@1 on {lang}, over the {MAX_HIT1_DROP} budget")
            ok = False
    if mean_tau < MIN_MEAN_TAU:
        print(f"  FAIL mean tau {mean_tau:.3f} < {MIN_MEAN_TAU}: int8 reorders too freely")
        ok = False

    print("")
    if not ok:
        print(f"  {key}: fall back to fp32 and re-bench, or drop this model.")
        return 1
    print(
        f"  PASS int8 matches fp32 on reranking "
        f"(Hit@1 delta en {-drop_en:+.3f} / hi {-drop_hi:+.3f}, tau {mean_tau:.3f})."
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--models",
        default="all",
        help="comma list of keys from config.RERANKERS, or 'all'",
    )
    ap.add_argument("--skip-parity", action="store_true")
    args = ap.parse_args()

    keys = list(RERANKERS) if args.models == "all" else args.models.split(",")

    rc = 0
    for key in keys:
        spec = RERANKERS[key]
        subdir = f"rerank-{key}"
        print("")
        print(f"  [{key}] {spec['repo']}  ({spec['note']})")
        fp32 = fetch(spec["repo"], FP32_FILE, subdir)
        int8 = fetch(spec["repo"], INT8_FILE, subdir)
        tok = fetch(spec["repo"], RERANK_TOKENIZER, subdir)

        int8_mb = int8.stat().st_size / 1_048_576
        expected = float(spec["int8_mb"])
        if abs(int8_mb - expected) > 0.15 * expected:
            print("")
            print(f"  WARNING int8 file is {int8_mb:.0f} MB, expected ~{expected:.0f} MB.")
            print("  Latency.md 8: a silently-fp32 model is a common and expensive mistake.")

        if not args.skip_parity:
            rc |= parity(key, spec["repo"], fp32, int8, tok)

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
