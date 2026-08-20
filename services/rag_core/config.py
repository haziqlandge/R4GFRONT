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
# Embedder and index
# --------------------------------------------------------------------------

EMBED_MODEL_REPO: Final[str] = "intfloat/multilingual-e5-small"
INT8_MODEL: Final[str] = "onnx/model_qint8_avx512_vnni.onnx"
FP32_MODEL: Final[str] = "onnx/model.onnx"
TOKENIZER_FILE: Final[str] = "onnx/tokenizer.json"
EMBED_DIM: Final[int] = 384

# --------------------------------------------------------------------------
# Reranker. Architecture.md 3.6, Phase 5.
# --------------------------------------------------------------------------

# Rules.md 3.3 lists the reranker as SOFT - "benchmark before deviating" - with
# ms-marco-MiniLM-L-6-v2 as the default. Both candidates are declared here so the
# choice is made on measured en+hi numbers rather than on the default, because
# half this corpus is Hindi and the default is an English-only BERT.
#
# Both publish pre-built ONNX including an AVX512-VNNI int8 quantization, so
# neither needs torch or optimum - the same situation 03_export_onnx.py found for
# the embedder.
RERANKERS: Final[dict[str, dict[str, object]]] = {
    "mono": {
        "repo": "cross-encoder/ms-marco-MiniLM-L-6-v2",
        "int8_mb": 22.1,
        "note": "English-only BERT, 22.7M params, 6 layers",
    },
    "multi": {
        "repo": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
        "int8_mb": 113.1,
        "note": "XLM-R, mMARCO-trained, Hindi supported, 12 layers x 384",
    },
}

# Set by the Phase 5 comparison. See Memory.md.
RERANKER: Final[str] = "multi"
RERANK_MODEL_FILE: Final[str] = "onnx/model_qint8_avx512_vnni.onnx"
RERANK_TOKENIZER_FILE: Final[str] = "tokenizer.json"

# Cross-encoder pair truncation. Query plus passage, jointly. C1 chunks are p50 72
# tokens and passages max at 205 words, so 256 covers the pair with headroom while
# keeping the quadratic attention cost bounded - the rerank stage's latency scales
# with sequence length squared, and this is the dial that matters most for it.
RERANK_MAX_TOKENS: Final[int] = 256

# Candidates reranked per query. Architecture.md 3.6 specifies top-20; measurement
# says 5, and the doc is corrected rather than the number bent to it.
#
# Quality (300 dev queries, paired, vs the same candidates in dense order):
#     depth   en Hit@1   hi Hit@1
#         5      0.393      0.307
#        10      0.397      0.313
#        20      0.410      0.290   (batch-16 run)
#        50      0.410      0.280   (batch-16 run)
# Depth 5 and 10 are indistinguishable (+0.004 en, +0.006 hi) and quality FALLS by
# depth 50: reranking deeper gives the cross-encoder more chances to promote
# something above the right passage, and the dense ordering it overrides already
# carries real signal.
#
# Latency then decides (idle BENCH, 2 threads, batch 1):
#     depth 5   59.3 ms P50 / 102.4 ms P100
#     depth 10  113.8 ms P50 / 191.4 ms P100
# Depth 10 leaves no reserve at P100 against a 200 ms budget, and the deploy target
# n2-standard-2 is 2 vCPU = one physical core plus a hyperthread against this box's
# six cores. Depth 5 has room to survive that move; depth 10 does not.
RERANK_TOP_K: Final[int] = 5

