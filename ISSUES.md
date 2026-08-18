# ISSUES.md

Open problems, measured rather than assumed, as of **18 August 2026** (end of Phase 2).

Severity is about the submission, not about engineering neatness:

- **P0** — would visibly break the demo or invalidate a published number
- **P1** — materially weakens a scored requirement
- **P2** — real, bounded, has a known home in a later phase
- **RESOLVED** — investigated and closed, kept here because the reasoning matters

---

## Summary

| # | Issue | Severity | Lands in |
|---|---|---|---|
| I1 | Hindi P100 spike, 119 ms from one pathological query | **RESOLVED (experiment)** | Phase 6 |
| I2 | Hit@1 is 0.362 en / 0.224 hi — the naive answer is usually wrong | **P1** | Phase 5 |
| I3 | Dense cosine cannot separate gibberish from a correct answer | **P1** | Phase 6 |
| I4 | Eight indexes cannot co-reside in RAM (A4 false) | **P1** | Phase 3 |
| I5 | Hindi retrieval trails English by 0.19 Recall@10 | **P2** | Phase 3/5 |
| I6 | Thread counts in config are wrong for both workloads | **P2** | now |
| I7 | Groq free tier caps Band B at ~50 queries | **P2** | Phase 5 |
| I8 | All benchmarks are local x86, not the deployed box | **P2** | Phase 7 |
| I9 | `build_passage_map` reconstructs passages approximately | **P2** | Phase 5 |
| I10 | Two degenerate `-` passages act as attractors | **P2** | Phase 3 |
| I11 | Schedule: 3 days behind the original plan | **P1** | ongoing |
| I12 | Non-ASCII through `curl` on Windows silently mangles | **RESOLVED** | — |

---

## I1 — Hindi P100 spike: 119 ms from one query

**Status: cause identified, fix validated by experiment, not yet implemented.**

### What was observed

Hindi Band A P100 was 119.13 ms against a P99 of 5.89 ms. A 20× gap between P99 and P100 is normally scheduler noise. This was not.

### What it actually is

`query_id=156297` — **7,168 characters** of one Devanagari phrase repeating (`आर्कजी का फ़ाइल प्रारूप` over and over). A machine-translation repetition loop in the source dataset, the same pathology found in passages during Phase 1, now appearing in a query. It reproduces on every single run.

### Where the time goes — measured, not assumed

| | pathological | typical Hindi | ratio |
|---|---|---|---|
| chars | 7,168 | 26 | 276× |
| tokens (after the 512 cap) | 512 | 13 | 39.4× |
| char length check | 0.0009 ms | 0.0002 ms | — |
| tokenize | 1.661 ms | 0.050 ms | 33× |
| **ONNX forward** | **100.891 ms** | 2.670 ms | **37.8×** |
| dense search | 0.202 ms | 0.295 ms | — |
| **total** | **102.971 ms** | 3.081 ms | |

**ONNX inference is 98.0% of the cost. Tokenization is 1.6%.**

Two things worth noting. The 512-token cap *is* working — the input is truncated. The cost is simply that a 512-token forward pass is 37.8× a 13-token one. And that ratio tracks the token ratio (39.4×) almost exactly, so on this model at this length the cost is **linear in sequence length**, not quadratic — the feed-forward layers dominate, attention has not yet taken over.

### Why a guard is safe here

The frozen benchmark's Hindi queries:

| | p50 | p95 | p99 | max |
|---|---|---|---|---|
| hi chars | 34 | 60 | 85 | 7,168 |
| en chars | 31 | 54 | 69 | 77 |

The second-longest Hindi query is **90 characters**. The pathological one is **80× larger than the next largest**. There is no ambiguity to resolve — any threshold between ~100 and ~7,000 separates them cleanly.

### Candidate guards, measured

| char limit | guard cost | rejects 156297? | other hi rejected | en rejected | worst accepted |
|---|---|---|---|---|---|
| 512 | 0.00007 ms | **yes** | 0 | 0 | 5.78 ms |
| 1024 | 0.00007 ms | **yes** | 0 | 0 | 5.16 ms |
| 2048 | 0.00007 ms | **yes** | 0 | 0 | 5.19 ms |
| 4096 | 0.00008 ms | **yes** | 0 | 0 | 5.14 ms |

