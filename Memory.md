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
| Build machines | Three. BENCH i5-12400F CPU-only (reference), EMBED 3060 Ti, LLM 5070 Ti. See `Devices.md`. |

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

### 18 Aug 2026 (evening) — C1 rebuilt with the J10 filter; degenerate-passage removal confirmed working

The canonical C1 index was accidentally overwritten twice during `--limit` smoke tests of the new registry dispatch (fixed the same day: `--limit` now writes to `<strategy>-smoke/`, never the canonical path). Rebuilt clean, now through J10's degenerate-passage filter for the first time.

| | before (Phase 2) | after (J10 rebuild) |
|---|---|---|
| passages | 295,890 | **295,888** (-2, filtered) |
| chunks | 379,242 | 379,240 |
| index.bin | 655 MB | 655 MB |
| en Recall@10 | 0.870 | 0.870 (unchanged) |
| hi Recall@10 | 0.682 | **0.702** (+0.020) |

English is identical, which is the expected null result. Hindi improved by a small but real margin - one of the two filtered `-` passages was Hindi and had been acting as a low-quality attractor exactly as `ISSUES.md` I10 predicted. Confirms the filter does what it was built for, on real data rather than only in the design doc.

`slice_records_sha256` matches the frozen corpus, so the rebuild is bound to the correct slice despite going through a different code path (registry dispatch) than the original Phase 2 build.

### D9: Phase 3 splits across three machines, by resource rather than by strategy count
**Date:** 18 Aug 2026

