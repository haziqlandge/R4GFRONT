# Memory.md

Rolling context log for team OK4T, Task 2.

**Purpose.** If a session ends, a laptop dies, a teammate joins late, or a new AI coding session starts cold, this file is what gets read first. It carries the *why*, not just the *what*. Code shows what was built; this file shows what was considered and rejected, which is the part that is expensive to reconstruct.

---

## How to use this file

**Read it first.** Before any new session, before any new phase, before asking a teammate a question that might already be answered here.

**Write to it at the end of every phase, before the merge.** A phase is not done until its entry exists. This is a HARD rule in `Rules.md`.

**Entry format:**

```markdown
### [Phase N] Title
**Date:** | **Who:** | **Branch:**

**What happened**
Plain description of what was built or changed.

**Why this approach**
The reasoning. What alternatives were on the table and why they lost.
This is the most valuable field in the entry. Do not skip it.

**What it unblocks**
What becomes possible now that this exists.

**Numbers**
Any measurement produced. Latency, recall, index size, build time.

**Surprises and gotchas**
Things that cost time. Things a future session would hit again.

**Open threads**
Anything deliberately deferred, with a note on where it should land.
```

Also log **mid-phase** whenever any of these happen: a SOFT rule is broken, a benchmark number moves materially, an assumption turns out false, or a decision gets reversed. Reversals are the most valuable entries in the file, because the reasoning behind a reversal is exactly what a future session will otherwise repeat.

---

## Standing context

Carry-forward facts a cold session needs immediately.