# CORRECTED ON THE DEPLOYED BOX, 20 Aug. Depth 5 does not fit there.
#
# Everything above was measured on a six-core i5. On the n2-standard-4 the
# frozen 250 at depth 5 gives en P50 172.14 ms and hi P50 188.95 ms, with 16 and
# 71 queries over the 200 ms budget. The per-stage breakdown says why: rerank is
# 152.88 ms of a 169.75 ms total, about 90% of everything.
#
# Depth 3, same box, same 250 queries:
#     en  P50 137.39  P70 145.69  P90 158.19  P100 215.84   1/250 over budget
#     hi  P50 149.19  P70 158.93  P90 177.62  P100 221.67   5/249 over budget
#
# The quality cost, measured rather than assumed (300 dev queries, paired, the
# same candidate lists, 4000 resamples):
#
#     arm      en Hit@1  hi Hit@1   en MRR  hi MRR  en nDCG
#     dense       0.360     0.233    0.516   0.369    0.572
#     depth 3     0.400     0.290    0.512   0.375    0.539
#     depth 5     0.390     0.307    0.537   0.421    0.588
#
# Read that carefully, because it is not the trade anyone expected. Depth 3 is
# BETTER on English Hit@1 and worse on Hindi, and both differences are small
# with heavily overlapping intervals - on top-1 the two depths are barely
# distinguishable. Where depth 5 genuinely wins is MRR and nDCG, which is the
# ordering BELOW rank one.
#
# That maps onto the product. The extractive path returns top-1 and nothing
# else, so Hit@1 decides whether the answer is right; MRR and nDCG decide the
# order of citations two and three. Trading citation ordering for 35 ms and a
# true 200 ms claim on the machine this actually runs on is the right way round.
#
# To revert: set this to 5 and expect en P50 ~172 ms, hi ~189 ms, and the
# headline claim to be false on the deployed box.

# AND THEN REVERTED, THE SAME DAY, BECAUSE THE PREMISE WAS WRONG.
#
# The block above is kept rather than deleted: it is a correct reading of a real
# measurement, and the measurement was of a bug. Depth 5 cost 161 ms on that box
# because the embedder's ONNX thread pool was spinning on cores the cross-encoder
# needed (see ONNX_THREADS_EMBED_SERVING below). One thread for the embedder took
# the same depth 5 to 97 ms without touching the depth at all.
#
# Re-measured through the deployed service afterwards, 250 frozen queries x 2
# passes per language, 30 warmup discarded
# (bench/results/2026-08-20-135850-banda-deployed-n2std4-d5-final.json):
#
#            depth 3                    depth 5
#     en   P50  64.48  P100 122.91   P50 102.48  P100 181.34
#     hi   P50  75.88  P100 147.88   P50 120.66  P100 182.33
#     over 200 ms: 0 of 998         over 200 ms: 0 of 998
#
# Both fit. Depth 5 is the better answer on the numbers the previous block
# already established - hi Hit@1 0.307 against 0.290, and clearly better MRR and
# nDCG, which order citations two and three. Nothing was gained by staying at 3
# except margin, and the margin is now held by a mechanism instead: the rerank
# deadline stops before a pair it cannot afford (retrieval/rerank.py) rather than
# after one, which is what removed the last 8 over-budget queries.
#
# The lesson is the one Rules.md 5 keeps making: a lever pulled against a number
# nobody explained buys the wrong thing. Depth was traded for latency that a
# session option gave back for free.

# Wall clock held back from the reranker's deadline for the work that must still
# happen after it: the argsort, routing, building the answer and serializing. Those
# measure well under a millisecond combined (answer_extractive was 0.03 ms in Phase
# 2); 10 ms is deliberate slack, because overrunning the budget to save 9 ms of
# reranking would be a bad trade.
RERANK_DEADLINE_MARGIN_MS: Final[float] = 10.0

# Architecture.md 3.3. ef_search is the primary latency dial and is tuned against
# the budget in Phase 5; lower trades recall for speed close to linearly.
HNSW_M: Final[int] = 32
HNSW_EF_CONSTRUCTION: Final[int] = 200
HNSW_EF_SEARCH: Final[int] = 64

DENSE_TOP_K: Final[int] = 50
DEFAULT_STRATEGY: Final[str] = "c1"

# --------------------------------------------------------------------------
# Lexical index. Architecture.md 3.4.
# --------------------------------------------------------------------------

# Textbook BM25 defaults, and deliberately not tuned. Tuning k1/b against the
# bench slice would fit the published number to the test set; if they ever move
# it must be against the dev partition, per Rules.md 5.
BM25_K1: Final[float] = 1.5
BM25_B: Final[float] = 0.75
BM25_METHOD: Final[str] = "lucene"

LEXICAL_TOP_K: Final[int] = 50  # matches DENSE_TOP_K; RRF fuses equal-depth lists

# Lives beside the dense index it is row-aligned with, not in a directory of
# its own - the two are only meaningful together. See lexical.py.
LEXICAL_DIRNAME: Final[str] = "bm25"