Three boxes became available: an i5-12400F with a GT 710 (CPU only in practice, the GT 710 is below every current CUDA wheel's floor), a Ryzen 7 with an RTX 3060 Ti, and a Core Ultra 9 with an RTX 5070 Ti.

The obvious split is three strategies each. That is wrong, because the eight strategies do not cost the same thing. C5 and C6 need **no new embeddings at all** (C5 changes the payload and filter over C1's vectors; C6's children are the C1 chunks and its parent layer is a `query_id` lookup table), while C4 needs an LLM pass over 295,890 passages and C2 needs sentence-level embedding of the whole corpus.

So the split is by resource: GPU-embedding jobs to EMBED, LLM and whole-passage-encode jobs to LLM, zero-embedding and CPU-lexical jobs to BENCH. Full board in `Phase3-Parallel.md` §2.

**The larger reason this matters is scheduling, not throughput.** Most of Phase 3 is unattended compute. Running it on two spare boxes lets Phases 4 and 5 start on BENCH before Phase 3 closes, which is the only realistic recovery from the three-day slip in `ISSUES.md` I11.

**Reversal condition:** if the coordination overhead across three branches exceeds the compute saved, collapse back to sequential builds on BENCH. The cost of that fallback is roughly 30 minutes per strategy, which is survivable.

### D10: index vectors may be built on GPU in fp16; query vectors stay ONNX int8 on CPU
**Date:** 18 Aug 2026

The build bottleneck is embedding: 379,242 C1 chunks took 30.8 minutes at 211 chunks/sec on ONNX int8 CPU. Eight strategies, several with two to three times C1's chunk count, is many hours of CPU that the schedule does not have. A 3060 Ti running the same model in fp16 is roughly an order of magnitude faster.

`Rules.md` §3.1 already allows `sentence-transformers` **for offline index building and eval only**, so this is inside the rules rather than a deviation. The hot path is untouched: `Rules.md` §2.1 still bans PyTorch at request time, `retrieval/embedder.py` is not modified, and the GPU path lives in an offline-only `scripts/_gpu_embedder.py`.

This does mean the index holds fp16 GPU vectors while the served query embedder is int8 CPU. **That pairing is gated, not assumed.** Rebuild C1 on GPU over the identical chunk list, evaluate with the identical int8 CPU query embedder, and compare against the Phase 2 numbers already on disk (en Recall@10 0.870, Hit@1 0.362). Pass is a Recall@10 delta within 0.005 and a Hit@1 delta within 0.010 on both languages.

Same gate shape as the int8-versus-fp32 check in `03_export_onnx.py`, and for the reason recorded in the Phase 2 entry: raw vector cosine is a meaningless test on this corpus because the rank-10 to rank-11 similarity gap is 0.00137, smaller than the perturbation being measured. Compare on retrieval or do not compare.

**Reversal condition:** the gate fails. Fall back to CPU builds on all three boxes.

### D11: no offline corpus processing touches Groq
**Date:** 18 Aug 2026

`Phases.md` specified C4 as an offline LLM decomposition run overnight on the slice. Sized against the actual corpus: 295,890 passages at roughly 80 output tokens each is about 24 million output tokens. `ISSUES.md` I7 records Groq's free tier at 12,000 tokens per window.

**C4 through Groq is not slow, it is arithmetically impossible.** This was never going to work and would have been discovered mid-phase.

C4 moves to a local 3B to 7B instruct model on the 5070 Ti. The standing rule from here: **Groq tokens are spent on the Phase 5 runtime fallback path and on the roughly 50-query Band B benchmark, and nowhere else.** Any offline job needing an LLM runs on local hardware.

This is the clearest single justification for the three-machine split. It converts C4 from impossible to an overnight job while *preserving* the quota for the part that is actually scored.

**Reversal condition:** none. The arithmetic does not change.

### D12: index build time is demoted from a comparison metric to an annotation
**Date:** 18 Aug 2026

`Phases.md` Phase 3 lists build time as one of four comparison columns. Across three machines and two backends, that column compares hardware, not strategies.

Cost is reported instead on machine-invariant quantities: chunks emitted, tokens embedded, `index.bin` size, and projected serving RAM. Wall-clock is still recorded, tagged with `device_tag` and `backend` in `meta.json`, and presented as an annotation rather than a ranking.

Stated plainly in the README rather than quietly dropped, per `Rules.md` §1's no-dishonest-measurement rule. A hardware-tagged timing table is a stronger artifact than a uniform one that quietly averages three different machines.

**Reversal condition:** none.

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
- **Thread count for the build is 8, and it was worth measuring properly.** On the i5-12400F (6 physical / 12 logical), against **real C1 chunk texts**: 8 threads 213.0 chunks/sec, 12 threads 208.7 (**−2.0%**), 16 threads 61 (genuine oversubscription, 3.4x slower). A *synthetic* sweep using uniformly short English strings had shown 12 threads 11.6% **faster** and nearly caused a config change in the wrong direction. Real C1 chunks are p50 72 tokens; the synthetic strings were 5-10x shorter, so each batch already saturated all 6 physical cores at 8 threads and the extra logical threads only added contention. **Benchmark the real workload — a convenient stand-in gave a directionally wrong answer.** Full record in `ISSUES.md` I6.
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
**Date:** 19 Aug 2026 | **Who:** BENCH | **Branch:** `main` (uncommitted)
**Status: gateway built and verified against live Sarvam. Browser recorder and UI
still to come.**

**What happened**
`services/stt_gateway` built: config, Sarvam client, energy VAD, and a FastAPI
service exposing a batch endpoint, a frame-streaming WebSocket, and a health check.
202 tests green, `mypy --strict` clean across both services.

**Why this approach — a reliable floor before the impressive path**
Council review (19 Aug) made the case: requirement 1 was scoring zero, it was the
only remaining item with a genuine unknown, and the realtime socket is the riskiest
part of it. So the order was inverted deliberately. The BATCH path (one buffered
utterance, one documented HTTPS POST) went in first, and server-side VAD segments
utterances so the browser still gets a hands-free experience without betting
requirement 1 on the realtime socket behaving. The realtime relay layers on top.

**Verified against the live API, without a microphone.** Sarvam TTS was used to
synthesize speech and feed it straight back through our own STT path — a real
round trip in both languages, repeatable in CI-less conditions and reusable as
demo footage:

| | said | heard | conf | STT |
|---|---|---|---|---|
| en-IN | "How tall is Mount Everest?" | exact match | 0.991 | 911 ms |
| hi-IN | "एफिल टॉवर कितना ऊंचा है?" | "Eiffel Tower कितना ऊंचा है?" | 0.851 | 527 ms |

**Surprises and gotchas**
- **Sarvam authenticates with `api-subscription-key`, not a bearer token.** An
  `Authorization: Bearer` header returns a 401 that reads like a bad key.
- **`input_audio_codec` is REQUIRED for raw PCM.** The endpoint sniffs container
  formats; raw samples have nothing to sniff, and omitting the tag produces a
  decode error that looks like corrupt audio rather than a missing parameter.
- **Sarvam transliterates proper nouns into Latin script inside Hindi output** —
  "एफिल टॉवर" came back as "Eiffel Tower". This is correct code-mixed behaviour and
  it is a RETRIEVAL concern, not a transcription one: our Hindi passages are
  Devanagari throughout, so a spoken Hindi question can arrive carrying Latin-script
  entities that the indexed text never contains. Worth measuring against the Hindi
  index before the demo; `multilingual-e5-small` may absorb it, or may not.
- STT costs 527–911 ms. That is **Band C** and is reported separately: Latency.md 1
  starts the Band A clock at the transcript. A judge will time from when they stop
  speaking, so the boundary has to be visible on screen rather than argued in a
  README.
- **`python-multipart` is a hard runtime dependency of this service and nothing
  imports it.** `POST /v1/stt/file` takes an upload, and FastAPI resolves
  `Form`/`UploadFile` parameters when it REGISTERS the route rather than when the
  route is called - so the missing package did not break one endpoint, it stopped
  the whole gateway from starting. Found by booting the service; **198 unit tests
  were green at the time.** The suite imported `sarvam`, `vad` and `config` and
  never `main`, so nothing it covered could have caught it.

  Two lessons, and the second is the general one. Declare dependencies that only
  the framework imports - they are the easy ones to omit precisely because no
  `import` statement points at them. And **a green suite is not evidence a service
  runs**: `tests/test_stt_gateway.py` now imports both apps and asserts their
  routes are registered, which is the cheapest available proxy for "uvicorn can
  start this".

**Open threads**
- Browser recorder (`getUserMedia` + AudioWorklet, 48 kHz → 16 kHz PCM16) unbuilt.
  This is the remaining unknown in requirement 1.
- The realtime relay (`/v1/stt/live`) is specified in `sarvam.py` but not wired;
  the batch and frame-streaming paths cover the requirement without it.
- Latency.md 5's speculative prefetch depends on realtime partials and is therefore
  still hypothetical. It should not be claimed until it is measured.


### [Phase 5] Reranking, routing and calibration
**Date:** 19 Aug 2026 | **Who:** BENCH (i5-12400F) | **Branch:** `main` (uncommitted)

**What happened**
Cross-encoder reranking, confidence routing, a Groq fallback with a circuit
breaker, and calibrated thresholds. Both candidate rerankers were fetched with a
parity gate, compared on en+hi, and the depth chosen off a measured curve. Two new
issues found (I24, I25), one closed (I9). 158 tests green, `mypy --strict` clean.

**Why this approach**
Every number below came from a comparison run in ONE process over ONE query list,
because Phase 3 taught that a table assembled from separate runs compares run
settings rather than the thing under test (I21/I22). The reranker harness reuses
that discipline wholesale.

**Numbers**

Retrieval quality, 300 dev queries, paired bootstrap vs the same candidates in
dense order:

| arm | en Hit@1 | Δ en | hi Hit@1 | Δ hi |
|---|---|---|---|---|
| dense, no rerank | 0.360 | — | 0.233 | — |
| mono (English-only) d10 | 0.447 | +0.087 * | **0.120** | **−0.113** * |
| **multi d5 (shipped)** | 0.393 | +0.033 | 0.307 | **+0.073** * |
| multi d10 | 0.397 | +0.037 | 0.313 | **+0.080** * |

`*` = 95% CI excludes zero.

Band A, 250 frozen queries, 30 warmup discarded, BENCH:

| | P50 | P90 | P99 | P100 |
|---|---|---|---|---|
| en, dense only | 3.25 | 3.81 | 4.37 | 4.66 |
| **en, reranked** | **59.99** | 75.10 | 113.96 | 118.79 |
| **hi, reranked** | **73.77** | 95.61 | 135.50 | 155.92 |

Band B (generative path forced), 12 queries: P50 **653.6 ms**, P100 815 ms.

**Surprises and gotchas**

- **The Rules.md default reranker is actively harmful here.** English-only
  `ms-marco-MiniLM-L-6-v2` wins English outright and drives Hindi to 0.120 against
  a 0.233 no-rerank baseline — worse than not reranking, monotonically worse with
  depth. Replaced with `mmarco-mMiniLMv2-L12-H384-v1` (XLM-R, mMARCO-trained).
  Rules.md 3.3 updated under its own "benchmark before deviating" clause.
- **Depth 20 was wrong; depth 5 ships.** Quality is flat 5→10 and *falls* by 50,
  while cost is linear: 59 / 114 / 249 ms P50. Deeper reranking gives the model
  more chances to promote something above the right passage.
- **Batching is slower AND less reproducible.** I24: int8 activation scales are
  derived per tensor at run time, so padding makes a pair's score depend on its
  batch neighbours (0.279 logits against a 0.364 adjacent-rank gap). Batch size 1
  removes that — and is 32% faster at 2 threads, because padding waste exceeds the
  batch parallelism. The reproducible configuration and the fast one coincided.
- **Stage timeouts do not work for synchronous stages (I25, P0).** Measured: a sync
  stage with a 50 ms timeout ran 123.7 ms and reported `ok`; an awaiting stage was
  cut at 47.4 ms. `asyncio.wait_for` only fires at an await point and ONNX never
  yields. `Latency.md` 4.1's "guarantee rather than an average" rests entirely on
  the pre-stage gate. The reranker now takes a deadline it checks between pairs —
  possible only because I24 already forced one-pair-at-a-time scoring.
- **Groq retired both models this project had verified.** `llama-3.3-70b-versatile`
  and `llama-3.1-8b-instant` now 404 with `model_not_found`; the 14 Aug entry
  recording them as live is stale. Re-check a provider's model list at the start of
  any phase that depends on it. Now on `openai/gpt-oss-20b`, measured correct and
  cited in both languages.
- **A reasoning model broke the abstention check.** `qwen/qwen3.6-27b` opens with a
  `<think>` block that eats the whole 160-token cap, and quotes the abstention
  sentinel while reasoning — which `if INSUFFICIENT in text` read as a refusal.
  The check is now anchored to the start of the cleaned response.

**The finding that matters most, stated carefully**

The reranker **closed most of the Hindi gap and left English roughly where it
was.** Hindi is +0.073 and significant; English is +0.033 with a CI spanning zero.
It is not "the reranker fixed ranking".

Calibration then split the same way. `tau_low = -1.103` catches **100% of
genuinely-unanswerable queries and 100% of gibberish** at a 5% false-abstention
cost, with answerable-correct at median +8.30 against unanswerable at -7.28.

**CORRECTED after council review, same day — see ISSUES.md I26.** The first
version of this entry called that signal "excellent" and said it vindicated
Architecture.md 3.6. Both numbers are true; the conclusion was not, and it is the
Phase 3 mistake repeated: a real measurement generalised past the population it
was measured on.

Re-analysed from the same calibration file: **92.5% of WRONG top-1 answers score
above the floor and are answered anyway, and 62.1% of everything the system
answers is wrong.** The two negative populations differ enormously in difficulty -
a topically *unrelated* pool scores a median -7.28 and is trivially caught, while a
topically *related but wrong* pool scores +5.89, just under a correct answer's
+8.30 and far above the floor. Only the easy population was measured.

**So `tau_low` is an excellent out-of-domain detector and a poor grounding
detector.** Requirement 6 is genuinely satisfied for off-topic, gibberish and
unanswerable-from-corpus input - three of Phase 6's five adversarial categories -
and genuinely NOT satisfied for the common case where retrieval returns something
plausible and wrong. Quote the 100% figure only with its population attached.

It remains a real answer to I3, where dense cosine separated gibberish from a
correct answer by 0.05 and this separates them by ~15 logits. That claim was about
out-of-distribution input and survives intact. The broader claim does not.

The extractive signal is **poor**. Top-1 precision never reaches the 0.75 target —
it peaks at 0.508 at 37% coverage and falls after. **Assumption A6 is false and
D2's reversal condition is triggered**, not narrowly avoided. `tau_high` was set to
1.877 (85% extractive / 10% generative / 5% abstain) as a deliberate judgement:
buying precision with coverage would route the majority of traffic to a free tier
that serves ~12 calls per window (I7), which is the same arithmetic that killed C4.

Corroboration from an independent direction: on Band B, `gpt-oss-20b` given the
top-3 passages returns `INSUFFICIENT_CONTEXT` on **50%** of queries. Our own LLM,
reading the retrieved context, agrees it usually does not contain the answer.
**Retrieval, not ranking, is the remaining ceiling** — which is the opposite of
what Phase 3 concluded and should be stated that way rather than smoothed over.

**Open threads**
- **A6 / D2 resolved 19 Aug by council review: build the mode toggle (D2's own
  named fallback), and reframe the product as "cited evidence with calibrated
  confidence" rather than "answers".** The toggle is one request enum plus one UI
  control because the router already carries both bands; it also becomes the
  requirement 3/4 demo, since a judge can flip it and watch 654 ms collapse to
  60 ms. Rejected: routing more traffic to the LLM, which is incoherent on our own
  evidence (40% top-1 and 50% INSUFFICIENT_CONTEXT are one measurement seen twice)
  and inoperable on a 12-call window.
- **I26 makes Phase 6's OUTPUT guard load-bearing rather than decorative.** The
  retrieval-score floor cannot catch the 62.1%; only checking groundedness against
  the answer text can. Do not skip it on the assumption requirement 6 is banked.
- `Latency.md` 4.1's guarantee now rests on the pre-stage gate plus one in-stage
  deadline. `embed_query` is still uninterruptible, which is the real mechanism
  behind I1 — the Phase 6 input guard is now the *only* thing bounding it.
- Hindi P100 is 155.92 ms against a 200 ms budget on a 6-core box. The GCP target
  is 2 vCPU (one core plus a hyperthread). **This may not fit there.** Re-measure
  before publishing (I8), and depth 3 is the lever if it does not.
- Groq's `meta-llama/llama-prompt-guard-2-86m` is available and is purpose-built
  for prompt-injection detection — directly relevant to Phase 6 Layer 1, though it
  is a network call and so cannot sit on the hot path.
- BM25 and RRF fusion are still not wired into the pipeline. I19 said fusion does
  not pay for itself at the reranker's depth; at depth 5 that argument is stronger,
  not weaker, and the decision should be recorded rather than left implicit.


### [Phase 6] Guardrails
**Date:** 20 Aug 2026 | **Who:** BENCH | **Branch:** `front-v1`
**Status: PARTIAL. Layers 1 and 4 built, tested and live. Layer 2 measured and
deliberately not built. Before and after both measured against the live service.**

**What happened**
`guardrails/input_guard.py` and `guardrails/output_guard.py` written test-first,
wired into `build_pipeline()` as the first and last stages, and
`bench/adversarial.jsonl` built at 76 cases. 221 tests green, `mypy --strict`
clean across 40 files. `scripts/06_eval_guardrails.py` reports per-category
abstention precision and recall, which is the Phase 6 exit criterion.

**Layer 1, the input guard.** Empty check, then a 512-character pre-filter, then
a 64-token bound, then prompt-injection and unsafe-intent patterns. The ordering
is the design: I1 measured the character check at 0.00007 ms against 0.04228 ms
to tokenize, so the cheap one runs first, and the token bound is the actual
safety limit because cost is linear in tokens and characters-per-token is
script-dependent.

Verified against the real tokenizer over the frozen benchmark: **499 of 500
queries accepted, 1 rejected** — `query_id=156297` at 7,168 characters and
**2,390 tokens**. The largest legitimate query is 25 tokens, so the bound clears
real traffic by 2.5x. Worth noting that I1 records this query as 512 tokens,
which is the count *after* the embedder truncates; the raw count is 2,390.

This closes the oldest latency hole in the project. I25 established that a stage
timeout cannot interrupt synchronous ONNX work, so until this existed that query
had no bound on it at all.

**Layer 4, the output guard.** Content-word recall averaged with adjacent-pair
recall, against the cited passages, plus a citation-index validity check. I26 is
why it exists: the abstention floor is an out-of-domain detector and lets 92.5%
of wrong answers through, and nothing else in the pipeline reads the answer text.

**The measured limit of that measure, recorded because it is easy to overclaim.**
On one worked example: a verbatim span scores 1.000, a FALSE sentence reassembled
out of the passage's own words scores 0.833, a TRUE paraphrase scores 0.639, and
an unsupported answer scores 0.062. **The false reassembly outscores the true
paraphrase.** Lexical overlap measures whether the wording is traceable to the
source, which is not the same question as whether the answer is right. So the
floor is set at 0.35 to catch the bottom of that list and nothing more, and
`tests/test_output_guard.py` pins the inversion so nobody later describes this as
a hallucination detector.

**Layer 2 was built as a measurement and then rejected. See I27.**
The score-gap ambiguity check is specified in `Architecture.md` 7 and does not
survive its own data: catching 5 of 9 ambiguous cases costs 4 of 14 real
questions. The distributions interleave rather than merely overlap — the real
question "what happens during a docket call in court" has a gap of 0.07, smaller
than the single word "mercury" at 0.08. The language-mismatch flag is rejected
separately and on design: answering a Hindi question from the English twin is
this project's cross-lingual claim, so a guard there would refuse the headline
capability. Both reasons live in `retrieval_guard.py` so the absence reads as a
decision.

That makes three components now killed by their own measurement rather than
shipped because a design document named them: I3 (dense-score floor), I19 (RRF
fusion), and now I27.

**Numbers — 76 cases, before and after, same set, same service, restarted between**

| category | n | before | after |
|---|---|---|---|
| injection | 12 | 100% | 100% |
| unsafe | 12 | 83% | **100%** |
| off_topic | 12 | 75% | 75% |
| unanswerable | 12 | 75% | 75% |
| **ambiguous** | 12 | **25%** | **25%** |
| answerable (control) | 16 | 12% false abstention | 12% |

| | recall | precision | F1 |
|---|---|---|---|
| before | 0.717 | 0.956 | 0.819 |
| after | **0.750** | 0.957 | **0.841** |

**Read past the headline, because recall moved only +0.033 and that is not the
result.** Two other things moved and they matter more.

**Refusals are now for the right reason.** Before, all 45 refusals came back
`LOW_CONFIDENCE` — including every injection and every unsafe case. Those were
being caught *by accident*: a bomb-making question retrieves badly, so the
Phase 5 retrieval floor happened to fire. That is luck wearing a guardrail's
uniform. After: 24 `LOW_CONFIDENCE` and 23 `UNSAFE_INPUT`, named and
deterministic. Requirement 6 asks the system to show it knows when *not* to
answer, and "the retrieval score was low" is not knowing.

**Refusing got much cheaper.** Median latency of a refused request fell from
**75.96 ms to 45.01 ms**, because a guard-blocked input exits in 0.1 to 0.3 ms
instead of paying for an embedding and a rerank first. Measured live: over-long
0.3 ms, injection 0.1 ms, unsafe 0.1 ms, against 83.8 ms for gibberish that
still goes the full distance to the retrieval floor.

`groundedness` is populated and reaches the API: a normal question answered in
49.1 ms reports 1.0, which is the extractive path's structural guarantee shown as
a number rather than asserted.

**One thing the live run exposed: the I1 query never reaches this guard over
HTTP.** `AnswerRequest.query` carries `max_length=2000` from Phase 2, and the
pathological query is 7,168 characters, so pydantic rejects it with a **422**
before the pipeline starts. It is bounded, which is what matters for I1, but it
is bounded as a transport error rather than as a typed refusal — so a judge who
pastes something enormous sees an error, not the abstention panel. The guard
covers everything between 512 and 2000 characters plus the token bound inside
that range, which is where an adversarial dense-script input would actually sit.
Deliberately not changed: the request contract was frozen in Phase 2 precisely so
it would not move under the frontend, and two bounds at different layers is a
reasonable design. Worth a decision before submission rather than a silent one.

**Deviation, recorded per Rules.md 9: no `guardrails/policies.yaml`.**
`Phases.md` asks for one. The thresholds already live in `config.py` with the
measurement that set each one written above it, and `DONT-FORGET.md` 9 records
what happened the last time this project had two sources for one number: a
figure was cited to a file that did not contain it, which is indistinguishable
from fabrication to anyone who checks. A YAML duplicating `config.py` would
recreate that failure by construction. One source, with its calibration beside
it, is the better version of what the rule was asking for.

**Surprises**
- The adversarial eval immediately found the weakest category, and it was not
  one of the two the phase was designed around. Ambiguity at 25% is a real
  weakness that the guards built here do not address, and I27 explains why the
  specified fix would cost more than it buys. It is reported per category rather
  than averaged away.
- Adding the output guard broke one existing test, correctly. A routing test's
  fake model returned the placeholder "composed answer", which is grounded in
  nothing, so the new guard refused it. The fix was to make the fake realistic
  rather than to weaken the assertion.
- `Rules.md` 3.3 allows a keyword list as the fallback for the ONNX toxicity
  classifier, and that is what shipped. The control group is what makes it
  defensible: nine legitimate questions about weapons, medicine, crime and
  hacking must all pass, because a corpus of web passages legitimately covers
  those subjects and a filter keying on topic rather than intent would refuse
  them.

**Open threads**
- Decide whether a query over 2,000 characters should 422 or abstain. It is
  bounded either way; the question is whether the demo shows an error or the
  refusal panel.
- Layer 3, the generation guard, is partly present already: the system prompt
  constrains the model to the passages and the abstention sentinel is handled in
  `generative.py`. The schema-repair retry named in `Phases.md` is not built.
- The interface does not yet show `groundedness`. The field is populated in the
  response and `Confidence.groundedness` has been in the contract since Phase 2.
- Ambiguity remains an open weakness, stated rather than closed.

### [Phase 7] Deploy and harden
**Date:** 20 Aug 2026 | **Who:** BENCH | **Branch:** `front-v1`
**Status: LIVE at https://shrutirag.duckdns.org, and the 200 ms claim does NOT
hold on the deploy target. That is the finding.**

> **SUPERSEDED THE SAME DAY. Read this entry, then the second Phase 7 entry at
> the end of this section.** Everything below is accurate about what was measured
> and wrong about what it meant: the cost it attributes to the deploy target was
> mostly a thread-pool defect in our own process (`ISSUES.md` I28). The 200 ms
> claim now holds, at every percentile, in both languages, over 998 requests.
> The entry is kept because the reasoning it contains is the reasoning that
> found the defect, and because two decisions taken here were later reversed
> (R4) — deleting it would hide the part worth learning from.

**What happened**
`rag_core` and `stt_gateway` under systemd on the GCP Mumbai VM, Caddy on 443
serving the static site and reverse proxying both services, real Let's Encrypt
certificate, DuckDNS hostname. Configs are versioned in `deploy/etc/`.

**THE NUMBER, measured on the deployed box, 250 frozen queries, 30 warmup discarded**

| | P50 | P70 | P90 | P99 | P100 |
|---|---|---|---|---|---|
| en, i5-12400F | 59.99 | 65.18 | 75.10 | 113.96 | 118.79 |
| **en, n2-standard-2** | **190.47** | **198.31** | 216.12 | 247.00 | 250.90 |
| hi, i5-12400F | 73.77 | 80.85 | 95.61 | 135.50 | 155.92 |
| **hi, n2-standard-2** | **200.87** | 208.98 | 221.72 | 250.77 | 256.57 |

**Roughly 3x slower, and the budget is missed.** English P50 clears 200 ms with
9.5 ms to spare and its P70 is 198.31, which is the line. Hindi P50 is already
over at 200.87. Every P90 and P100 is over.

This is exactly what the Phase 5 entry predicted: *"Hindi P100 is 155.92 ms
against a 200 ms budget on a 6-core box. The GCP target is 2 vCPU. This may not
fit there. Re-measure before publishing (I8), and depth 3 is the lever if it does
not."* The prediction was right and the lever is now needed.

`ISSUES.md` I8 is closed by this measurement: every figure published before today
came from a machine the product does not run on.

**Why it is slower, and it is not a surprise**
The box is an Intel Xeon at 2.80 GHz against the i5's ~4.4 GHz boost, and it has
2 vCPU meaning one physical core plus a hyperthread against six real cores. The
reranker is 94% of the budget spent and it is the part that scales with both.
`avx512_vnni` IS present, so the int8 models are hitting their fast kernels and
this is not a quantization fallback.

**Levers, in the order they should be pulled**
1. **Resize to `n2-standard-4`** and set `ONNX_THREADS_SERVING` from 2 to 4.
   Costs money, costs no quality. Quota is not a constraint: `N2_CPUS` limit is
   200 with 2 in use. ~$140/mo against ~$70, and the $300 credit still covers
   the mid-September judging window.
2. **Rerank depth 5 to 3.** Free, and costs quality: the Phase 5 depth sweep has
   the curve.
3. `ef_search` 64 to 48. Smallest effect, costs recall.

Take them in that order, and re-measure after each rather than stacking them.

**Surprises and gotchas, each of which cost real time**
- **The frontend hardcoded `http://127.0.0.1:8000`.** A deployed page pointing
  there asks the VISITOR's machine for an answer. It worked in development for
  the obvious reason and would have failed for every judge. Fixed by computing
  the base URL from `location.hostname`; see the `[P7]` commit.
- **Caddy exits 1 if told to log to a file under `/var/log/caddy`.** The
  packaged unit has a sandboxed `ReadWritePaths`. It fails *before* requesting a
  certificate, so the symptom is "no HTTPS" rather than "cannot write log".
- **Serving a web root out of a home directory returns 403.** Home is `0750` and
  the `caddy` user cannot traverse it. Web root is `/var/www/shruti`.
- **`gcloud compute ssh` on Windows generates a key labelled
  `DESKTOP-<name>dmin`.** A backslash is not valid in a Linux username, so the
  server refuses it, and the error is `Server refused our key` rather than
  anything about the username. Fixed by registering the key under the real
  account at instance level, which leaves any Cloud Shell key untouched.
- **`pscp` does not expand `~`.** `gcloud compute scp` to `~/dir/` fails with
  `unable to open ~/dir/: no such file or directory`. Use an absolute path.
- **The repo is private, so the VM cannot clone it.** Shipped `git archive`
  output instead, which is 2.7 MB and needs no credentials on the box. The repo
  has to become public before submission anyway (`Submission.md` S2), after the
  secret scan.
- Home upload ran at roughly 0.5 MB/s, so 1,017 MB took about half an hour. Only
  `index.bin` and `chunks.parquet` genuinely have to travel from a laptop: the
  ONNX models are fetched from Hugging Face by the export scripts, and the VM's
  link to HF is far faster. Worth knowing if this ever has to be redone.

**No Docker.** `Phases.md` names a Dockerfile; a venv plus systemd plus Caddy is
fewer moving parts than building a 2 GB image and pushing it to a registry, and
the artifacts have to be copied either way. Recorded as a Rules.md 9 deviation.

**Open threads**
- **`.env` is not on the box.** It is gitignored, so the VM has no
  `SARVAM_API_KEY` and no `GROQ_API_KEY`. Text answering works; **voice does not,
  and the generative path is off.** `/api/stt/health` returns 503 `no_api_key`.
  This is the last thing standing between the deployment and a full demo.
- The resize decision above.
- Re-bench after any lever, and republish. Everything in `data.js` and on the
  documentation page is still the i5 number.
- Secret scan over full git history before making the repo public.

### [Phase 8] Demo surfaces and polish
**Date:** 19 Aug 2026 | **Who:** BENCH | **Branch:** `p4-p5-voice-rerank`
**Status: SUPERSEDED on 20 Aug. `apps/web` was replaced by the static site in
`frontends/` and deleted. Kept as the record of what it cost and what it taught,
particularly the resampler, which survived the change unaltered. The current
frontend entry is the next one.**

**What happened**
`apps/web` built: Next.js 15, React 19, TypeScript, no component library. Browser
recorder (getUserMedia -> AudioWorklet -> 16 kHz PCM16), mic orb with an
amplitude-reactive ring, latency waterfall, citation chips that expand in place,
abstention panel, confidence readout, fast/accurate mode toggle, and the F16 text
fallback on the same endpoint. Build clean at 110 kB First Load JS.

**The resampler is the part that could have shipped silently broken**
`Phases.md` warns "resample properly, do not just drop samples". The reason it
matters here specifically: decimating 48 kHz to 16 kHz without filtering folds
everything above 8 kHz back into the speech band, and sibilance and Devanagari
retroflex consonants carry real energy there - so the damage lands exactly on the
sounds an Indic STT model needs, and it does not sound broken on a laptop speaker.
It just quietly costs Hindi accuracy.

`public/pcm-worklet.js` therefore low-passes before resampling: a 63-tap
Blackman-windowed sinc at 7.4 kHz, then fractional-position resampling with linear
interpolation. **The filter is designed at run time from the real AudioContext
sampleRate**, not hard-coded for 48 kHz - devices report 44.1 kHz too, where the
ratio is 2.75625 and integer decimation is not even available.

**Verified in a real browser against both live services**

| query | result | Band A |
|---|---|---|
| English | EXTRACTIVE, 3 citations, confidence 8.68 | 58.0 ms |
| Hindi | EXTRACTIVE, Devanagari answer and citations | 83.4 ms |
| gibberish | **ABSTAINED** `LOW_CONFIDENCE`, -4.908 vs the -1.103 floor | 91.3 ms |

Zero console errors. At 375 px the instrument column collapses BENEATH the stage
and stays visible, per `Design.md` 4.1 - hiding it would remove the only genuinely
unusual thing on the screen.

**Deviation, recorded per Rules.md 9: no Tailwind.** `Rules.md` 3.1 lists it. The
spec is exact pixel geometry, a hatched-bar texture, an amplitude-driven transform
and six keyframe animations - all plain CSS that utility classes would only wrap,
plus a config to keep in sync with `tokens.css`. The rule's intent (no component
library, nothing templated) is met; the tool is not. Overturn freely if a later
session wants it.