| Fact | Value |
|---|---|
| Team | OK4T |
| Task | HH Goa 2026 Shortlisting Task 2, voice-enabled RAG |
| Deadline | 22 August 2026, 11:59 PM IST. No resubmissions. |
| Hashtag | `#RAGInGoa` |
| Dataset | `ai4bharat/MSMARCO-XI` on Hugging Face |
| STT provider | Sarvam, `saaras:v3-realtime`. Chosen over ElevenLabs; the brief says pick one and we picked. |
| LLM | Groq, fallback path only |
| Latency target | 200ms, measured as Band A per `Latency.md` |
| Previous submission | Task 1, ID/frame generator, shipped at `id-frame-check.vercel.app` |
| Local Python | **3.12** (`py -3.12`, `.venv\`). Not 3.14 — `onnxruntime`, `hnswlib` and `bm25s` have no 3.14 wheels. |
| Dataset access | `hf_hub_download` on individual parquet files. `load_dataset()` **does not work** on this repo. |
| English is free | Every language file carries parallel `English_passages` *and* `Translated_passages`. One download, two corpora. |
| Frozen slice | English + Hindi, 15,000 queries, 295,890 passages, seed 20260814, dataset revision `bf5cdc1f`. `artifacts/slice_manifest.json`. |
| Passage length | English passages max out at **205 words**. Nothing needs splitting. See D8. |

---

## Decision log

Decisions made before implementation started, recorded here so they are not relitigated. Each has a reversal condition: the observation that should make us change our mind.

### D1: Sarvam over ElevenLabs
**Date:** 14 Aug 2026

Corpus is thirteen Indian languages. Sarvam's Saaras v3 is trained on Indian audio, covers 22 Indian languages plus English, handles code-mixed speech, and publishes sub-150ms time-to-first-token in fast mode. Crucially the `saaras:v3-realtime` endpoint emits true partial transcripts, which is what makes the speculative prefetch in `Latency.md` section 5 possible. ElevenLabs has no comparable Indic-language story for this corpus.

**Reversal condition:** Sarvam accuracy on our actual demo queries is materially worse than expected, or the realtime endpoint proves unstable.

### D2: Dual-path answering, extractive fast path plus LLM fallback
**Date:** 14 Aug 2026

The central architectural decision. A hosted LLM call cannot fit inside 200ms; TTFT for even the fastest providers is measured in hundreds of milliseconds, which exhausts the budget before retrieval starts. MS MARCO passages are answer-bearing by construction and ship `is_selected` flags, so span extraction is the correct operation for this corpus rather than a degraded substitute. The same reranker confidence score drives both the path routing and the abstention decision, so requirements 3 and 6 are satisfied by one calibrated mechanism.

**Reversal condition:** extractive answer quality on the dev slice is poor enough that judges would read it as a dodge. If so, keep the fast path but present it as a "fast mode" toggle rather than the default.

### D3: Everything in-process, no hosted vector DB, no hosted embeddings
**Date:** 14 Aug 2026

Every network hop is 20 to 80ms. The budget does not survive two of them. hnswlib plus bm25s plus ONNX int8 models, all in one process, all warm in RAM.

**Reversal condition:** none foreseeable. This is the load-bearing decision.

### D4: No LangChain or LlamaIndex in the runtime
**Date:** 14 Aug 2026

Deep call stacks with hidden retries and hidden network calls. You cannot budget what you cannot see, and this project is entirely about budgeting. Their source is worth reading for ideas.

**Reversal condition:** none.

### D5: Freeze a corpus slice, do not index all of MSMARCO-XI
**Date:** 14 Aug 2026

Individual validation splits run to hundreds of megabytes (the Telugu one is 474 MB). Indexing everything wastes days and blows the container RAM budget. A documented, seeded, reproducible slice is worth more than a large one.

**Reversal condition:** the slice proves too small to make retrieval non-trivial. Widen it, regenerate all benchmarks, and note the invalidation.

### D6: Publish three latency bands honestly
**Date:** 14 Aug 2026

Band A (core RAG) is the number that meets the brief. Bands B and C are published alongside with the boundary stated. A fabricated sub-200ms number a judge can poke a hole in is worse than an honest larger number with rigorous methodology behind it.

**Reversal condition:** none. This is a values decision, not a technical one.

### D7: Deploy on an always-on container in India, never serverless
**Date:** 14 Aug 2026

Cold starts destroy P100 and index load takes seconds. A US-region deployment adds enough round trip on its own to make the task unwinnable.

**Reversal condition:** none.

### D8: The chunking problem here is composition, not splitting
**Date:** 14 Aug 2026

Measured on the frozen slice: English passages are p50 48 words, p99 115 words, **max 205 words**. Not one exceeds 256 tokens. A 256-token fixed-size chunker emits exactly one chunk per passage and does nothing.

So requirement 2 is reframed around choosing the retrieval *unit* rather than cutting long documents: sub-passage units (C1 at 96/24, C2, C3, C4, C8), supra-passage units (C6, grouping the ~10 passages sharing a `query_id` into a parent), and alternative representations (C5, C7). `Architecture.md` §4.1 carries the full argument.

This is a stronger answer to the brief than eight splitters would be, because it demonstrates the strategy was picked against measured corpus properties. It also means C1's parameters changed from 256/40 to 96/24.

**Reversal condition:** if the slice is ever widened to a corpus with genuinely long documents, this argument stops applying and C1 goes back to 256/40.

---

## Phase entries

_Append below as phases complete. Newest at the bottom._

### [Phase 0] Foundation and measurement
**Date:** 14 Aug 2026 | **Who:** Claude Code session | **Branch:** `main`

**What happened**
Repo initialised, the eight planning docs moved to root, the full `Architecture.md` §5 tree created with named stubs. `.venv` on Python 3.12. Built `services/rag_core/harness/trace.py` (Span, Trace, `span()` context manager, `add_skipped()`, serialization matching the `Architecture.md` §9 contract) and `scripts/04_bench_latency.py` (warmup discard, `numpy.percentile` with `method="nearest"`, dated immutable JSON output, `--stub` and `--breakdown`). 15 tests in `tests/test_harness.py`, all passing.

**Why this approach**
Measurement rig before any product code, because every architecture decision from Phase 2 onward is downstream of the numbers it produces. A team that builds first and measures on day seven discovers the architecture is wrong with two days left.

The stub deliberately has a *known* answer — its stages sum to 72.5ms — so the rig can be validated against it. A benchmark harness nobody has checked is just a number generator.

**What it unblocks**
Phase 2 plugs the real pipeline into `run_benchmark()` unchanged. `Trace.serialize()` is already the exact shape `LatencyWaterfall.tsx` will consume in Phase 8, so the UI contract is fixed before the UI exists.

**Numbers**
Stub pipeline, 250 samples, 30 warmup discarded, concurrency 1:
P50 72.55 · P70 72.57 · P90 72.59 · P99 72.66 · P100 75.30 ms. Mean 72.57, stddev 0.18.
Expected 72.5 ms, so **harness overhead is 0.05 ms**. The rig does not meaningfully pollute the measurement.
Result: `bench/results/2026-08-14-170359-banda-stub.json`.

**Surprises and gotchas**
- **`asyncio.sleep` is useless for sub-15ms stubs on Windows.** Timer granularity is ~15.6ms, so every stage under 15ms would have measured identically. Switched to a `perf_counter_ns` busy-spin, which is also the honest simulation: real Band A stages are CPU-bound in-process work, not awaits.
- P100 (75.30) sits 2.7ms above P99 (72.66) even on a stub doing nothing but spinning. That gap is pure OS scheduler jitter and it is exactly what the 25ms reserve in `Latency.md` §4 exists to absorb. Good early evidence the reserve is correctly sized.
- Python 3.14 is the machine default; `onnxruntime`/`hnswlib`/`bm25s` have no 3.14 wheels. Everything runs on `py -3.12`.
- `.gitignore` needs `artifacts/*` not `artifacts/`, because git cannot un-ignore a file inside an ignored directory and `slice_manifest.json` must be committed.

**Open threads**
- **Accounts are not done and are a human task.** Sarvam key, Groq key, and the Fly.io/Render India-region check (assumption A7) all still need doing. A7 is the one that can invalidate `Architecture.md` §10, so it should be checked before Phase 2 rather than at Phase 7.
- ONNX export in Phase 2 is the first genuine Windows/Linux divergence risk. Export on the machine that builds the container image, or verify parity on both.
- `--concurrency 8` is implemented but untested against a real pipeline; the stub is CPU-bound and GIL-limited, so its concurrency numbers mean nothing.

---

### [Phase 1] Corpus slice and freeze
**Date:** 14 Aug 2026 | **Who:** Claude Code session | **Branch:** `main`

**What happened**
`scripts/00_download_dataset.py` pulls `validation/hinval.parquet` (440 MB, 97,941 rows) and asserts the schema before anything else runs. `scripts/01_freeze_slice.py` samples 15,000 queries at seed 20260814, explodes them into 295,890 parallel English/Hindi `Passage` records, dedups, assigns disjoint test/dev/corpus_only splits, and writes `passages.parquet`, `queries.parquet`, `slice_manifest.json` and `bench/queries_250.jsonl`. `--verify` rebuilds from the manifest alone and matches.

**Why this approach**
Two-pass streaming rather than loading the file: it is a single 440 MB row group, and pass 1 reads only `query_id`/`query` to pick candidates before pass 2 pays for the passage columns.

Dedup is keyed on the **English** text for both languages. Deduplicating each language independently would collapse different row sets and break the en/hi pairing; keying both on the English sha1 keeps `parallel_id` a perfect bijection, which is what makes "ask in Hindi, cite the English twin" a checkable retrieval event rather than a demo anecdote.

`is_selected` was deliberately **not** put on the passage, contrary to the original plan. It describes a *(query, passage)* pair, and after dedup a passage's owning query is arbitrary — a passage-level flag would have been quietly wrong. Ground truth moved onto the query as `gold_en_ids`/`gold_hi_ids`, which is the shape Recall@10/MRR@10/nDCG@10 consume anyway. The passage keeps `is_selected_any` for C5.

`bench/queries_250.jsonl` was written **now**, in Phase 1, specifically because `Rules.md` §5 forbids tuning against a benchmark you are still editing. Freezing it before anything exists to tune removes the temptation entirely.

**What it unblocks**
Phase 2 index building. Phase 3 retrieval eval has free ground truth. Phase 5 threshold calibration has free answer labels (`answer_en`/`answer_hi`).

**Numbers**
- Source: `ai4bharat/MSMARCO-XI` revision `bf5cdc1f26e581e519018e434db14edd1b77602b`, `validation/hinval.parquet`, 440 MB, 97,941 rows.
- Slice: 15,000 queries from 30,000 candidates. 295,890 passages (147,945 en + 147,945 hi). 1,857 duplicate pairs dropped. 31,990 answer-bearing.
- Splits: test 1,000 / dev 2,000 / corpus_only 12,000.
- Query types: DESCRIPTION 7,885 · NUMERIC 3,667 · ENTITY 1,292 · PERSON 1,081 · LOCATION 1,075.
- Gold passages per query: 1.07 average.
- `records_sha256` `7f9f7c5978bc2456308669cd071c3ed63d5c774f4b858e9822737b59c5743360`.

**Surprises and gotchas**
- **`load_dataset("ai4bharat/MSMARCO-XI", "hi")` fails**, despite being the documented usage on the dataset card. The loader script resolves `.jsonl` paths; the repo holds `.parquet`. Same root cause as the dataset viewer's `500 dataset generation failed`. Anyone who trusts the card loses an hour here.
- **~45% of MS MARCO rows have no `is_selected` passage** ("no answer present"). Acceptance was 55%, so the first run at 1.25x oversample produced 10,331 queries instead of 15,000. Oversample is now 2.0.
- **English passages max out at 205 words.** See decision D8 — this reframes the whole chunking requirement.
- One Hindi passage is 4,093 words against a 205-word English source: a translation-model repetition loop. Whole-passage encoders need a length cap or a few degenerate rows will dominate index build time.
- The dataset has 14 languages including **Sanskrit**, which every earlier draft of these docs omitted.
- Reproducibility is asserted on `records_sha256` (content) rather than the parquet file hash, because parquet encoding can shift with a pyarrow version bump. The file hashes are recorded but advisory.

**Open threads**
- Widening the slice to Tamil/Bengali is a one-line change to `SOURCE_FILES` plus a re-freeze, but it invalidates every benchmark taken against the current slice. Decide before Phase 3, not during.
- The 99.5th-percentile length cap for the Hindi outliers is specified but not implemented; it belongs in the chunkers, Phase 2/3.
- `queries.parquet` carries `answer_en`/`answer_hi` that nothing consumes yet. Phase 5 calibration is where they earn their place.

### [Phase 2] Thin vertical slice, text only
**Date:** 18 Aug 2026 | **Who:** Claude Code session | **Branch:** `p2-vertical-slice`

**What happened**
One working query path end to end: text in, cited passage out, with a real measured P50. ONNX embedder fetched and parity-gated, C1 chunker, 379,242-chunk HNSW index, typed pipeline harness with the budget counter, `POST /v1/answer`, retrieval eval, and the benchmark. 35 tests green, `mypy --strict` clean across 34 files.

**Why this approach**
Kept deliberately thin - one chunker, dense only, no BM25, no reranker, no guardrails - because the point of this phase is an early architectural signal. If the number had come back bad, a thin slice says *which* stage is at fault; a full stack does not.

Added one thing the original task list did not have: `scripts/05_eval_retrieval.py`. Phase 2's exit criterion is a P50, and a P50 from a retriever returning garbage is not a weak number, it is a meaningless one. Every e5 failure mode is silent - omit the `query: `/`passage: ` prefixes or pool on CLS instead of a masked mean and you get a fast service that confidently retrieves the wrong passage, with nothing anywhere raising. The corpus ships `is_selected`, so ground truth was free.

`03_export_onnx.py` fetches rather than exports. The model author already publishes ONNX including an int8 build quantized for AVX512-VNNI; exporting it ourselves would have installed torch (~2 GB) and optimum to reproduce an existing artifact.

**What it unblocks**
Phase 3 adds seven chunkers as siblings under `artifacts/indexes/<strategy>/` with no restructuring. Phase 5 appends rerank and route stages to `build_pipeline()` without touching existing stages. The frontend contract in `answering/schemas.py` is already the final Architecture.md section 9 shape, so Phase 8 builds against it once.

**Numbers**

| | |
|---|---|
| Chunks | 379,242 from 295,890 passages (1.282/passage), 262 truncated |
| Index | 655 MB `index.bin`, 50 MB `chunks.parquet` |
| Build | chunk 55s, embed 30.8 min (211/s avg), HNSW 1.4 min |
| int8 vs fp32 | cosine 0.99686; Hit@1 0.945 vs 0.935; Recall@10 1.000 both |

Retrieval, 500 dev queries, k=10, dense-only:

| | Recall@10 | MRR@10 | Hit@1 |
|---|---|---|---|
| en | 0.870 | 0.525 | 0.362 |
| hi | 0.682 | 0.367 | 0.224 |

**Band A latency, 250 frozen queries, 30 warmup discarded:**

| | P50 | P70 | P90 | P99 | P100 |
|---|---|---|---|---|---|
| en | **3.31** | 3.53 | 3.85 | 4.41 | 4.72 |
| hi | 3.83 | 4.21 | 4.59 | 5.89 | 119.13 |
| en, concurrency 8 | 3.50 | 3.73 | 4.03 | 4.65 | 4.92 |

Per-stage medians: `embed_query` 2.81 ms, `dense_search` 0.42 ms, `answer_extractive` 0.03 ms.

**Surprises and gotchas**

- **P50 is 3.31 ms against a 200 ms budget.** Phases.md set 40 ms as "comfortable headroom"; we are 12x inside that. The reranker's 60 ms allocation, BM25, and all four guardrail layers now fit with room to spare. The architecture is not merely viable, it is over-provisioned - which is a far better problem than the alternative and means Phase 5 can afford accuracy rather than hunting milliseconds.
- **A pathological query costs 118 ms, reproducibly.** Hindi P100 is 119 ms against a P99 of 5.89. It is one query, `query_id=156297`, 7,168 characters of `आर्कजी का फ़ाइल प्रारूप` repeating - the same translation repetition-loop pathology found in passages during Phase 1, now in a *query*. It fills the embedder's entire 512-token window instead of ~20 tokens. Re-run 20x it costs 111 ms every time, so it is the input and not scheduler noise. **This makes the Layer 1 input guard a latency mechanism, not only a safety one** - the same structural point as the reranker score serving both routing and abstention. It stays in the frozen benchmark (Rules.md 5) and gets fixed properly by the length bound in Phase 6.
- **Hit@1 is 0.362 en / 0.224 hi**, and the Phase 2 extractive path returns the top passage. So the naive answer is wrong most of the time. That is expected and is precisely what the Phase 5 reranker exists to fix - rerank top-20 to reorder top-1. It does quantify how much the design leans on that reranker, which was previously an assumption.
- **Thread oversubscription is 3.4x slower, not marginally worse.** On a 12-core box: 8 threads 210 chunks/sec, 16 threads 61. Rules.md 2.2 warned about this; now there is a number.
- **Length-sorted batching is 1.46x faster.** The tokenizer pads each batch to its longest member, so mixed-length batches spend most of their compute on padding.
- **My first int8 parity gate was a bad test.** It compared top-10 neighbour overlap among randomly sampled passages and failed at 0.866. Measured properly, the similarity gap between neighbour rank 10 and rank 11 on this corpus is 0.00137 while int8 perturbs cosine by ~0.004 - the perturbation exceeds the tie gap, so that ordering is noise and its instability measures nothing. Replaced with query-to-gold retrieval agreement, where the two models are indistinguishable. Lesson worth keeping: a failing gate deserves a diagnosis before a threshold change, and sometimes the diagnosis is that the gate was wrong.
- Chunk text is sliced from the source by character offset rather than decoded from token ids. Decode round-trips lose whitespace and normalise characters, and the extractive path hands this text to the user verbatim.

- **Dense cosine is a poor out-of-distribution detector, and this changes Phase 6.** Live endpoint results: a correct English match scores 0.9193, a correct Hindi match 0.9050, and the pure gibberish query `zxqwv fhqwhgads plorbnak` scores **0.8624**. A ~0.05 margin between "right answer" and "meaningless input" is far too narrow to set an abstention floor on. This is direct evidence for Architecture.md 3.6's choice to make the *reranker* score the confidence signal rather than the retrieval score - a cross-encoder actually reads the query against the passage, where a bi-encoder only compares two independent embeddings. Phase 6 must calibrate the floor on rerank score; a dense-score floor would either abstain on good answers or accept nonsense.
- The gibberish query's top hits are two passages whose entire text is `-`. There are exactly 2 such passages in 295,890 (0.001%), both Hindi, and they act as attractors for meaningless queries because a degenerate embedding sits near the centroid. Filter empty and near-empty passages at index build time in Phase 3. The slice itself stays frozen (Rules.md 5).
- **Non-ASCII payloads through curl on Windows get mangled.** A Hindi query sent via `curl -d` returned unrelated passages and looked like a cross-lingual retrieval bug; the same query sent through Python `urllib` with explicit UTF-8 encoding returns the correct passage at rank 1. Test Indic-language endpoints with a real HTTP client, not shell curl, or the next session will chase a defect that is not there.

**Open threads**
- Hindi retrieval trails English by ~0.19 Recall@10. Expected - queries and passages are both machine-translated and e5-small is weaker on Devanagari - but worth confirming BM25 (Phase 3) and the reranker (Phase 5) close the gap rather than widen it.
- `Runtime.build_passage_map` reconstructs passage text by taking the longest chunk. Correct for the 78% of passages that yield one chunk, approximate for the rest. Phase 5 needs a real passage store when span selection requires exact offsets.
- All eight indexes at 655 MB each will not be simultaneously resident on an 8 GB box. The F13 strategy toggle must load on switch. A deliberate toggle is not the hot path, so this is acceptable, but it changes F13's design.
- Benchmarks so far are local x86, not the GCP box. Latency.md 6 requires the published numbers come from the deployed service.

---

### [Phase 3] Chunking depth
_pending_

### [Phase 4] Voice input
_pending_

### [Phase 5] Reranking, routing and calibration
_pending_

### [Phase 6] Guardrails
_pending_

### [Phase 7] Deploy and harden
_pending_

### [Phase 8] Demo surfaces and polish
_pending_

### [Phase 9] Videos, posting, submission
_pending_

---

## Mid-phase log

### 14 Aug 2026 — API keys verified, and Groq's round trip confirms the dual-path thesis

**Sarvam.** `POST /text-lid` returns 200. Correctly identified Devanagari Hindi input as `hi-IN` / `Deva`. Key is live. Note `/text-lid` covers only 11 languages, not all 23 — fine for our en+hi slice, but it is not a general language detector.

**Groq.** `GET /v1/models` returns 200; both `llama-3.3-70b-versatile` and `llama-3.1-8b-instant` are available, 15 models total.

**Gotcha:** Groq's edge returns `403, error code: 1010` — a Cloudflare fingerprint block — to any request with a default `urllib`/`python-requests` User-Agent. It looks exactly like an auth failure and is not one (a bad key returns 401 with a JSON body). **The `httpx.AsyncClient` in `answering/generative.py` must set an explicit User-Agent.** This would otherwise present as a mystery 403 in Phase 5, on the fallback path, under time pressure.

**The number that matters.** A minimal non-streamed completion — 5 max tokens, 44 tokens total — took **352 ms** end to end from this machine. That is the floor: no retrieval, no prompt of any size, no real answer length, and not from a Mumbai container. It is comfortably outside the 200 ms budget on its own.

This is the first hard evidence for D2 and for `Latency.md` §2, and it came from a real measurement rather than a cited benchmark. Use this number in the README and in Video 2: *"the fastest hosted provider's shortest possible call is 352 ms before our pipeline does anything."*

**HF token.** Read scope, verified against `ai4bharat/MSMARCO-XI`. Optional — the dataset is public and downloaded fine anonymously — so it only buys rate limits and transfer speed. Read is the right scope: a write token in a `.env` could modify our own HF repos and buys nothing.

**`.env` loading.** `config.load_env()` is dependency-free (no python-dotenv) and real environment variables win over the file, because in production the keys arrive as platform secrets and there is no `.env` at all. It also strips stray quotes: `KEY= "value"` would otherwise send the quote as part of the secret and produce a 401 that looks like a bad key rather than a bad file. That exact mistake happened here on first entry.

**Free-tier rate limits observed:** 1,000 requests and 12,000 tokens per window. The token ceiling is the binding one — a full 250-query Band B benchmark at ~1,000 tokens per query needs ~250k tokens and will be throttled hard. **Plan Band B measurement as a smaller sample (say 50 queries) and say so in the methodology**, rather than discovering the throttle mid-benchmark. This also makes the Phase 5 circuit breaker easy to demo honestly: the 429 will be real.

### 15 Aug 2026 — A7 resolved: Oracle Cloud Hyderabad, not Render

Surveyed hosts against the three constraints in `Architecture.md` §10: India region, always-on, enough RAM.

| Host | Free? | India region | Always-on | RAM / CPU |
|---|---|---|---|---|
| **Oracle Cloud, Ampere A1** | **$0 forever** | **Mumbai + Hyderabad** | **yes, real VM** | **12 GB / 2 OCPU** |
| HF Spaces, CPU Basic | $0 | no, US only | sleeps at 48h idle | 16 GB / 2 vCPU |
| Render Standard | $25/mo | no, Singapore | yes | 2 GB / 1 CPU |
| Fly.io | ~$5-6/mo | Mumbai | yes | 2 GB |
| Koyeb free | $0 | no | yes | 256 MB, unusable |

**Render is out as the target.** No India region at all — nearest is Singapore. Worse, its free *and* Starter tiers are both 512 MB, and the service needs ~1.2 GB:

| Component | RAM |
|---|---|
| Dense vectors, 295,890 × 384 × 4 B | 454 MB |
| HNSW graph, M=32 | 77 MB |
| ONNX embedder + reranker, int8 | 175 MB |
| BM25 index + passage text | 250 MB |
| Python + FastAPI + numpy | 200 MB |
| **one strategy, total** | **~1.16 GB** |

512 MB OOMs on the vectors alone. Render's floor for this workload is Standard at $25/mo, in the wrong region.

**Decision: Oracle Cloud Always Free, Ampere A1, 2 OCPU / 12 GB, home region Hyderabad.** Zero cost, correct country, no cold start, and 12 GB leaves room to keep several chunking indexes resident — which is what F13's live strategy toggle needs. Hyderabad over Mumbai because Mumbai is heavily contended for A1 capacity.

**Reversal condition:** A1 capacity never materialises in either India region. Fall back to Fly.io Mumbai at ~$5-6/mo, which satisfies the same constraints for money.

**Render stays as an interim smoke-test target only** — proving wiring, CORS, health checks and the WS relay on a deliberately reduced corpus. Any number measured there is throwaway: Singapore-x86 will not survive the move to Hyderabad-ARM, and `Latency.md` §6 requires benchmarks against the real deployed service. **Nothing measured on Render goes in the README.**

**This raises a new risk: A1 is ARM (aarch64), not x86.** `onnxruntime`, `hnswlib` and `bm25s` all support aarch64, but int8 inference uses different kernels there. Assumption A1 — the one the entire 200 ms budget rests on — must be re-verified on the actual box, early. Logged as A11.

**Two gotchas recorded so nobody loses an evening:**
- Oracle's **home region is permanent** and set at signup. Choosing wrong means a whole new account.
- **Two firewalls.** Opening a port in the OCI security list is not enough; Oracle's Ubuntu images ship with `iptables` blocking everything except 22. Both must be opened. This is the single most common "my instance is unreachable" cause.

---

## Reversals and corrections

_Log here whenever a prior decision is overturned. Include the original reasoning, what changed, and the new decision. These are the highest-value entries in the file._

### R3: Google Cloud Mumbai, not Oracle Cloud Hyderabad
**Date:** 15 Aug 2026 | **Overturns:** the A7 resolution taken earlier the same day

**Original reasoning.** Oracle Cloud Always Free gave 2 OCPU / 12 GB in an India region at $0 forever, which beat Render's $25/mo Singapore floor on every axis. The accepted costs were ARM (aarch64), the "out of host capacity" lottery, and self-managing a bare VM.

**What changed.** A Google Cloud account with $300 / 90-day trial credits became available. That reprices the comparison entirely.

**New decision.** Compute Engine VM, `n2-standard-2` (2 vCPU / 8 GB), **`asia-south1` (Mumbai)**, always on. Notes in `deploy/gcp.md`.

**Why this is better, not merely different:**

1. **It is x86.** This retires assumption **A11** without an experiment. ONNX int8 on aarch64 uses different kernels, and A11 sat directly on top of A1 and A2 — the assumptions the entire 200 ms budget rests on. Removing an untested variable from the critical path six days before the deadline is worth more than the money saved.
2. **Mumbai, not Hyderabad.** Marginally closer to most judges, and a first-class region rather than a capacity lottery.
3. **No provisioning gamble.** Oracle's A1 "out of host capacity" could have burned an evening or a week, with no way to predict which.

**Costs accepted:**

- **It is not free after the runway ends.** ~$70/mo means the $300 lasts about four months, to roughly mid-December. The live URL must survive the HH Goa selection rounds through mid-September, so this is comfortable — but it is a runway, not a permanent home.
- **Trial ends at $300 or 90 days, whichever first**, and then *all resources stop* with data marked for deletion and a 30-day grace period. A budget alert is mandatory, not optional.

**Explicitly rejected: Cloud Run.** It is the obvious GCP answer for FastAPI and the wrong one. `rag_core` holds ~1.2 GB of warm index and takes seconds to load; Cloud Run pays that per cold start, which `Rules.md` §3.2 bans. Pinning `--min-instances=1` does keep it warm, but then you pay VM prices for something you cannot SSH into, `mmap` predictably, or profile. For a project about per-millisecond control, that trade is backwards.

**Also rejected: `e2` machine types**, despite being cheaper. The `e2` family is burstable — sustained CPU throttles toward a baseline once credits run out. Invisible at P50, brutal at P100, and P100 is the number that fails. `Latency.md` §4 reserves 25 ms for jitter and a throttling vCPU eats all of it.

**Reversal condition:** credits drain faster than projected, or the frontend/judging window extends past the runway. Fall back to Oracle Always Free, which stays $0 — at the cost of re-verifying A11 on ARM.

**Provisioned 15 Aug 2026.** Instance `rag-core`, `asia-south1-a`, `n2-standard-2`, static external IP `34.100.222.236`, reserved address `rag-core-ip`. Firewall rule `allow-rag-core` (tcp:80,443) applied. GCP's firewall is the only one in the path — unlike the Oracle plan this superseded, Ubuntu's image ships with no blocking iptables rules, so there is exactly one gate to open, not two. Project id is the GCP-generated default (`project-bc7a4f5d-...`); cosmetic only, left as is.

### R1: C5 metadata-aware chunking redefined; the `url` field does not exist
**Date:** 14 Aug 2026 | **Overturns:** `Architecture.md` §4 as originally written

**Original reasoning.** C5 was specified as chunking on "`language`, `query_id`, `is_selected`, `url` domain", with URL domain as a quality and topicality signal. This was written from knowledge of the *original* MS MARCO, which ships a `url` per passage.

**What changed.** MSMARCO-XI does not carry `url`. Its schema is `source_lang`, `target_lang`, `meta`, `query`, `Answer`, `query_id`, `query_type`, `passages{is_selected, English_passages, Translated_passages}`, `Eng_Query`, `Eng_Answer`. The translation pipeline dropped the field. Verified directly against the parquet schema, not inferred.

**New decision.** C5 filters on `language`, `script`, `query_type`, `is_selected_any` and passage `position`. `query_type` replaces `url` as the primary pre-filter signal and is arguably better for this task: it is a real query-intent label (DESCRIPTION / NUMERIC / ENTITY / LOCATION / PERSON) rather than a proxy for source quality, and the distribution is usefully spread (51% / 24% / 9% / 7% / 7%).

**The lesson worth carrying.** The schema in a planning doc was written from the upstream dataset, not the one we are actually using. Assert schemas against the file. `scripts/00_download_dataset.py` now does this before any sampling runs.

### R2: `is_selected` moved off the passage and onto the query
**Date:** 14 Aug 2026 | **Overturns:** the Phase 1 plan's `Passage` record

**Original reasoning.** `is_selected` was going to be a field on `Passage`, mirroring how it appears in the source row.

**What changed.** Deduplication makes it incoherent. `is_selected` is a property of a *(query, passage)* pair; the same passage text is answer-bearing for one query and not for another. After dedup the record's owning query is whichever one happened to be seen first, so a passage-level flag would have been arbitrary and quietly wrong — the worst kind of bug, because Recall@10 would still have produced plausible-looking numbers.

**New decision.** Gold ids live on the query (`gold_en_ids`, `gold_hi_ids`), which is the shape the Phase 3 metrics consume anyway. `Passage.is_selected_any` survives as a genuine corpus-level property: "was this text ever answer-bearing for anything in the slice".

---

## Assumptions not yet verified

Track these explicitly. An unverified assumption that turns out false late is the most expensive kind of problem.

| # | Assumption | Verify in | Status |
|---|---|---|---|
| A1 | ONNX int8 `multilingual-e5-small` embeds a short query in under 15ms on the target container CPU | Phase 2 | ✓ **TRUE, 2.81 ms median** locally. Re-verify on the GCP box. |
| A2 | Cross-encoder rerank of 20 candidates fits in 45ms on the same CPU | Phase 5 | ☐ |
| A3 | Sarvam's realtime endpoint emits partials fast enough and stably enough for speculative prefetch to hit often | Phase 4 | ☐ |
| A4 | The frozen slice fits in the container RAM budget with all eight indexes loaded | Phase 3 | ✗ **FALSE as stated.** One index is 655 MB; eight will not co-reside in 8 GB alongside the models. Load-on-switch instead. |
| A5 | C7 (doc2query / query-aligned) outperforms the other seven strategies on this corpus | Phase 3 | ☐ |
| A6 | Extractive answers are good enough to be the default path rather than a fallback | Phase 5 | ◐ dense-only Hit@1 is 0.362 en / 0.224 hi, so the naive top-passage answer is usually wrong. Rests entirely on the Phase 5 reranker. |
| A7 | An India-region always-on container is available on the free or cheap tier of the chosen host | Phase 0 | ✓ **TRUE** — GCP Compute Engine `n2-standard-2`, `asia-south1` (Mumbai), on $300 trial credits. See reversal R3. |
| A11 | ONNX int8 inference on ARM (Ampere A1) hits the same latency as x86 | Phase 2 | ~~open~~ **MOOT.** Retired by R3: GCP is x86, so the question no longer arises. |
| A12 | $300 of GCP credit outlasts the judging window | Phase 7 | ◐ ~$70/mo projects to ~4 months (mid-Dec) against a mid-Sept judging need. Budget alert required. |
| A8 | Sarvam free credits cover the full build plus demo recording | Phase 4 | ◐ key verified live 14 Aug; remaining credit balance not yet checked |
| A10 | Groq free-tier limits allow a Band B benchmark of useful size | Phase 5 | ✗ **FALSE as stated** — 12,000 tokens/window caps it. Band B must be a ~50-query sample, stated in the methodology. |
| A9 | The 200ms budget's 25ms reserve is enough to absorb tail jitter | Phase 5 | ◐ early evidence: a do-nothing stub already shows a 2.7ms P99→P100 gap from scheduler jitter alone |

---

## Prompt for a cold session

Paste this when starting a fresh AI coding session on this project:

> You are working on team OK4T's HH Goa 2026 Task 2 submission: a voice-enabled RAG system with a 200ms latency target on the core pipeline.
>
> Read these files first, in order: `Memory.md` (context and decisions), `Rules.md` (hard constraints), `Phases.md` (find the current phase), `Architecture.md` (the design), `Latency.md` (the budget).
>
> Key context: the fast path makes zero network calls. Extractive answering when reranker confidence is high, Groq LLM fallback when moderate, abstention when low. No LangChain. No hosted vector DB. No hosted embeddings. Everything in-process on ONNX int8.
>
> Tell me which phase we are on and what its exit criterion is before writing any code.