Every limit tested rejects exactly one query — the pathological one — and zero legitimate queries.

### Character limit vs token limit

The question was whether a token-based limit is unusable because tokenization is itself the expensive part. **It is not** — but the character check is still the right pre-filter:

- char guard: **0.00007 ms** per query
- token guard: **0.04228 ms** per query (**568× more expensive**)
- tokenizing the 7,168-char query alone: 1.422 ms, against 100.9 ms of ONNX

So a token-only limit would work — wasting 1.4 ms on the pathological input is irrelevant next to 100 ms. But the character check costs effectively nothing and rejects before any allocation happens.

**However, a character limit alone is not principled**, because characters-per-token is script-dependent:

- English: **4.56** chars/token
- Hindi: **3.19** chars/token

At ~2 chars/token (a dense script, or an adversarial input chosen to maximise tokens per character), 1,024 characters could still reach the full 512-token window. A character limit bounds *characters*; only a token limit bounds the thing that actually costs money.

**Therefore the correct design is both, in order:** a cheap character pre-filter that catches gross outliers for ~0 cost, then a token-count bound after tokenization that enforces the real limit. Defence in depth, and each layer is priced honestly.

### Result with a 1,024-char guard applied

Full Hindi benchmark, 3 passes over the frozen 250, guard applied before embedding:

| | with guard | before |
|---|---|---|
| P50 (accepted) | 3.29 ms | 3.83 ms |
| P99 (accepted) | 4.97 ms | 5.89 ms |
| **P100 (accepted)** | **5.17 ms** | **119.13 ms** |
| rejected | 1 query (`156297`), 0.0018 ms to reject | — |

### CORRECTION — a character-only guard leaks, and 1,024 was the wrong number

The result above is real but it validated the guard against **n=1**. A follow-up
experiment (prompted by council peer review) tested whether a *shorter* adversarial
input could slip under the threshold and still blow the budget. It can.

**Latency vs input length, same pathological text truncated:**

| chars | tokens | latency |
|---|---|---|
| 32 | 12 | 3.40 ms |
| 128 | 43 | 7.12 ms |
| 256 | 86 | 12.71 ms |
| 512 | 172 | **24.78 ms** |
| 1024 | 343 | **57.57 ms** |
| 1536 | 512 | 109.38 ms |
| 7168 | 512 | 111.99 ms |

Cost is **linear in tokens** (~0.21 ms/token) until the 512-token cap, then flat.

**So the character limits I proposed all leak:**

| char limit | worst admissible input | worst latency | verdict |
|---|---|---|---|
| 512 | 172 tokens | 24.99 ms | **leaks** |
| 1024 | 343 tokens | 56.02 ms | **leaks** |
| 2048 | 512 tokens | 107.77 ms | **leaks badly** |

A 1,024-char guard would have left a 56 ms worst case — still 11× the P99, still
the P100. It only produced 5.17 ms because no *other* query in the frozen set
happens to be long. That is exactly the n=1 fragility the review flagged.

**A character limit bounds characters; cost is driven by tokens.** At ~2.6
chars/token for repeated Devanagari, 1,024 characters still buys ~390 tokens.

### The design that actually works: bound tokens

| token cap | worst-case latency | legit hi rejected | legit en rejected |
|---|---|---|---|
| 32 | 5.79 ms | 1 | 0 |
| **64** | **10.02 ms** | **1** | **0** |
| 128 | 18.15 ms | 1 | 0 |
| 256 | 38.31 ms | 1 | 0 |

Legitimate query token counts: Hindi p99 **24**, second-highest **25**, max 512
(the pathological one). English max **16**.

**Chosen: a 64-token bound, with a 512-character pre-filter in front of it.**

- 64 tokens bounds worst-case embedding at **10.02 ms**, comfortably inside budget.
- It clears the largest legitimate query (25 tokens) by **2.6×**, so it is not
  fitted to the outlier.
- **It is justified externally**, which is what makes it defensible: 64 tokens is
  roughly 15 seconds of continuous speech. This is a voice system — the input path
  is a microphone. A question nobody could say out loud in 15 seconds is not a
  question this system is for. That reasoning holds whether or not `156297` exists.