**The measurement boundary is stated on screen**, not only in the README: the
instrument column shows `stt` and `pipeline` as separate numbers. A judge times
from when they stop speaking, and a 200 ms claim that quietly excludes speech
reads as cherry-picking - which is worse than being slower.

**Open threads**
- **The microphone path has never run against real audio.** The gateway was proven
  with Sarvam TTS fed back through STT, but this box has no microphone, so
  getUserMedia -> AudioWorklet -> resampler is unexercised. If anything is wrong in
  Phase 4, it is there. Test it first on any machine with a mic.
- The realtime socket (`/v1/stt/live`) is still unwired, so partials and the
  `Latency.md` 5 prefetch remain hypothetical and must not be claimed.
- **F16's prominence is an open design question.** The text input currently sits
  below the answer as an ordinary form, which reads as co-equal to voice rather
  than as the fallback `Project.md` intends. Collapsing it behind a "No
  microphone? Type instead" link would keep the insurance while making the demo
  read voice-first. Not done; it is a judgement call, not a defect.
- Not built from `Phases.md` Phase 8: the live strategy toggle (F13), the
  failure-injection query param that forces a 429 to demo the circuit breaker, and
  the citation matched-span highlight.


### [Phase 8, continued] 20 Aug 2026 — the frontend was replaced, and `apps/web` deleted
**Date:** 20 Aug 2026 | **Who:** BENCH | **Branch:** `front-v1`
**Status: DONE. `frontends/` is the site. `apps/web` is gone.**

