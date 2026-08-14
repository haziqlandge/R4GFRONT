"""Central configuration. Rules.md section 6: no magic numbers in the hot path.

Every constant that a benchmark or a guardrail depends on lives here or in
guardrails/policies.yaml, never inline at a call site.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR: Final[Path] = REPO_ROOT / "artifacts"
RAW_DIR: Final[Path] = ARTIFACTS_DIR / "raw"
INDEX_DIR: Final[Path] = ARTIFACTS_DIR / "indexes"
ONNX_DIR: Final[Path] = ARTIFACTS_DIR / "onnx"
BENCH_DIR: Final[Path] = REPO_ROOT / "bench"
RESULTS_DIR: Final[Path] = BENCH_DIR / "results"

SLICE_MANIFEST: Final[Path] = ARTIFACTS_DIR / "slice_manifest.json"
PASSAGES_PARQUET: Final[Path] = ARTIFACTS_DIR / "passages.parquet"
QUERIES_PARQUET: Final[Path] = ARTIFACTS_DIR / "queries.parquet"

# --------------------------------------------------------------------------
# Environment
# --------------------------------------------------------------------------

ENV_FILE: Final[Path] = REPO_ROOT / ".env"


def load_env(path: Path = ENV_FILE, override: bool = False) -> list[str]:
    """Load .env into os.environ. Returns the names of the keys it set.

    Deliberately dependency-free rather than pulling in python-dotenv: this runs
    in offline scripts and in service startup, and the parsing needed is trivial.

    Real environment variables win by default (override=False), because in
    production the keys arrive as platform secrets and there is no .env file at
    all. Rules.md section 4: keys live only in services/, never in the browser.
    """
    if not path.exists():
        return []
    loaded: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # Tolerate KEY= "value" / KEY='value'; a quote read as part of a secret
        # produces a 401 that looks like a bad key rather than a bad file.
        value = value.strip().strip('"').strip("'")
        if not value:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


# HTTP client identity. Groq's edge returns 403 with Cloudflare error code 1010
# to default urllib/httpx User-Agents; it presents as an auth failure and is not
# one. Every outbound client sets this.
USER_AGENT: Final[str] = "ok4t-voice-rag/0.1"

# --------------------------------------------------------------------------
# Corpus slice. Rules.md section 5: frozen in Phase 1, never silently changed.
# Changing any value here invalidates every number in bench/results/.
# --------------------------------------------------------------------------

DATASET_REPO: Final[str] = "ai4bharat/MSMARCO-XI"

# The repo's loader script (ms_marco_translations.py) points at .jsonl paths that
# no longer exist; the repo now holds .parquet. load_dataset() therefore fails and
# the HF dataset viewer returns "500 dataset generation failed". We download the
# parquet files directly. See Memory.md, Phase 1 entry.
SOURCE_FILES: Final[tuple[str, ...]] = ("validation/hinval.parquet",)

# Hindi file carries parallel English_passages and Translated_passages for the same
# passages, so English comes free from the single download.
SLICE_LANGUAGES: Final[tuple[str, ...]] = ("en", "hi")
SCRIPT_BY_LANGUAGE: Final[dict[str, str]] = {"en": "Latn", "hi": "Deva"}

SEED: Final[int] = 20260814
SLICE_QUERY_COUNT: Final[int] = 15_000

# Disjoint query partitions. All 15k queries' passages are indexed; the partition
# governs which queries may be *tuned against*, not what is retrievable.
TEST_QUERY_COUNT: Final[int] = 1_000  # frozen; source of bench/queries_250.jsonl
DEV_QUERY_COUNT: Final[int] = 2_000  # Phase 5 threshold calibration only
BENCH_QUERY_COUNT: Final[int] = 250  # the published benchmark set

# --------------------------------------------------------------------------
# Latency contract. Latency.md section 4.
# --------------------------------------------------------------------------

BUDGET_MS: Final[float] = 200.0

# Per-stage allocation, hard timeout. Allocated total is 175ms; the remaining 25ms
# is reserve for GC pauses and scheduler jitter that show up at P99/P100.
STAGE_BUDGET_MS: Final[dict[str, float]] = {
    "input_guard": 12.0,
    "embed_query": 25.0,
    "dense_search": 15.0,
    "lexical_search": 10.0,
    "fuse": 3.0,
    "rerank": 60.0,
    "route": 2.0,
    "answer_extractive": 15.0,
    "output_guard": 25.0,
    "serialize": 8.0,
}

STAGE_TIMEOUT_MS: Final[dict[str, float]] = {
    "input_guard": 15.0,
    "embed_query": 30.0,
    "dense_search": 20.0,
    "lexical_search": 12.0,
    "fuse": 5.0,
    "rerank": 70.0,
    "route": 3.0,
    "answer_extractive": 20.0,
    "output_guard": 30.0,
    "serialize": 10.0,
}

# --------------------------------------------------------------------------
# Benchmark methodology. Latency.md section 6.
# --------------------------------------------------------------------------

WARMUP_RUNS: Final[int] = 30  # discarded; cold ONNX/HNSW runs inflate P100
BENCH_PASSES: Final[int] = 5  # independent passes over the full query set
PERCENTILES: Final[tuple[int, ...]] = (50, 70, 90, 99, 100)
PERCENTILE_METHOD: Final[str] = "nearest"  # P100 is the true max, not the 99.9th