- The 512-char pre-filter is a free early-out (0.00007 ms) that rejects gross
  inputs before any allocation. It is a performance optimisation, **not** the
  safety bound.

### No hidden milder corruption

Scanned all 500 query strings (250 × en/hi) for single-word repetition ratio
above 0.25. **Exactly one hit: `query_id=156297` at 0.33.** The corruption is
isolated, not the tip of a systemic data-quality problem.

### On reporting: never call it bare "P100"

`P100` means the maximum over all traffic. Reporting a filtered subset under that
label is a category error regardless of intent. Published as:

```
P100, accepted inputs      10.02 ms   (249/250)
P100, all submitted       119.13 ms   (includes 1 rejected input)
Rejected                    1 query   query_id=156297, 7,168 chars,
                                      MT repetition loop, refused in 0.0018 ms
```

Both numbers, same table, one line of cause. A judge sees the input contract and
its cost, not a laundered figure.

### This is not benchmark manipulation

The pathological query stays in `bench/queries_250.jsonl`, unedited. `Rules.md` §5 forbids editing a benchmark to improve a number. The query is *rejected by a stated policy*, its rejection cost is measured and published (0.0018 ms), and the published figures will report accepted and rejected traffic separately. A judge can see exactly what was refused and why.

The honest framing: **the input guard is a latency mechanism as well as a safety one.** That is the same structural pattern as the reranker score serving both routing (requirement 3) and abstention (requirement 6) — one control, two requirements.

### Caveat worth stating

This makes P100 look excellent, but it does so by refusing to answer one query. That is legitimate *only* because the query is genuinely degenerate — 7,168 characters of one repeated phrase is not a question anyone asked. If a future slice contains long but legitimate queries, the guard would be suppressing real work and the number would be dishonest. The threshold must stay justified by the measured distribution, and that distribution must be re-checked if the slice changes.

---

## I2 — Hit@1 is 0.362 en / 0.224 hi

**Severity: P1.** The Phase 2 extractive path returns the top passage, so the naive answer is wrong roughly two times in three (English) and three times in four (Hindi).

Expected, and it is exactly what Phase 5's cross-encoder reranker exists to fix — rerank top-20 to reorder top-1. But it converts a previously vague dependency into a measured one: **the entire extractive-answer story rests on the reranker delivering a large Hit@1 lift.** Recall@10 of 0.870 (en) says the right passage is nearly always *in* the candidate set, which is the precondition for reranking to work. If Phase 5's reranker does not move Hit@1 substantially, assumption **A6** fails and the fallback is to present extractive as a "fast mode" toggle rather than the default (the reversal condition already recorded in D2).

---

## I3 — Dense cosine cannot separate gibberish from a correct answer

**Severity: P1.** Measured on the live endpoint:

| query | top-1 score |
|---|---|
| correct English match | 0.9193 |
| correct Hindi match | 0.9050 |
| `zxqwv fhqwhgads plorbnak` (pure gibberish) | **0.8624** |

A ~0.05 margin between "right answer" and "meaningless input" is far too narrow to place an abstention floor on. `Architecture.md` §7 Layer 2 specifies a confidence floor at 0.35 — that threshold was written for a *reranker* score and would be meaningless against these dense scores, where everything lives above 0.85.

This is direct evidence for the design already chosen in `Architecture.md` §3.6: the confidence signal must come from the **cross-encoder**, which reads query and passage together, not from the bi-encoder, which compares two independently-formed embeddings. Phase 6 must calibrate the floor on rerank score against a labelled dev slice. A dense-score floor would either abstain on good answers or accept nonsense.

---

## I4 — Eight indexes will not co-reside in RAM

**Severity: P1. Assumption A4 is false as originally stated.**

One C1 index is **655 MB** (`index.bin`) plus 50 MB of chunk metadata. Eight strategies is ~5.6 GB before models, Python, or the BM25 index. The GCP box is `n2-standard-2` with **8 GB**.