**What happened**
The Phase 8 surface above was a Next.js 15 application. It has been replaced by
`frontends/`: `index.html`, `docs.html`, one stylesheet, one console script and
a `_shared/` module directory, served by `python -m http.server` on :3000.
`apps/web` was deleted from the working tree. It is recoverable from git history
and nothing else in the repo depended on it.

**Why**
The thing a judge opens is a page, not an application. It has one screen, no
routing, no authentication, no data layer of its own and no state that outlives
a reload; every heavy decision already lives in `rag_core`. A framework was
buying a build step, a `node_modules`, a Node version in the prerequisites table
and a compile between typing and seeing, in exchange for nothing this surface
uses. Removing it removed all four. The prerequisites table now says Node is not
required at all, which is one fewer thing to go wrong on a machine that is not
this one.

The second reason is honesty about the numbers. `_shared/data.js` holds every
published figure exactly once and each block names the dated file under
`bench/results/` it came from, so the demo page, the documentation page and the
on-page console cannot disagree with each other. That property is worth more
than any component library.

**How it was built, and what got thrown away**
Eight complete interface treatments were built over one design pass, sharing a
single behaviour layer, and one was chosen. Seven were deleted on 20 Aug along
with the launcher that switched between them; the survivor was promoted to the
root of `frontends/`. `_backup/03-terminal-v1/` keeps the first draft of the
survivor, self-contained, so a rollback is a copy.

