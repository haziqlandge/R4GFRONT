# Latency.md

The 200ms requirement, treated as the primary engineering problem it is.

This file is the one a judge is most likely to read closely. It should be the most rigorous document in the repo.

---

## 1. The measurement contract

**Stated plainly and up front, because the alternative is looking like we are hiding something.**

The brief asks that "chunking + vector DB retrieval + everything through to final output" complete in under 200ms.

We measure and publish three distinct bands:

| Band | Boundary | Target |
|---|---|---|
| **Band A: Core RAG** | Transcript received by `rag_core` → response serialized. Includes guardrails, embedding, dense search, lexical search, fusion, reranking, routing, extractive answering, groundedness check. Excludes STT and excludes any LLM network call. | **< 200ms, this is the target the brief describes** |
| **Band B: Core RAG + generation** | Band A but routed through the Groq LLM fallback instead of the extractive path. | Reported honestly. Will exceed 200ms. |
| **Band C: Full wall clock** | User stops speaking → answer painted in browser. Includes STT finalization, both network hops, and render. | Reported honestly. Will exceed 200ms. |

**Why Band A is the right band for the stated requirement.** The brief enumerates the stages it means: chunking, vector DB retrieval, and the path to final output. Speech-to-text is listed as a separate, preceding pipeline stage. Band A covers exactly the enumerated stages plus our own guardrail and answering layers.

**Why we publish B and C anyway.** Because a judge will ask, and the team that already answered the question in writing looks rigorous while the team that has to improvise looks evasive. Publishing C is a strength, not an admission.

---

## 2. Why an LLM call cannot fit in 200ms

This is the constraint that shapes the entire architecture, so the reasoning is written down rather than assumed.

Independent benchmarking of hosted inference providers puts time-to-first-token for the fastest providers in the range of a few hundred milliseconds, with Groq among the strongest on both TTFT and throughput. Groq's own published figures for early benchmarks put TTFT around 0.22 to 0.3 seconds. Even at the optimistic end, first token alone consumes the entire 200ms budget before a single retrieval operation has run.

Add to that:
- Our server-to-Groq network round trip from Mumbai
- The tokens after the first, since an answer is not a first token
- Our own retrieval work, which must happen *before* the LLM call

The arithmetic does not close. A pipeline containing a hosted LLM call cannot reliably finish in 200ms, and no amount of prompt optimization changes this because the floor is network plus inference, not tokens.

**Therefore the fast path must not contain an LLM call.** This is not a compromise; it is the only design that satisfies the requirement, and it happens to be well suited to this specific corpus.

---

## 3. The dual-path design

```
                      rerank_top1_score = s
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
    s >= 0.72            0.35 <= s < 0.72        s < 0.35
        │                     │                     │
   EXTRACTIVE            GENERATIVE             ABSTAIN
   span from top          Groq LLM            typed refusal
   passage, cited        streamed, cited        with reason
        │                     │                     │
     ~4 ms                ~300 ms                ~0 ms
   Band A: ✓ PASS       Band B: reported      Band A: ✓ PASS
```

### 3.1 Why extractive answering is legitimate here, not a cop-out

MS MARCO is a machine reading comprehension dataset. Its passages were selected because they contain the answer, and the dataset ships `is_selected` flags marking answer-bearing passages. The answer to a MS MARCO query is, by construction, a span inside a retrieved passage.

Extracting that span is the *correct* operation for this corpus, not a degraded substitute for generation. It also has a property generation cannot match: **zero hallucination risk**, because every output token came verbatim from a cited source. Requirement 6 asks for hallucination checks; the extractive path makes hallucination structurally impossible.

The generative path exists for the harder cases: queries where the answer must be synthesized across passages, or where the top passage is relevant but does not contain a clean span.

### 3.2 Why the same signal drives routing and abstention