The F13 live strategy toggle must therefore **load on switch** rather than keeping all strategies warm. That is acceptable — a deliberate toggle is a demo action, not the hot path, and `Rules.md` §2.1's "no disk reads at request time" is about the *request* path. But it changes F13's design and its demo choreography: there will be a visible pause when switching strategies, which the UI should show honestly rather than hide.

---

## I5 — Hindi retrieval trails English by 0.19 Recall@10

**Severity: P2.** en 0.870 / hi 0.682 Recall@10; MRR 0.525 / 0.367.

Expected: both queries and passages are machine-translated, and `multilingual-e5-small` is weaker on Devanagari than on Latin script. Worth watching rather than fixing directly — BM25 with Indic-aware tokenisation (Phase 3) and the reranker (Phase 5) should both help. The risk is that they help English *more*, widening the gap. If that happens the multilingual framing in `Project.md` §3 weakens and should be restated honestly rather than glossed.

---

## I6 — Thread counts: measured, and the synthetic benchmark was misleading

**Severity: P2. Resolved — no config change. Kept because the reasoning matters more than the outcome.**

### Build threads: keep 8

A first sweep used a **synthetic** workload (`short_en * 40` — uniformly short English strings) and showed build throughput rising with thread count:

| threads | chunks/sec (synthetic) |
|---|---|
| 6 | 872.6 |
| 8 | 999.9 |
| 12 | 1115.5 |

On that basis I recommended moving `ONNX_THREADS_BUILD` from 8 to 12. **That recommendation was wrong, and the percentage I attached to it was also wrong.**

**The arithmetic error:** 1115.5 / 999.9 = **+11.6%** for 8 → 12. I reported ~28%, which is 1115.5 / 872.6 — the 6 → 12 comparison. Two different baselines conflated.

**The substantive error:** the workload wasn't representative. Re-run against **actual C1 chunk texts** sampled from the completed 379,242-chunk build, through the exact production path (global length sort → batch 32 → int8 embedder → scatter back), two interleaved rounds per configuration:

| threads | chunks/sec (real C1) | runs |
|---|---|---|
| **8** | **213.0** | 213.6, 212.4 |
| 12 | 208.7 | 204.9, 212.4 |

**8 → 12 is −2.0%.** The advantage did not shrink; it reversed.

Sample validity: sample token distribution p50 72 / mean 70.1 against population p50 73 / mean 70.6; language mix 45/55 en-hi in both. The projection also validates against reality — 29.7 min predicted at 8 threads versus **30.8 min actually observed** on the full build.

**Why the synthetic number lied.** Real C1 chunks are p50 **72 tokens**; the synthetic strings were roughly 5–10× shorter. With long sequences each batch already saturates all 6 physical cores at 8 threads, so 4 extra logical threads add contention and nothing else. With trivially short strings, per-batch compute is small relative to dispatch overhead, so more threads appeared to help. The 11.6% was an artifact of the toy workload.

**Decision: `ONNX_THREADS_BUILD` stays at 8.** Projected saving from switching to 12 is **−4.9 min across Phase 3's eight index builds** — i.e. a loss.

Honest caveat: −2.0% is close to noise (round 2 tied at 212.4 for both). The defensible claim is *"12 threads is at best equal, possibly slightly worse"* — which still leaves no case for changing.

### Serving threads: keep 2 (deployment decision, not a local one)

Separate question, separate answer. Local sweep on the i5-12400F (6 physical / 12 logical), single short query, latency-bound:

| threads | en P50 | en P99 | hi P50 |
|---|---|---|---|
| 1 | 3.40 ms | 4.94 ms | 3.83 ms |
| 2 | 2.49 ms | 4.06 ms | 2.93 ms |
| 4 | 2.01 ms | 2.87 ms | 2.38 ms |
| **6** | **1.97 ms** | **2.69 ms** | 2.37 ms |
| 8 | 2.14 ms | 2.98 ms | 2.47 ms |
| 12 | 2.49 ms | **15.58 ms** | 2.58 ms |

6 threads is fastest **locally**, but `ONNX_THREADS_SERVING` **stays at 2** because the deploy target is a 2-vCPU `n2-standard-2`. A local optimum tuned to 6 physical cores does not transfer to a 2-vCPU box, and the published numbers must come from the deployed service anyway (`Latency.md` §6).