The split that made this cheap is still in place and should stay: `base.css`
sets structure and reads a token contract, `theme.css` defines it and holds
every visual decision. Deleting seven treatments cost nothing because no
treatment could break the citation expander or the abstention panel.

**The design**
A session log. Amber on black, monospace throughout, lowercase, regions framed
like a text user interface with the title on the top rule. Dark only, one
accent. Under the timing panel is a console you can type into: styling, not a
shell, nothing executes on your machine, and every figure it prints comes from
`data.js`. `help` lists the commands, `status` polls both services live,
`session` prints the percentiles for the queries you have run.

**Verified against both live services on 20 Aug**

| query | result | Band A |
|---|---|---|
| English | EXTRACTIVE, 3 citations, confidence 4.23 | 54.6 ms |
| Hindi | EXTRACTIVE, Devanagari answer and citations | 141.0 ms |
| gibberish | **ABSTAINED** `LOW_CONFIDENCE`, -4.908 vs the -1.103 floor | 101.0 ms |

Zero console errors. No horizontal page overflow at 375, 768 or 1280; the wide
tables scroll inside their own wrapper rather than stretching the page.

**Three bugs worth remembering, because each has a general form**

1. **The sticky section nav highlighted the wrong section.** It used an
   IntersectionObserver and lit the *first* section in document order still
   touching a band under the nav. At a boundary two sections touch it at once
   and the outgoing one is always the earlier of the two — so clicking a link
   left the *previous* entry highlighted, and landing exactly on a boundary is
   not an edge case, it is precisely what clicking a link does. Replaced with a
   position test: the last section whose top has passed the reading line. No
   tie, and correct at both ends of the page. **General form: an intersection
   test cannot answer a question about ordering.**

2. **Numeric table headers were left-aligned over right-aligned numbers.** It
   reads as fine at one width and as broken at every other, which is why it
   survived so long. Fixed with an explicit class on numeric headers, plus a
   `min-width` inside the scrolling wrapper so a narrow window scrolls the table
   sideways instead of compressing headers into three-line stacks.

3. **A `flex-basis: 100%` spacer forced the top bar to wrap on every screen
   under 900px**, whether or not the contents needed it to, which put the brand
   alone on the first line and every control on the second. The row fits on one
   line at 375px once the tagline and the repo link are dropped, and both of
   those are said again elsewhere on the page.

**One correction the site needed, found the same day**
The chunking table showed four rows and filed C5 and C6 under a prose note, which
read as though only four strategies had ever been built. Six were: C5 and C6 have
their own dated result files (`bench/results/2026-08-18-200054-retrieval-c5.json`
and `-200059-retrieval-c6.json`), their own index builds, their own rows in the
J15 paired comparison the table already cites, and 20 dedicated tests in
`tests/test_derived_chunkers.py`. The Phase 3 honesty note — that only four rows
are *independent evidence*, because C5 and C6 reuse C1's byte-identical index by
construction — is about the strength of the evidence, not about how much work was
done, and the page had collapsed the two. All six rows are now in the table, with
the two derived ones carrying a `reuses C1` marker and dimmed figures. Dropping
the rows hid work; showing them unmarked would have padded C1's column with its
own reflection. Both failures are avoidable at once.

**A read of the whole repo against the whole site, same day**
Four things came out of it, recorded because the class of error matters more
than the instances.

1. **C3 and C4 really were never built**, and the site was right to say so.
   `c3_semantic.py` raises on construction, `registry.py` holds both as
   `_Pending`, and `artifacts/indexes/` contains exactly `c1 c2 c5 c6 c7
   c7-leaky c8`. Three independent proofs, none of them a document. Worth
   writing down because "the docs say not built" and "it was not built" are
   different claims and only the second one survives a judge checking.
2. **I20, the C7 answer-key leak, was missing from the site entirely** —
   `Rules.md` 1 asks for it as a published finding and it is the strongest
   honesty story in the repo. Now on the documentation page under the chunking
   table, with the leaky row drawn in the refusal colour so it cannot be
   mistaken for a result.
3. **Two published numbers were cited to files that do not contain them.** The
   shipped `tau_high` is 1.877; the calibration JSON the page cited says 9.242
   and a 25/70/5 split. The override is deliberate and well argued in
   `config.py` — precision peaks at 0.508 and buying it with coverage would
   route most traffic to a free tier serving ~12 calls per window — but a reader
   who opens the cited file finds a different number and concludes it was
   massaged. Same shape in the reranker table, which draws depths 5 and 10 from
   one 300-query run and the English-only arm and depths 20 and 50 from another,
   while naming only the first. Both now name every file they use, and the
   threshold curve is on the page rather than buried in a config comment.
   **The lesson: "every figure names its source" is only worth anything if the
   named source contains the figure.** A correct number with a wrong citation
   is indistinguishable from a fabricated one.
4. **Retrieval, not ranking, is still the stated ceiling** and the site does not
   say it. On Band B, `gpt-oss-20b` handed the top-3 passages returns
   `INSUFFICIENT_CONTEXT` on 50% of queries. Left as an open thread rather than
   quietly fixed, because it is a claim about the system rather than a caption.

`DONT-FORGET.md` at the repo root carries all of this in the form a cold session
needs: what is easy to get wrong, and the file that proves otherwise.

**Rules that carried over and are still HARD**
No API key anywhere under `frontends/` (`Rules.md` 4): the browser talks to
`stt_gateway`, the gateway talks to Sarvam. Every number mono and tabular. The
abstention panel weighted equally to an answer and never styled as an error.
The measurement boundary on screen, with `speech` as its own readout beside
`pipeline` — a 200 ms claim that quietly excludes speech-to-text reads as
cherry-picking, which is worse than being slower.

**`Design.md` is now partly superseded** and carries a banner saying so. Its
thesis survived; its type stack, colour tokens and component names described
`apps/web` and do not describe this. The surviving rules are restated in
`HANDOFF.md` 5A.

**Port 3000 is still load bearing.** `stt_gateway` allows CORS from
`localhost:3000` only. On any other port typing works and speaking fails, with a
CORS rejection that reads exactly like a broken microphone. `run-dev.bat`,
`frontends/serve.bat` and the VS Code `web` task all now serve `frontends/` on
3000 instead of running `npm run dev`.

**The microphone gap closed the same day**
Phase 4 and Phase 8 both ended with `getUserMedia` -> AudioWorklet -> resampler
unexercised, because the build box has no microphone and the gateway had only
ever been proven by feeding Sarvam TTS back through STT. On 20 Aug it was run
for real, in a browser, by a person speaking:

| spoken | speech ms | pipeline ms | path | confidence |
|---|---|---|---|---|
| "What is the capital of Russia?" | 1016 | 65.2 | EXTRACTIVE | 5.01 |
| "Who is Donald Trump?" | 705 | 68.2 | EXTRACTIVE | 10.94 |

Both transcribed exactly and answered with three citations. **The whole capture
chain works**, including the windowed-sinc low pass that was the riskiest thing
in the frontend and could have shipped silently degrading Hindi.

Two things worth keeping from that run. Real microphone audio costs **more** than
the TTS loopback: 705 to 1016 ms against the loopback's 527 to 911 ms, so Band C
should quote both ranges rather than the friendlier one. And the Donald Trump
query, **spoken in English, returned a Hindi passage at rank 2** (`1002273:1:hi`,
10.37) beside its English twin at rank 1 (10.94) — cross-lingual retrieval firing
on live spoken input rather than on a constructed example, which `README.md` has
been calling "a checkable event rather than a demo anecdote" since Phase 1.

Two samples is a sighting, not a distribution. A proper Band C distribution is
still unmeasured and the page says so where it prints these.

**Open threads, unchanged by this work**
- The realtime socket (`/v1/stt/live`) is still unwired; partials and the
  `Latency.md` 5 prefetch remain hypothetical and must not be claimed.
- Not built: the live strategy toggle (F13), the failure-injection query param
  that forces a 429 to demo the circuit breaker, and the citation matched-span
  highlight.

**Where to continue.** *(Superseded by the Phase 6 entry below, 20 Aug: layers 1
and 4 are built and the eval runs. Phase 7 deploy is now the top priority, then
Phase 9.)* The frontend needs nothing further to be submittable.

### [Phase 7] Deploy, and the defect that had been hiding under the budget
**Date:** 20 Aug 2026 | **Who:** Claude Code session | **Branch:** `front-v1`