# --------------------------------------------------------------------------
# Fusion. Architecture.md 3.5.
# --------------------------------------------------------------------------

# Cormack et al.'s original constant, and the standard default. It damps the
# influence of the top ranks: with k=60 the gap between rank 1 and rank 2 is
# small, so a single retriever cannot dominate the fused order on confidence
# alone. Lowering it makes fusion behave more like "trust whoever ranked it
# first". Not tuned here - tuning it against the bench slice would fit the
# published number to the test set (Rules.md 5).
RRF_K: Final[int] = 60

FUSED_TOP_K: Final[int] = 50  # what fusion hands to the reranker in Phase 5

# Rules.md 2.2: set explicitly, never left to the ONNX Runtime default.
#
# BUILD = 8. Measured on an i5-12400F (6 physical / 12 logical) against real C1
# chunk texts: 8 threads 213.0 chunks/sec, 12 threads 208.7 (-2.0%), 16 threads 61.
# A synthetic sweep with uniformly short strings showed 12 threads 11.6% FASTER and
# would have moved this the wrong way. Real chunks are p50 72 tokens, long enough
# that 8 threads already saturate 6 physical cores. See ISSUES.md I6.
#
# SERVING = 4, and this number tracks the deploy target rather than this box.
#
# It was 2 for a 2-vCPU n2-standard-2. Deploying to that box on 20 Aug and
# measuring the frozen 250 through it gave en P50 190.47 ms and hi P50 200.87 ms
# against a 200 ms budget, roughly 3x the development machine and over the line
# (ISSUES.md I8). The instance was resized to n2-standard-4 and this moved with
# it, because ONNX Runtime is told its thread count explicitly (Rules.md 2.2)
# and leaving it at 2 would have bought half the cores and none of the benefit.
#
# The local optimum is still 6 (1.97ms vs 2.49ms at 2) and is still not what
# goes here: the number that matters is the one the deployed box can use. 12
# threads showed a P99 of 15.58ms from hyperthread contention, so this is not a
# free dial to turn up - see ISSUES.md I6.
#
# SERVING = 2 SINCE 20 AUG, AND IT DID NOT COST A MILLISECOND.
#
# The paragraph above reasons that a thread count should track the deploy target's
# vCPUs. Measurement says the cross-encoder stops scaling at two. Per pair on the
# deployed box, 120 real pairs each (scripts/07b_rerank_sweep.py,
# bench/results/2026-08-20-133814-rerank-sweep-threads-n2std4.json):
#
#     threads   P50     P90     P100
#         1    26.59   38.46   74.45
#         2    17.78   24.15   43.90
#         3    20.06   27.26   48.23
#         4    17.88   25.35   41.93
#
# Two threads and four threads are the same number. A 12-layer 384-hidden encoder
# over a ~120-token pair simply does not have four threads of work in it, and the
# third and fourth workers spend their time synchronising.
#
# That turns spare vCPUs into a THROUGHPUT dial instead of a latency one. Every
# Band A stage is synchronous ONNX or C++ that never yields, so one uvicorn
# process serves exactly one request at a time no matter how many cores it has -
# measured directly: at four concurrent clients on the 4-vCPU box, per-request
# Band A was unchanged at P50 101 ms while client wall clock went to P50 418 and
# P100 698. That is a queue, and it is the number a judge with a browser sees.
#
# So the deployment runs `uvicorn --workers N` with N = vCPUs / 2, and this stays
# at 2. On the 8-vCPU box that is 4 workers, and at four concurrent clients wall
# clock falls from 698 ms P100 to 416 while Band A stays inside budget. The
# worker count lives in deploy/etc/shruti-core.service; it is a deployment shape,
# not a model parameter, so it does not belong in this file - but the two numbers
# have to be chosen together, which is why it is written down here.
ONNX_THREADS_SERVING: Final[int] = 2
ONNX_THREADS_BUILD: Final[int] = 8