The reranker's top-1 score is used for both the fast-path/slow-path decision and the abstain decision. This is the structural elegance of the design: one calibrated confidence measure satisfies requirement 3 (latency) and requirement 6 (knowing when not to answer) simultaneously, rather than bolting on two independent mechanisms.

---

## 4. Band A budget, stage by stage

Total budget 200ms. Allocated with headroom, because P100 is what fails, not P50.

| Stage | Allocated | Expected | Hard timeout | On timeout |
|---|---|---|---|---|
| `input_guard` | 12 ms | 3 to 8 | 15 ms | Pass through, log |
| `embed_query` | 25 ms | 5 to 15 | 30 ms | Fail request |
| `dense_search` | 15 ms | 2 to 8 | 20 ms | Fall back to lexical only |
| `lexical_search` | 10 ms | 1 to 5 | 12 ms | Fall back to dense only |
| `fuse` | 3 ms | < 1 | 5 ms | Use dense order |
| `rerank` | 60 ms | 20 to 45 | 70 ms | **Skip, use RRF order** |
| `route` | 2 ms | < 1 | 3 ms | Default to abstain |
| `answer_extractive` | 15 ms | 2 to 6 | 20 ms | Return top passage whole |
| `output_guard` | 25 ms | 5 to 15 | 30 ms | Downgrade to extractive |
| `serialize` | 8 ms | 1 to 3 | 10 ms | Fail request |
| **Allocated total** | **175 ms** | **40 to 100 ms** | | |
| **Reserve** | 25 ms | | | |

The 25ms reserve absorbs GC pauses, scheduler jitter and network buffer effects that show up at P99 and P100 but never at P50.

### 4.1 The remaining-budget counter

The pipeline carries a countdown. Before each stage runs, the harness checks whether the stage's allocation fits in the remaining budget. If it does not, the stage is **skipped** and its declared fallback runs instead.

The practical effect: if dense search had a slow run and consumed 40ms, the reranker is skipped automatically and RRF order is used. Quality degrades; latency is protected. Every skip is recorded in the returned trace and rendered as a hatched bar in the UI waterfall.

This is what makes the 200ms figure a guarantee rather than an average.

---

## 5. The prefetch optimization

Sarvam's realtime endpoint emits partial transcripts as the user speaks. We exploit this.

When a partial transcript has been stable for 250ms and contains at least four tokens, the harness speculatively executes `input_guard` through `rerank` against it, in the background, while the user is still talking.

If the final transcript matches the speculative one within a normalized edit distance of 0.15, the retrieval results are already in hand and Band A collapses to just `route` + `answer` + `output_guard` + `serialize`, roughly 15 to 30ms.

If it does not match, the speculative work is discarded and the pipeline runs normally. Cost of a miss: some wasted CPU during a period when the CPU was idle anyway.

**Expected hit rate: high**, because most spoken questions stabilize well before the speaker finishes. This is free latency and it is the single best optimization available in the whole design.

---

## 6. Measurement methodology

Published numbers must be defensible. The method:

- **Query set:** `bench/queries_250.jsonl`, 250 queries, frozen before any optimization begins, drawn from the held-out portion of the frozen corpus slice. Language distribution matches the slice.
- **Warmup:** 30 discarded queries before measurement starts. ONNX sessions, HNSW page cache and the Python JIT-adjacent paths are all cold on first use, and including cold runs inflates P100 in a way that is not representative.
- **Runs:** 5 independent passes over the full 250. All 1250 samples pooled.
- **Clock:** `time.perf_counter_ns()`, monotonic, captured at stage boundaries inside the process. No wall clock, no `datetime`.
- **Percentiles:** `numpy.percentile` with `method="nearest"`. P100 is the true maximum, not the 99.9th.
- **Environment:** measured against the **deployed** service, not localhost. Localhost numbers are not real numbers.
- **Concurrency:** measured at 1 concurrent request and again at 8, since P100 under concurrency is what a judge hitting the live URL alongside others will actually experience.
- **Reporting:** every run writes a dated immutable JSON to `bench/results/`. Results are never overwritten.