Worth noting for its own sake: 12 threads produces a **P99 of 15.58 ms** — a 6× tail blowup from hyperthread contention, exactly the failure mode that destroys P100.

### The lesson worth keeping

**Benchmark the real workload, not a convenient stand-in.** A synthetic proxy produced a number that was directionally wrong, and the recommendation built on it would have made Phase 3 builds slower while claiming a 28% speedup. Two independent errors — wrong baseline in the arithmetic, wrong workload in the measurement — pointed the same way, which is how a plausible-looking wrong answer survives review.

---

## I7 — Groq free tier caps Band B measurement

**Severity: P2.** 12,000 tokens per window. A full 250-query Band B benchmark needs ~250k tokens and will throttle hard. Band B must be a ~50-query sample with the sample size stated in the methodology. Silver lining: the Phase 5 circuit-breaker demo gets a *real* 429 rather than an injected one.

---

## I8 — All benchmarks are local, not deployed

**Severity: P2, but it invalidates the headline number if forgotten.** `Latency.md` §6 requires published figures to come from the deployed service. Everything so far is local x86 on a 12400F; the GCP box is a 2-vCPU `n2-standard-2`. Expect the numbers to move — the 3.31 ms P50 was measured with `ONNX_THREADS_SERVING=2`, which is the right setting for that box, but the cores are slower. Re-bench in Phase 7 and publish only those.

---

## I9 — `build_passage_map` reconstructs passages approximately

**Severity: P2.** It takes the longest chunk as the passage representative. Correct for the ~78% of passages that yield a single chunk, approximate for the rest — a multi-chunk passage returns only its longest fragment as the "answer", so the user may see a partial passage.

Phase 5 needs a real passage store keyed by `passage_id` when span selection requires exact offsets. Cheap to fix (the text is already in `passages.parquet`); left alone in Phase 2 only because the thin slice deliberately did not add a second data structure.

---

## I10 — Two degenerate `-` passages act as attractors

**Severity: P2.** Exactly 2 passages of 295,890 (0.001%) have text `-`, both Hindi. They surface as the top hits for meaningless queries, because a degenerate embedding sits near the centroid of the space and is therefore mildly similar to everything.

Filter empty and near-empty passages at index build time in Phase 3. The frozen slice itself stays unchanged — `Rules.md` §5 — the filter belongs in the indexer, and the count of filtered passages goes into `meta.json`.

---

## I11 — Three days behind the original plan

**Severity: P1.** `Phases.md` scheduled Phase 2 for 15 August; it completed on 18 August. Code freeze is 21 August 11:59 PM (`Rules.md` §7), leaving Phases 3–8 for the remaining window.

The user has directed that work proceed at full quality rather than compressing, so this is recorded rather than acted on. The cut order in `Phases.md` §Slack remains available and unapplied: nice-to-haves (F17–F20) first, then chunkers C6 and C8, then the F13 toggle. Never cut: the guardrail eval set, the latency benchmark, the deployment, the videos, the posting.

---

## I12 — RESOLVED: `curl` mangles non-ASCII on Windows

A Hindi query sent via `curl -d '{"query":"एंड्रोजेन..."}'` returned unrelated passages and looked exactly like a cross-lingual retrieval bug. It was not — the shell mangled the UTF-8 before it reached the service. The same query through Python `urllib` with explicit UTF-8 encoding returns the correct passage at rank 1, score 0.9050.

Kept here because the false symptom is convincing and someone will hit it again. **Test Indic-language endpoints with a real HTTP client, not shell `curl`.**

---

## What is explicitly *not* an issue

- **P50 of 3.31 ms.** Not too good to be true — the breakdown accounts for it (2.81 ms embed + 0.42 ms search + 0.03 ms answer), the warmup discard is honest, and the stub rig was independently validated to 0.05 ms of overhead in Phase 0.
- **int8 quantization loss.** Verified against fp32 on real retrieval: Recall@10 identical at 1.000, Hit@1 0.945 vs 0.935.
- **The chunker doing almost nothing.** C1 emits 1.28 chunks per passage because the corpus genuinely has short passages. That is decision D8 working as designed, not a bug.