# THE EMBEDDER DOES NOT GET THE SAME NUMBER, AND THAT IS THE WHOLE POINT.
#
# `rag_core` holds two ONNX sessions and a request uses both: the embedder runs
# first, the cross-encoder second. Each session owns its own intra-op thread
# pool, and ORT's pool SPINS after finishing a task instead of sleeping at once,
# so the embedder's workers were still burning cores while the reranker ran.
# Giving both sessions 4 threads is 8 spinning workers on 4 vCPUs.
#
# Measured on the deployed n2-standard-4, same pairs, same order
# (scripts/07c_ort_contention.py, bench/results/2026-08-20-134018-ort-n2std4.json
# and -134156-ort-n2std4-d5.json). Figures are the rerank stage, not the request:
#
#     arm                              depth 3 P50   depth 5 P50   embed P50
#     reranker alone, no embedder          57.27         97.20          -
#     embedder 4 threads (what shipped)   122.52        161.31        14.29
#     embedder 4 threads, spinning off     73.83        121.87         8.69
#     embedder 2 threads                       -       139.13        12.19
#     EMBEDDER 1 THREAD                    58.90         97.27         8.40
#     embedder 1 thread, spinning off     (n/a)         122.64         7.13
#
# One thread for the embedder recovers the standalone reranker cost exactly, and
# the embedder itself gets FASTER doing it - 14.29 ms to 8.40 - because it stops
# fighting its own oversubscribed pool on a 4-vCPU box. That is the rare dial
# that costs nothing on either side.
#
# Disabling spinning is NOT the fix, though it looked like one: it helps the
# session that is being starved and hurts the reranker, whose own pool spins
# productively between the pairs of one rerank call. Arm E is worse than arm D
# at both depths. Leave `session.intra_op.allow_spinning` at its default.
#
# This is the same shape of finding as ISSUES.md I6 - more threads is not more
# speed - but at the level of two sessions competing rather than one session
# oversubscribing. Latency.md 8 lever 4 says to pin threads and measure; it did
# not anticipate that the two sessions have to be tuned against each other.
#
# A query embeds in 8.4 ms on one thread. There is nothing to reclaim by raising
# this, and everything to lose: the reranker is ~85% of the budget.
ONNX_THREADS_EMBED_SERVING: Final[int] = 1

# --------------------------------------------------------------------------
# Guardrails, layer 1. Architecture.md 7, guardrails/input_guard.py. Phase 6.
# --------------------------------------------------------------------------

# What calibrated these (Rules.md 6): ISSUES.md I1, measured on the frozen 250
# query benchmark.
#
# The problem. One query, `query_id=156297`, is 7,168 characters of a single
# Devanagari phrase repeating - a machine-translation loop in the source
# dataset. It fills the embedder's 512-token window instead of the ~20 tokens a
# real question uses and costs 118 ms in `embed_query`, against a Hindi P99 of
# 5.89 ms. Nothing downstream can catch it: ISSUES.md I25 measured that a stage
# timeout cannot interrupt synchronous ONNX work, so the bound has to be here.
#
# Why the bound is on TOKENS. Cost is linear in tokens at ~0.21 ms/token up to
# the 512-token cap. A character limit bounds characters, and characters per
# token are script dependent - English 4.56, Hindi 3.19, and an adversarial
# input can push lower - so every character-only limit leaks:
#
#     char limit   worst admissible input   worst latency
#        512              172 tokens            24.99 ms
#       1024              343 tokens            56.02 ms
#       2048              512 tokens           107.77 ms
#
# Measured token bounds, against the same worst case:
#
#     token cap   worst-case latency   legit hi rejected   legit en rejected
#        32             5.79 ms               1                  0
#        64            10.02 ms               1                  0
#       128            18.15 ms               1                  0
#
# 64 bounds the worst case at 10.02 ms, and it clears the largest legitimate
# query by 2.6x: legitimate Hindi is p99 24 tokens with a second-highest of 25,
# English maxes at 16. It is also justified externally, which is what makes it
# defensible rather than fitted: 64 tokens is roughly fifteen seconds of
# continuous speech, and this is a voice product. A question nobody could say
# out loud in fifteen seconds is not a question this system is for. That holds
# whether or not query 156297 exists.
INPUT_MAX_TOKENS: Final[int] = 64

# The character pre-filter is a PERFORMANCE early-out, not the safety bound.
# I1 measured it at 0.00007 ms against 0.04228 ms to tokenize, so it rejects
# gross input 600x cheaper and before any allocation. It is set well above the
# token bound's character equivalent (64 tokens is ~205 characters of Hindi) so
# that it can never be the thing that refuses a real question.
INPUT_MAX_CHARS: Final[int] = 512