**What happened**
The stack went live at **https://shrutirag.duckdns.org** — Caddy on 443 with a
Let's Encrypt certificate, serving the static site from `/var/www/shruti` and
proxying `/api/core/*` and `/api/stt/*` to `rag_core` and `stt_gateway` on
loopback under systemd. Then Band A was measured *through it*, which
`Latency.md` 6 has required since Phase 0 and which had never actually been
done.

It failed. `n2-standard-2` gave en P50 190.47 ms and hi P50 200.87 ms against a
200 ms budget. Two levers were pulled from the `Latency.md` 8 list — resize the
instance, then cut rerank depth from 5 to 3 — and the second was documented
carefully, including the quality it cost. Nine of 499 requests were still over
budget.

**Neither lever was the fix, and one of them made things worse.** `rag_core`
holds two ONNX Runtime sessions and a request uses both; each had been given
`ONNX_THREADS_SERVING` intra-op threads, which on a 4-vCPU box is eight workers
for four cores. ORT's pool spins rather than sleeping when it finishes a task,
so the embedder was still burning cores while the cross-encoder ran. One thread
for the embedder took en P50 from 132.59 ms to 64.48 and made the embedder
itself faster, 14.29 ms to 8.40. `ISSUES.md` I28.

Depth 5 was then restored (reversal R4), the rerank deadline was made predictive
rather than reactive, and the box was resized to `n2-standard-8` running four
uvicorn workers — which buys concurrency rather than speed, because the
cross-encoder stops scaling at two threads and one uvicorn process serves one
request at a time (`ISSUES.md` I29).

**Published, through the deployed service, 250 frozen queries x 2 passes per
language, 30 warmup discarded**
(`bench/results/2026-08-20-141232-banda-deployed-FINAL-n2std8-w4t2-d5.json`):

| | P50 | P70 | P90 | P99 | P100 | over 200 ms |
|---|---|---|---|---|---|---|
| en | **95.89** | 103.44 | 117.61 | 152.48 | **183.35** | **0 of 500** |
| hi | **115.88** | 126.17 | 146.54 | 174.62 | **182.20** | **0 of 498** |

Band A holds at concurrency 1, 4 and 8. Client wall clock does not, and that is
reported as the queue it is rather than tuned away.

**Why this approach**
The rule that produced the finding is worth more than the finding. Every lever
on the optimization list was applied to a number nobody had explained, and the
list is not wrong — it was just being consulted at the wrong moment. Timing the
expensive component *in isolation* and comparing it against the same component
*inside the process* is a two-minute check that would have caught this in Phase
5, and it is now step zero of `Latency.md` 8, ahead of every lever.

The second habit worth keeping: the superseded reasoning was left in `config.py`
underneath the correction rather than deleted. A repo whose pitch is honest
measurement should show what it believed and why it stopped believing it.

**What this cost**
`n2-standard-8` is roughly 4x the burn of the original box. Deliberate for a
judging window measured in weeks, and A12 has been re-priced accordingly.
Resizing down after judging is a task somebody has to do, not an option.

**Also built, and switched off.** The realtime STT relay (`/v1/stt/live`) closes
assumption A3 — Sarvam's realtime model emits real partials, 19 over 5.8 s of
speech — but the live on-screen transcript is disabled. Under
`language_code=auto` the partials arrive romanised and only the final is
Devanagari; pinning `hi-IN` fixes that and corrupts the English final into
phonetic Devanagari, which would be the string sent to `rag_core`. `ISSUES.md`
I30.

**Where to continue.** Phase 9 (videos, posting, submission) is the only phase
left. `DONT-FORGET.md` 12 holds three decisions still waiting on a human, and 13
holds what is still open — Band B and the path distribution have never been
measured on the deployed box, and the rerank deadline's truncation rate is known
only at concurrency 1.

---

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

### [Phase 3] Chunking depth — the baseline holds, and the method was the contribution
**Date:** 18-19 Aug 2026 | **Who:** BENCH (i5-12400F) | **Branch:** `main`

**What happened**
Seven strategies evaluated against C1 on 500 dev queries. C4 killed on a cost
model (D11). C2 and C8 built and measured here; C5, C6, C7, BM25 and RRF fusion
were already in place. J15 replaced the comparison method itself.

**The result, stated precisely (corrected after council review).** Earlier
phrasing said "nothing beats C1", which overstates. The accurate statement is:
**C1 and C8 are statistically tied on English; C1 wins on Hindi; everything else
is significantly worse.**

**Only 4 of the 6 rows below are independent evidence.** C5 and C6 reuse C1's
byte-identical `index.bin` *by construction* - they change the payload and the
parent lookup, not the vectors - so their identical scores are a property of the
design, not three separate confirmations. Presenting six rows as six results
inflates the apparent method count and visually pads C1's column. Counted
honestly: **4 independently measured strategies (C1, C2, C7, C8), 1 reasoned-out
(C3), 1 killed on a cost model (C4).**

**Independently measured (distinct indexes):**

| strategy | en R@10 | hi R@10 | en Hit@1 | chunks | serving MB |
|---|---|---|---|---|---|
| **c1 fixed 96/24** | 0.878 | **0.714** | 0.356 | 379,240 | 1,080 |
| c2 sentence-window | 0.354 | 0.416 | 0.124 | 927,069 | 2,029 |
| c7 doc2query | 0.864 | 0.674 | 0.352 | 403,240 | 1,122 |
| **c8 late chunking** | **0.886** | 0.692 | **0.366** | 379,240 | 1,080 |

**Not independent — same index as C1 by construction:**

| strategy | en R@10 | hi R@10 | note |
|---|---|---|---|
| c5 metadata | 0.878 | 0.714 | C1's vectors, different payload/filter |
| c6 hierarchical | 0.878 | 0.714 | C1's chunks, plus a `query_id` parent lookup |

**Hit@1 matters more than Recall@10 for this product** and was missing from the
first version of this table. A voice assistant speaks ONE passage; the extractive
path returns the top hit. C8 leads on en Hit@1 (0.366 vs 0.356), though not
significantly.

Paired deltas vs c1 (same queries, 4,000 resamples): c2 **-0.524 en**, c7 -0.014 en
/ -0.040 hi, all significant and worse. c8 is +0.008 en (not significant) and
**-0.022 hi (significant, worse)**. c5 and c6 are exactly +0.0000.

**Decision: C1 stays the default** - but as a tie broken on Hindi and on risk,
not as a win. C8 matches it on English (and edges Hit@1), costs the same 655 MB,
and is significantly worse only on Hindi (-0.022). C1 is chosen because it holds
both languages, is the simplest thing that works, and is already the measured
Phase 2 baseline. **C8 is a live alternative, not a rejected one** - if the Phase 5
reranker changes the ranking picture, revisit it.

**Why this approach**
The comparison method mattered more than any strategy. Two of my own reported
findings turned out to be measurement artifacts, both caught by fixing the
harness rather than by re-running:

1. **Query-count artifact (I21).** The old table was assembled from separately
   dated eval JSONs whose runs used different `--limit` values. c1 at 250 queries
   scored 0.896, c8 at 500 scored 0.870, which read as C8 being 0.026 worse. On
   identical settings both score 0.870. The table compared sample sizes.
2. **Granularity artifact.** Scoring raw top-k CHUNKS penalises fine chunkers:
   chunks-per-passage varies 2.4x (c1 1.28, c2 3.13), so at k=10 chunks c2 can
   surface only ~3 distinct passages where c1 surfaces ~8. Under fair
   passage-level dedup, C8's apparent "+0.040 significant nDCG win" collapses to
   +0.009, not significant.

So J15 hoists everything shared out of the per-strategy loop - query list,
embedder, **query vectors**, k, language handling, gold-id logic - and scores
distinct passages. A strategy cannot be scored under different conditions than
its neighbours because no per-strategy conditions remain.

**Pairing is verified, not asserted.** c5 and c6 reuse c1's byte-identical
`index.bin` and return a paired delta of exactly `+0.0000 [+0.0000, +0.0000]`.
Only genuinely aligned per-query arrays produce an exact zero.

**Numbers**
- C8 build: 295,888 passages -> 379,240 chunks in 61.6 min, matching C1's count exactly (same spans, different context).
- C2 build: 927,069 chunks (3.13/passage) in ~17 min at 878 chunks/sec.
- C8 vector check before committing an hour: cosine 0.976 vs independent embedding, 0/11 identical, L2 norms exactly 1.0.

**Surprises and gotchas**
- **A pyarrow write broke every rebuild.** `Chunk.meta` was added for C5; every
  other strategy leaves it empty, and pyarrow refuses `struct<>` with no child
  fields. Builds failed *at the write*, after all embedding work was spent. The
  existing c1 index hid it by predating the field. Found on an 8,000-row smoke;
  it would otherwise have surfaced 73 minutes into C2.
- **C2 is genuinely weak, not broken.** Its chunks are real sentences averaging
  26 tokens against C1's 70.6. A single sentence carries too little signal here,
  and it costs 2.4x the index for -0.52 recall.
- **C5 and C6 are C1.** Both reuse its vectors by construction, so identical
  scores are the expected result and the exact zeros are the evidence.