Reported statistics: P50, P70 (explicitly required by the brief), P90, P99, P100, mean, and standard deviation. The brief asks for P50/P70/P100; publishing P90 and P99 alongside costs nothing and demonstrates that the tail was actually examined.

---

## 7. Results

**To be filled in at Phase 5 and finalized at Phase 7. Estimates below are placeholders and must be replaced with measured values before submission. Do not ship this file with estimates in it.**

### Band A: Core RAG, extractive path

| Percentile | Target | Measured |
|---|---|---|
| P50 | < 90 ms | _pending_ |
| P70 | < 120 ms | _pending_ |
| P90 | < 160 ms | _pending_ |
| P99 | < 190 ms | _pending_ |
| P100 | < 200 ms | _pending_ |

### Band B: Core RAG + Groq generation

| Percentile | Measured |
|---|---|
| P50 | _pending_ |
| P70 | _pending_ |
| P100 | _pending_ |

### Band C: Full wall clock, speech end to painted answer

| Percentile | Measured |
|---|---|
| P50 | _pending_ |
| P70 | _pending_ |
| P100 | _pending_ |

### Path distribution over the 250-query benchmark

| Path | Share | Note |
|---|---|---|
| Extractive | _pending_ | Fraction meeting the Band A target |
| Generative | _pending_ | |
| Abstained | _pending_ | Expected to be low on in-distribution queries, high on the adversarial set |

### Per-stage medians

_Table generated by `scripts/04_bench_latency.py --breakdown`, pasted here at Phase 7._

---

## 8. Optimization levers, in order of payoff

If a benchmark comes in over budget, pull these in this order. Do not optimize randomly.

1. **Lower `ef_search` on the HNSW index.** Largest single dial. Trades recall for speed almost linearly. Start at 64, try 48 and 32.
2. **Rerank fewer candidates.** 20 to 12 saves roughly 15ms. Measure the recall cost before committing.
3. **Verify int8 quantization actually applied.** A silently-fp32 ONNX model is a common and expensive mistake. Check the model file size.
4. **Pin `onnxruntime` intra-op threads.** The default oversubscribes on small models. Try 1, 2 and 4 and measure. Counterintuitively, fewer threads is often faster here.
5. **Check the deploy region.** If the container is not in India, nothing else matters. This is worth checking first if numbers are wildly off.
6. **Shorten the maximum passage length fed to the reranker.** Cross-encoder cost scales with sequence length. Truncating to 256 tokens is usually free in accuracy.
7. **Drop to a smaller embedder.** Last resort, since it costs retrieval quality across the board.

Do not reach for caching as an optimization for the published numbers. A semantic cache makes repeat queries fast and tells you nothing about the pipeline. If a cache is added, benchmarks must be run cache-cold and that must be stated.

---

## 9. The honest paragraph

This paragraph, or something close to it, goes in the README and gets said out loud in Video 2. It is written here so it does not have to be improvised.

> Our core RAG pipeline (guardrails, embedding, hybrid retrieval, fusion, cross-encoder reranking, answer construction and groundedness verification) completes at a P50 of **[X]ms** and a P100 of **[Y]ms**, inside the 200ms target. It achieves this by making zero network calls on the fast path: the embedder, both indexes and the reranker all run in-process on quantized ONNX models.
>
> We do not claim 200ms end to end including speech-to-text and hosted LLM generation, because that is not physically achievable. The fastest hosted inference providers have a time-to-first-token measured in hundreds of milliseconds, which exhausts the budget before retrieval begins. Rather than hide this, we designed around it: when retrieval confidence is high, we answer extractively from the cited passage with no LLM call at all, which is both faster and structurally incapable of hallucinating. When confidence is moderate, we route to Groq and report that path separately at **[Z]ms**. When confidence is low, we abstain.
>
> All three bands are published, with the measurement boundary stated for each.