# --------------------------------------------------------------------------
# Guardrails, layer 4. Architecture.md 7, guardrails/output_guard.py. Phase 6.
# --------------------------------------------------------------------------

# What calibrated this (Rules.md 6): measured separation on a worked example,
# recorded in tests/test_output_guard.py so it stays checkable.
#
# The measure is content-word recall averaged with adjacent-pair recall, both
# against the cited passages. Scores on one passage about Mount Everest:
#
#     1.000  a verbatim span                (what the extractive path returns)
#     0.833  a FALSE sentence reassembled out of the passage's own words
#     0.661  a true sentence with an invented number
#     0.639  a TRUE paraphrase
#     0.062  an answer about something else entirely
#
# Read that list carefully, because it says what this threshold can and cannot
# do. The false reassembly outscores the true paraphrase. Lexical overlap
# measures whether the wording is traceable to the source, which is a different
# question from whether the answer is correct, and no cut on this axis
# adjudicates claims.
#
# So the floor is set to catch the bottom of that list and nothing more: 0.35
# sits well below the true paraphrase at 0.639 and well above the unsupported
# answer at 0.062. It is deliberately not tuned upward to catch the reassembly,
# because doing so would refuse honest paraphrases first.
#
# ISSUES.md I26 is why this layer exists at all: the retrieval score is an
# out-of-domain detector and cannot see grounding, so this is the only place
# that looks at what was actually said.
OUTPUT_MIN_GROUNDEDNESS: Final[float] = 0.35

# --------------------------------------------------------------------------
# Routing thresholds. Architecture.md 3.6, answering/router.py.
# --------------------------------------------------------------------------

# These are RAW CROSS-ENCODER LOGITS from the model named in RERANKER, roughly on
# a -11..+11 scale. They are not comparable to a dense cosine and they are not
# comparable across rerankers - changing RERANKER invalidates both and requires
# re-running the calibration.
#
# Architecture.md 7 Layer 2 names a confidence floor of 0.35. That figure was
# written for a normalised score and is superseded here; ISSUES.md I3 records why
# no dense-scale threshold can work at all.
#
# What calibrated these (Rules.md 6): scripts/06_calibrate_routing.py, over the DEV
# partition only (Rules.md 5), against three populations - answerable queries
# labelled by whether the reranked top-1 is actually gold, real queries scored
# against another query's candidate pool (genuinely unanswerable), and the I3
# gibberish probe. Placeholder values below are overwritten by that run; see the
# Memory.md Phase 5 entry for the fitted numbers and the curve they came off.
# Fitted 19 Aug 2026 on 250 dev queries, depth 5, reranker "multi".
# bench/results/2026-08-19-064809-routing-calibration.json.
#
# TAU_LOW = -1.103. Chosen as the 5th percentile of the answerable population, so
# at most 5% of answerable queries are abstained on. At that cut it catches
# **100% of genuinely-unanswerable queries and 100% of gibberish**. The populations
# barely touch: answerable-correct has a median of +8.30, unanswerable -7.28. This
# is the number that makes requirement 6 real, and it is the vindication of
# Architecture.md 3.6 - ISSUES.md I3 measured dense cosine separating the same two
# cases by 0.05, which no threshold could have used.
ROUTE_TAU_LOW: Final[float] = -1.103   # below: ABSTAIN

# TAU_HIGH = 1.877. This one is a judgement call made against a bad curve, and the
# honest description is a floor, not an optimum.
#
# Top-1 precision NEVER reaches the 0.75 the calibration targeted. It peaks at
# 0.508 (37.4% coverage) and then falls again - the rerank score is only weakly
# predictive of whether top-1 is the gold passage:
#
#     cut     precision   coverage
#     1.88        0.400      85.0%   <- here
#     4.99        0.433      65.6%
#     8.09        0.508      37.4%   (peak)
#     9.65        0.485      20.6%
#
# So assumption A6 is false: the extractive path is not reliably right, and D2's
# reversal condition is triggered rather than narrowly avoided.
#
# Given that, precision is not worth buying with coverage. Two reasons:
#   1. ISSUES.md I7 - Groq's free tier is 12,000 tokens per window, about 12 calls.
#      Routing 58-70% of queries to it (which the higher cuts imply) is not slow,
#      it is inoperable, the same arithmetic that killed C4 in Phase 3.
#   2. The extractive path does not assert "this is the answer". It returns a
#      retrieved passage WITH its citation, and Hit@1 asks whether that passage is
#      the one MS MARCO happened to label is_selected. A topically-correct but
#      unlabelled passage scores zero here and is still useful to a reader.
#
# Resulting distribution over answerable dev queries: 85% extractive, 10%
# generative, 5% abstain. Revisit if the reranker or the corpus changes; these
# numbers are not transferable to either.
ROUTE_TAU_HIGH: Final[float] = 1.877   # at or above: EXTRACTIVE, no network call