- Late chunking is a real technique that simply does not pay on this corpus:
  passages average ~77 tokens, so there is little surrounding context for a
  chunk to gain.

**Open threads**
- **C3 (semantic breakpoint) NOT BUILT - a time-boxed call, not a measured
  result.** Council review was right to push on this: the earlier justification
  ("it would occupy a size band C1 already holds") extrapolates from C2 and C7,
  which fail by different mechanisms, and states a hypothesis with the confidence
  of a measurement. The honest version: with three days to freeze and Phases 4-8
  unbuilt, a ~35 minute build plus eval was not the best use of the time, and the
  prior - that a ~77-token corpus leaves little room for any segmentation
  strategy to win - is weak evidence, not proof. **Report Phase 3 as 4 measured +
  1 reasoned-out + 1 cost-killed, never as "6 strategies tested".**
- Ranking, not retrieval, is the bottleneck: Hit@1 0.356 against Recall@10 0.878.
  Chunking cannot close that; the Phase 5 reranker is where the headroom is.
- The en/hi gap persists at ~0.16 and no strategy narrowed it.

### 19 Aug 2026 — Phase 5 mid-phase: A2 is false, A6 is not rescued, and the reranker is a Hindi fix

Logged mid-phase rather than at the end, per this file's own rule: an assumption
turned out false and a benchmark number moved materially. **Phase 5 is NOT
complete** — see "what is left" at the bottom. Work is on disk, uncommitted.

**The model choice was made on measurement, not on the default.** `Rules.md` §3.3
names `cross-encoder/ms-marco-MiniLM-L-6-v2`, which is an English-only BERT, and
half this corpus is Hindi. Both candidates were fetched and scored on the same
candidate lists in both languages (`scripts/03b_export_reranker.py`,
`scripts/05d_eval_rerank.py`). Neither needed torch or optimum — both publish
pre-built AVX512-VNNI int8 ONNX, the same situation the embedder was in.

| reranker, 300 dev queries, depth 10 | en Hit@1 | hi Hit@1 |
|---|---|---|
| dense baseline, no rerank | 0.360 | 0.233 |
| `ms-marco-MiniLM-L-6-v2` (the Rules.md default) | **0.447** | **0.120** |
| `mmarco-mMiniLMv2-L12-H384-v1` (chosen) | 0.417 | **0.307** |

The English-only model wins English by a clear margin and takes Hindi from 0.233
to 0.120 — **worse than not reranking at all**, and monotonically worse with depth
(0.120 → 0.073 → 0.043 at depths 10/20/50) as it gets more Devanagari to mis-rank.
The multilingual model is trained on mMARCO, MS MARCO machine-translated into 13
languages including Hindi; MSMARCO-XI is the same construction. Training
distribution and corpus match, and it is the only arm that improves both languages.

**Depth is 5, not the 20 that `Architecture.md` §3.6 specifies.** Quality is flat
from depth 5 to 10 (+0.004 en, +0.006 hi — noise) and *falls* by depth 50. Deeper
reranking hands the cross-encoder more chances to promote something above the
right passage, and the dense ordering it is overriding already carries real signal.
Latency then decides: depth 5 is 59.3 ms P50 / 102.4 ms P100 against depth 10's
113.8 / 191.4. The deploy target is `n2-standard-2` — 2 vCPU is one physical core
plus a hyperthread, against this box's six cores — so depth 10 has no room to
survive the move and depth 5 does.

**A2 is FALSE.** "Cross-encoder rerank of 20 candidates fits in 45 ms" was the
assumption. Measured on an idle BENCH at 2 serving threads, depth 20 costs
**249.1 ms P50** — 5.5x the assumption, on a faster CPU than the deploy target.
Full sweep in `bench/results/2026-08-19-013500-rerank-latency-sweep.json`.

**A6 is NOT rescued, and this is the finding to be careful about.** At the shipping
configuration (batch=1), the reranker's English gain is **+0.037, CI [-0.013,
+0.090] — not significant at n=300**. Hindi is +0.080 and significant. English
Hit@1 is 0.397, so the extractive path still returns the wrong passage about 60% of
the time in English.

The honest sentence is: **the reranker substantially closed the Hindi gap and left
English roughly where it was.** It is not "the reranker fixed ranking". `I2` said
the entire extractive-answer story rests on a large Hit@1 lift; that lift did not
arrive in English, so **D2's reversal condition is live** — the fallback is to
present extractive as a "fast mode" toggle rather than the default. That decision
is not taken yet and should be taken with Phase 6's numbers in hand, not now.

**Band A's headline number is gone, and that is the correct trade.** Phase 2's
3.31 ms P50 becomes roughly 63 ms at depth 5, because rerank costs 30-75x its
neighbours (`embed_query` 2.81 ms, `dense_search` 0.42 ms). Still inside the 200 ms
budget, and a 3.31 ms wrong answer was worth nothing.

**Two issues from this phase:**
- **I24, new.** int8 cross-encoder scores shift with batch composition — 0.279
  logits against a 0.364 median adjacent-rank gap — because dynamic quantization
  derives activation scales per tensor at run time and padding changes the tensor.
  fp32 is bit-exact; equal-length int8 batches are bit-exact. Resolved by scoring
  one pair at a time, which measurement then showed is *also* 32% faster at depth
  10 (padding waste exceeds any batch parallelism at 2 threads). The reproducible
  configuration and the fast one turned out to be the same configuration.
- **I9, closed.** `build_passage_map` reconstructed passages from their longest
  chunk. Once a cross-encoder is *reading* those passages and the eval scores
  against the real text from `passages.parquet`, an approximate store means the
  measured lift is not the lift the service delivers. `Runtime.load_passage_store()`
  loads the real text, ~162 MB against the 8 GB box.

**What is left before Phase 5 can be called done:**
1. Set `RERANK_TOP_K = 5` and rebalance `STAGE_BUDGET_MS`/`STAGE_TIMEOUT_MS`
   for `rerank`. They are still 60/70 ms against a measured 59.3/102.4, so **the
   stage would time out and silently fall back to dense ordering**, discarding the
   phase's entire contribution while the trace reports a tidy fallback. This is the
   one item that must not ship as-is.
2. Run `scripts/06_calibrate_routing.py` (written, never run). `ROUTE_TAU_LOW` and
   `ROUTE_TAU_HIGH` are still 0.0 placeholders, which collapses the generative band
   to nothing.
3. Band A re-bench with and without the reranker; Band B via
   `scripts/04b_bench_bandb.py`, capped at ~40 queries by the Groq token window.
4. `Latency.md` §4 rebalance, `Architecture.md` §3.6 (depth 20 → 5) and §7 Layer 2
   (the 0.35 floor is on a dense scale and cannot apply), and the Phase 5 entry.

---

## Reversals and corrections

_Log here whenever a prior decision is overturned. Include the original reasoning, what changed, and the new decision. These are the highest-value entries in the file._

### R4: rerank depth back to 5, and serving threads back to 2
**Date:** 20 Aug 2026 | **Overturns:** two decisions taken earlier the same day

**Original reasoning.** The first deploy measured `n2-standard-2` at en P50
190.47 ms and hi P50 200.87 ms against a 200 ms budget, with the rerank stage at
94% of the spend. Two levers were pulled from the `Latency.md` 8 list: resize the
instance and raise `ONNX_THREADS_SERVING` from 2 to 4 with it, then cut
`RERANK_TOP_K` from 5 to 3. On `n2-standard-4` that produced en P50 137.70 and hi
P50 150.64, with 9 of 499 requests still over budget. Both decisions were
documented carefully, including the quality cost of the depth cut, and both were
correct readings of the measurement in front of them.

**What changed.** The measurement was of a defect. `rag_core` holds two ONNX
Runtime sessions — the embedder and the cross-encoder — and both were built with
`intra_op_num_threads = ONNX_THREADS_SERVING`. On four vCPUs that is eight worker
threads, and ORT's pool spins rather than sleeping when it finishes a task, so
the embedder was burning cores while the reranker ran. Isolating the cross-encoder
gave ~18 ms per pair, which puts depth 3 at ~55 ms; the service was reporting 118.
That ratio is what found it, and nobody had computed it in six phases.

**New decision.** `ONNX_THREADS_EMBED_SERVING = 1`, `ONNX_THREADS_SERVING = 2`,
`RERANK_TOP_K = 5`, a predictive rerank deadline, and `uvicorn --workers` at
vCPUs/2. Measured through the deployed service, 250 frozen queries x 2 passes per
language: **en P50 95.89 / P100 183.35, hi P50 115.88 / P100 182.20, 0 of 998
requests over 200 ms.** `ISSUES.md` I28 and I29 carry the tables.

**Why this is better, not merely different:**

1. **The depth cut is undone at no latency cost.** Depth 5 beats depth 3 on Hindi
   Hit@1 (0.307 vs 0.290) and clearly on MRR and nDCG, which order citations two
   and three. The project traded that away to buy 35 ms that a session option
   returned for free.
2. **The guarantee is now a mechanism rather than a margin.** The deadline
   refuses to start a pair it cannot afford instead of asking whether it has
   already overrun. The rerank stage's three worst runs in a pass land within
   0.16 ms of each other, which is what a bound looks like.
3. **Spare cores now buy concurrency.** The cross-encoder is identical at 2 and 4
   threads, and one uvicorn process serves one request at a time because every
   stage is synchronous. Four workers took the client-side wall clock at four
   concurrent clients from P100 698 ms to 416 ms.

**Costs accepted:**

- **`n2-standard-8` is roughly 4x the runway burn of `n2-standard-2`.** Taken
  deliberately for a judging window measured in weeks, not as a permanent shape.
  R3's runway arithmetic no longer holds and A12 should be re-read as such.
- **The deadline truncates a rerank on 0.8% of English and 3.2% of Hindi
  requests**, which get depth 4. It is recorded in the trace and has to be quoted
  beside the P100, because a guarantee held by degrading is a different claim
  from a guarantee held by being fast.
- **Two documented decisions now read as reversed within hours.** The blocks in
  `config.py` are kept rather than deleted, with the reversal written underneath
  them. A repo whose whole pitch is honest measurement should show what it
  believed when it believed it.

**The lesson, which is the actual value of this entry.** `Latency.md` 8 is a list
of levers, and a list of levers invites pulling one. Every lever pulled here was
on the list, and none of them was the fix; two of them made things worse or were
wasted. The missing step was upstream of the list: *explain the number first*.
Time the expensive component in isolation, compare it against the same component
inside the service, and only then decide what to trade. That is now lever 0.

**Reversal condition:** a box where the cross-encoder scales past two threads —
more physical cores, or a materially different model — would put
`ONNX_THREADS_SERVING` back in play. Re-run `scripts/07b_rerank_sweep.py` on the
new box before assuming it.

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
| A2 | Cross-encoder rerank of 20 candidates fits in 45ms on the same CPU | Phase 5 | ✗ **FALSE.** Depth 20 measures **249.1 ms P50** on an idle BENCH at 2 threads - 5.5x, on a faster CPU than the deploy target. Depth is 5, not 20. See the 19 Aug mid-phase entry. |
| A3 | Sarvam's realtime endpoint emits partials fast enough and stably enough for speculative prefetch to hit often | Phase 4 | ✓ **TRUE, measured 20 Aug** (`scripts/08_probe_realtime_stt.py`). Over 5.8 s of synthesized speech: **19 partial events**, first at 991 ms, each extending the last word by word, final 385 ms after the audio ended. Through our own relay and Caddy (`08b_probe_live_relay.py`): 7 partials and a correct final. **The partials are NOT used on screen**, and that is a second finding rather than a change of mind: under `language_code=auto` the partials arrive ROMANISED ("Qatar ki rajdhani kya hai") and only the final is converted to Devanagari. Pinning `hi-IN` fixes them and corrupts the English final to `व्हाट इज द कैपिटल ऑफ कतार`. No `mode` or `stream_type` separates the two (`scripts/08c_probe_hindi_partials.py`). So A3 is TRUE as written and the prefetch it was asked for has a NEW blocker: the partial and the final are not in the same script, so the edit-distance match Latency.md 5 specifies would fail on every Hindi utterance. **The prefetch is still NOT built.** |
| A4 | The frozen slice fits in the container RAM budget with all eight indexes loaded | Phase 3 | ✗ **FALSE as stated.** One index is 655 MB; eight will not co-reside in 8 GB alongside the models. Load-on-switch instead. **Amended 18 Aug:** several Phase 3 strategies exceed C1's 655 MB, so the F13 toggle's switch pause differs per strategy — the UI should show the actual figure, not a generic spinner. |
| A5 | C7 (doc2query / query-aligned) outperforms the other seven strategies on this corpus | Phase 3 | ☐ |
| A6 | Extractive answers are good enough to be the default path rather than a fallback | Phase 5 | ◐ **still open, and the reranker did not settle it.** en Hit@1 0.360 -> 0.397 (+0.037, CI [-0.013,+0.090], NOT significant); hi 0.233 -> 0.313 (+0.080, significant). English top-1 is still wrong ~60% of the time, so D2's reversal condition is live. Decide with Phase 6 numbers. |
| A7 | An India-region always-on container is available on the free or cheap tier of the chosen host | Phase 0 | ✓ **TRUE** — GCP Compute Engine `n2-standard-2`, `asia-south1` (Mumbai), on $300 trial credits. See reversal R3. |
| A11 | ONNX int8 inference on ARM (Ampere A1) hits the same latency as x86 | Phase 2 | ~~open~~ **MOOT.** Retired by R3: GCP is x86, so the question no longer arises. |
| A12 | $300 of GCP credit outlasts the judging window | Phase 7 | ◐ **re-price it, 20 Aug.** The ~$70/mo that projected to mid-December was `n2-standard-2`. The box is now `n2-standard-8` (reversal R4), roughly 4x that, so the runway is roughly a quarter as long. It still covers a judging window measured in weeks, which is what it has to do. Budget alert is now mandatory rather than advisable, and resizing down after judging is a task, not an option. |
| A13 | fp16 GPU passage vectors paired with int8 CPU query vectors retrieve equivalently to an all-int8 index | Phase 3, J1 | ☐ **This is the gate the whole split rests on. Verify first, not last.** |
| A14 | A local 3B to 7B model produces usable propositions from machine-translated Hindi passages | Phase 3, J6 | ☐ Genuine risk of a lossy pass over already-lossy text. Track the parse-reject rate. |
| A15 | The winning strategy's index fits the 8 GB `n2-standard-2` alongside embedder, reranker, BM25 and passage store | Phase 3, J16 | ☐ Follows from `ISSUES.md` I4. One C1 index already costs ~1.16 GB resident; a strategy at 3x the chunk count may win on recall and still not be servable. |
| A16 | The 5070 Ti's CUDA stack comes up on this project's toolchain | Phase 3, J5 | ☐ Blackwell is sm_120, needs CUDA 12.8+. Timeboxed to 45 min with a role-swap fallback (`Devices.md` §4.3). |
| A8 | Sarvam free credits cover the full build plus demo recording | Phase 4 | ◐ key verified live 14 Aug; remaining credit balance not yet checked |
| A10 | Groq free-tier limits allow a Band B benchmark of useful size | Phase 5 | ✗ **FALSE as stated** — 12,000 tokens/window caps it. Band B must be a ~50-query sample, stated in the methodology. |
| A9 | The 200ms budget's 25ms reserve is enough to absorb tail jitter | Phase 5 | ✓ **TRUE on the deployed box, but the reserve is not what does the work.** 0 of 998 requests over 200 ms, en P99→P100 152.48→183.35. The tail is held by the predictive rerank deadline, which stops before a pair that will not fit, not by the reserve absorbing it. Remove the deadline and 8 of 998 go over at depth 5. |
| A17 | Lexical overlap between an answer and its cited passages detects ungrounded answers | Phase 6 | ✗ **FALSE as hoped.** It detects answers about a different subject (0.062) and does not detect false claims: a reassembly of the passage's own words scores 0.833 against a true paraphrase's 0.639. Useful for provenance, not for truth. |
| A18 | A score-gap check can catch ambiguous queries | Phase 6 | ✗ **FALSE.** Catching 5 of 9 ambiguous costs 4 of 14 answerable; the distributions interleave. Rejected, `ISSUES.md` I27. |

---

## Prompt for a cold session

Paste this when starting a fresh AI coding session on this project:

> You are working on team OK4T's HH Goa 2026 Task 2 submission: a voice-enabled RAG system with a 200ms latency target on the core pipeline.
>
> Read these files first, in order: `Memory.md` (context and decisions), `Rules.md` (hard constraints), `Phases.md` (find the current phase), `Architecture.md` (the design), `Latency.md` (the budget).
>
> Key context: the fast path makes zero network calls. Extractive answering when reranker confidence is high, Groq LLM fallback when moderate, abstention when low. No LangChain. No hosted vector DB. No hosted embeddings. Everything in-process on ONNX int8.
>
> The website is `frontends/`: static HTML, two stylesheets and ES modules, no build step and no Node. It is served by `python -m http.server` on port 3000, which is fixed because `stt_gateway` allows CORS from that origin only. Its own `README.md` is the frontend handoff. The Next.js app that used to be in `apps/web` was removed on 20 Aug.
>
> **Current state, 20 Aug 2026.** Phases 0-5, 7 and 8 are complete, Phase 6 is
> partial, and **Phase 9 (videos, posting, submission) is the only phase left.**
> It is deployed at https://shrutirag.duckdns.org on an `n2-standard-8` in
> Mumbai, and Band A is **95.89 ms P50 English / 115.88 ms Hindi measured through
> the deployed service, with 0 of 998 requests over the 200 ms budget.** Do not
> quote the 59.99 ms figure that appears in older files: it is a development
> machine and `DONT-FORGET.md` 6 explains why that matters.
>
> Read `DONT-FORGET.md` first, then `HANDOFF.md` 1A for how to reach and deploy
> to the box. Before changing anything for latency reasons, read `ISSUES.md` I28
> and step 0 of `Latency.md` 8 — the two most recent optimizations on this
> project were both aimed at a number nobody had explained and both were wrong.
>
> Tell me which phase we are on and what its exit criterion is before writing any code.