# --------------------------------------------------------------------------
# Generative fallback. Architecture.md 3.7, Phase 5. FALLBACK PATH ONLY.
# --------------------------------------------------------------------------

GROQ_URL: Final[str] = "https://api.groq.com/openai/v1/chat/completions"

# FORCED CHANGE, 19 Aug 2026. Rules.md 3.3 named llama-3.3-70b-versatile with
# llama-3.1-8b-instant as the alternate. BOTH now 404 with model_not_found -
# Groq retired the entire Llama chat lineup for this account between 14 and 19
# August. Memory.md's 14 Aug entry recording them as verified-available is
# therefore stale, which is a good argument for re-checking a provider's model
# list at the start of any phase that depends on it rather than trusting a note.
#
# Measured on the three chat models actually available (grounded prompt, one
# passage, en and hi):
#
#   openai/gpt-oss-20b     680 / 563 ms   correct both languages, clean [1] cites
#   openai/gpt-oss-120b    458 / 656 ms   correct, but emits fullwidth cites U+3010
#   qwen/qwen3.6-27b      1385 / 562 ms   UNUSABLE - see below
#
# qwen is a reasoning model: it opens with a <think> block that consumes the whole
# 160-token cap before producing any answer, so the response is truncated
# reasoning and never reaches a citation. Worse, while reasoning it quotes the
# abstention sentinel from the system prompt, which used to trip a false
# abstention - the substring check in generative.py was tightened because of it.
#
# gpt-oss-20b over the 120b: both are correct, the 20b's citation format needs no
# normalisation, and this is the FALLBACK path where the floor is already ~500ms.
GROQ_MODEL: Final[str] = "openai/gpt-oss-20b"

# Temperature 0 and a short cap: this path paraphrases retrieved passages, it does
# not compose. Anything longer invites the model to pad beyond its sources, which
# is exactly what the Phase 6 groundedness check would then have to catch.
GROQ_TEMPERATURE: Final[float] = 0.0
GROQ_MAX_TOKENS: Final[int] = 160

# The unverified aside gets its own, larger budget and its own reasoning setting.
#
# 160 is right for the generative PATH, where the model paraphrases passages it
# has been handed. It is wrong for the aside, which answers from its own
# knowledge - and gpt-oss-20b is a REASONING model, so it spends tokens thinking
# before it writes anything. At 160 the thinking ate the budget and the answer
# arrived cut off mid-sentence: "Eric Adams is the".
#
# This is the same trap config.py already records for qwen3.6-27b, and the one
# scripts/11_llm_judge.py hit at max_tokens 8, where the reasoning consumed the
# whole cap and `content` came back an empty string. Measured there: a
# one-word verdict cost 53 completion tokens.
#
# 320 with reasoning_effort "low" leaves room for the thinking AND two finished
# sentences. The aside is not on the fast path, so the extra tokens cost latency
# nobody is measuring rather than budget somebody published.
#
# LOWERED 320 -> 240 on 21 Aug. The aside is the ONLY thing standing between a
# curious visitor and the 12,000-token window that the real generative fallback
# also draws on (ISSUES.md I7), so the cap is now set as tight as it can be
# without re-entering the truncation trap rather than as loose as it can afford.
#
# 240 is a measured floor, not a guess. Re-run at 240 on the five questions most
# likely to run long - including the "who is the mayor of New York City" query
# that produced the "Eric Adams is the" truncation at 160 - and in both scripts:
# all five finish their sentences. 160 does not. If a Groq aside starts arriving
# truncated again, this is the first number to look at, not the last.
ASIDE_MAX_TOKENS: Final[int] = 240
ASIDE_REASONING_EFFORT: Final[str] = "low"

# Hard ceiling on the call. Generous relative to the 352ms floor and deliberately
# finite: an unbounded wait on the fallback path turns a slow answer into a hung
# request, and the extractive answer is already in hand by the time this runs.
GROQ_TIMEOUT_MS: Final[float] = 4000.0
GROQ_CONNECT_TIMEOUT_MS: Final[float] = 1500.0

# Passages handed to the model as context. Three is what the extractive path
# already cites (answering/extractive.py MAX_CITATIONS), so both paths ground on
# the same evidence and a citation index means the same thing on either.
GROQ_CONTEXT_PASSAGES: Final[int] = 3

# --------------------------------------------------------------------------
# Aside rate limiting. Per CLIENT, not per process. ISSUES.md I35.
# --------------------------------------------------------------------------
#
# Five calls per sixty seconds, per client, counted in a sliding window rather
# than a fixed bucket - a fixed bucket lets 10 calls through across a boundary,
# which is the burst this exists to bound.
#
# WHY IT EXISTS. One free-tier Groq key serves every visitor to one public site,
# and the aside spends the same 12,000-token window as the real generative
# fallback (ISSUES.md I7). DONT-FORGET.md 13 already recorded the failure mode:
# "a judge clicking repeatedly in accurate mode can exhaust it", after which the
# panel stops appearing for EVERYONE and the Band B fallback degrades with it. A
# per-client cap turns one impatient visitor from a site-wide outage into a
# personal pause.
#
# It is deliberately in front of the circuit breaker rather than behind it. The
# breaker reacts to an upstream that has already been pushed over; this declines
# to push it over. They are different jobs and both are wanted.
ASIDE_RATE_LIMIT: Final[int] = 5
ASIDE_RATE_WINDOW_SECONDS: Final[float] = 60.0

# --------------------------------------------------------------------------
# Latency contract. Latency.md section 4.
# --------------------------------------------------------------------------

BUDGET_MS: Final[float] = 200.0

# Per-stage allocation, hard timeout. Allocated total is 175ms; the remaining 25ms
# is reserve for GC pauses and scheduler jitter that show up at P99/P100.
# Rebalanced in Phase 5 against measurement rather than estimate. The Phase 0 table
# was written before anything existed; three stages have now been measured and two
# of them were over-provisioned by an order of magnitude while rerank was under-
# provisioned by two.
#
# Measured medians on BENCH: embed_query 2.81, dense_search 0.42,
# answer_extractive 0.03, rerank (depth 5) 59.3 P50 / 102.4 P100.
#
# embed_query keeps 20 ms rather than dropping to its 2.81 ms median because
# ISSUES.md I1's pathological query costs 118 ms in that stage and the Phase 6
# input guard that bounds it is not built yet.
STAGE_BUDGET_MS: Final[dict[str, float]] = {
    "input_guard": 12.0,
    "embed_query": 20.0,
    "dense_search": 8.0,
    "lexical_search": 10.0,
    "fuse": 3.0,
    "rerank": 90.0,
    "route": 2.0,
    "answer_extractive": 5.0,
    "output_guard": 25.0,
    "serialize": 8.0,
}

# READ ISSUES.md I25 BEFORE TRUSTING THESE. A stage timeout is enforced with
# asyncio.wait_for, which can only fire at an await point - and every Band A stage
# is synchronous ONNX or C++ work that never yields. Measured directly: a sync
# stage with a 50 ms timeout ran 123.7 ms and reported status "ok", while an
# awaiting stage with the same timeout was cut off at 47.4 ms.
#
# So for the sync stages these values are ADVISORY. The two mechanisms that do
# work are the pre-stage budget gate (which can decline to start a stage) and, for
# rerank specifically, the deadline it checks between pairs. The generative stage
# is the one place a timeout here is genuinely load-bearing, because it awaits.
STAGE_TIMEOUT_MS: Final[dict[str, float]] = {
    "input_guard": 15.0,
    "embed_query": 30.0,
    "dense_search": 20.0,
    "lexical_search": 12.0,
    "fuse": 5.0,
    "rerank": 130.0,
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
