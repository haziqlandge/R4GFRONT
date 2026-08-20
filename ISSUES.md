# ISSUES.md

Open problems, measured rather than assumed, as of **18 August 2026** (end of Phase 2, start of Phase 3).

Severity is about the submission, not about engineering neatness:

- **P0** — would visibly break the demo or invalidate a published number
- **P1** — materially weakens a scored requirement
- **P2** — real, bounded, has a known home in a later phase
- **RESOLVED** — investigated and closed, kept here because the reasoning matters

---

## Summary

| # | Issue | Severity | Lands in |
|---|---|---|---|
| I1 | Hindi P100 spike, 119 ms from one pathological query | **RESOLVED (built, 20 Aug)** | Phase 6 |
| I2 | Hit@1 is 0.362 en / 0.224 hi — the naive answer is usually wrong | **P1** | Phase 5 |
| I3 | Dense cosine cannot separate gibberish from a correct answer | **P1** | Phase 6 |
| I4 | Eight indexes cannot co-reside in RAM (A4 false) | **P1** | Phase 3 |
| I5 | Hindi retrieval trails English by 0.19 Recall@10 | **P2** | Phase 3/5 |
| I6 | Thread counts in config are wrong for both workloads | **P2** | now |
| I7 | Groq free tier caps Band B at ~50 queries | **P2** | Phase 5 |
| I8 | All benchmarks are local x86, not the deployed box | **RESOLVED (measured 20 Aug, and it closed against us)** | Phase 7 |
| I9 | `build_passage_map` reconstructs passages approximately | **P2** | Phase 5 |
| I10 | Two degenerate `-` passages act as attractors | **P2** | Phase 3 |
| I11 | Schedule: 3 days behind the original plan | **P1** | ongoing |
| I12 | Non-ASCII through `curl` on Windows silently mangles | **RESOLVED** | — |
| I13 | Build time is no longer comparable across strategies | **P2** | Phase 3 (J15) |
| I14 | The 5070 Ti is the only unproven part of the toolchain | **P1** | Phase 3 (J5) |
| I15 | Amends I7 — the Groq cap also rules out offline use | **P1** | Phase 3 (J6) |
| I21 | Eval results assembled from runs with different query counts | **RESOLVED** | J15 harness |
| I22 | Scoring raw chunks biases against fine-grained chunkers | **RESOLVED** | J15 passage dedup |
| I16 | `tests/test_lexical.py` pushed before J11 exists — collection fails | **RESOLVED** | — |
| I17 | ~~BM25 widens the en/hi gap~~ — **corrected**: the gap is flat across retrievers | **P2** | Phase 5 |
| I18 | Lexical P99 breaches its 12 ms stage timeout on English | **P2** | Phase 3 (J12) / Phase 5 |
| I19 | RRF fusion does not earn its place at the reranker's depth | **P1** | Phase 3 (J16) / Phase 5 |
| I20 | C7 as specified leaks the answer key; A5 cannot be tested here | **P0** | Phase 3 (J16) |
| I21 | C5 and C6 are retrieval-identical to C1 by construction | **P2** | Phase 3 (J15) |
| I22 | C4 killed on a costed impossibility, not deferred | **RESOLVED (decision)** | — |
| I23 | Strategy deltas need PAIRED tests; unpaired CIs hide real effects | **P1** | Phase 3 (J15) |
| I24 | int8 cross-encoder scores shift with batch composition | **RESOLVED** | Phase 5 |
| I25 | Stage timeouts do not fire for synchronous stages | **P0** | Phase 5 / Phase 6 |
| I26 | The abstention floor detects off-topic input, NOT ungrounded answers | **P0** | Phase 6 |
| I27 | The score-gap ambiguity check cannot ship: it refuses real questions first | **RESOLVED (measured, rejected)** | Phase 6 |

---

## I1 — Hindi P100 spike: 119 ms from one query

**Status: BUILT AND CLOSED, 20 Aug 2026.** `guardrails/input_guard.py`, first
stage in the pipeline. Verified against the real tokenizer over the frozen 250:
**499 of 500 queries accepted, one rejected**, and the rejection is exactly
`query_id 156297`. A rejected request costs 0.1 to 0.3 ms instead of 118 ms.

One correction to the numbers below, found when the real tokenizer was finally
run against it: this entry records the query at **512 tokens**, which is the
count *after* the embedder truncates to its window. The raw count is **2,390**.
Everything the entry concludes is unaffected, but the raw figure is the one the
guard sees and it is 4.7x larger than the number written here.

The original investigation follows, unchanged, because the reasoning about why
the bound is on tokens rather than characters is the part worth keeping.

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

**RESOLVED 20 Aug 2026, by deploying and measuring. The answer is that the
headline number does not survive the move.**

Measured on `n2-standard-2` in `asia-south1`, same 250 frozen queries, same 30
warmup discards, through the live service:

| | P50 | P70 | P90 | P100 |
|---|---|---|---|---|
| en, i5-12400F | 59.99 | 65.18 | 75.10 | 118.79 |
| **en, deployed** | **190.47** | **198.31** | 216.12 | 250.90 |
| hi, i5-12400F | 73.77 | 80.85 | 95.61 | 155.92 |
| **hi, deployed** | **200.87** | 208.98 | 221.72 | 256.57 |

About 3x slower. English P50 clears 200 ms by 9.5 ms and its P70 is 198.31,
which is the line rather than a margin. Hindi P50 is over. All P90 and P100 are
over.

Not a quantization problem: `avx512_vnni` is present on the box, so the int8
models are on their fast kernels. It is clock and cores. The Xeon runs at
2.80 GHz against the i5's ~4.4 GHz boost, and 2 vCPU is one physical core plus a
hyperthread against six real ones. The reranker is 94% of the budget spent and
scales with both.

**Levers, in order, re-measuring after each rather than stacking:** resize to
`n2-standard-4` with `ONNX_THREADS_SERVING` 2 to 4 (costs money, no quality);
rerank depth 5 to 3 (free, costs quality, curve is in the Phase 5 entry);
`ef_search` 64 to 48 (smallest, costs recall).

The original entry follows, and it was right.

---

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

## I13 — Build time is no longer comparable across strategies

**Severity: P2.** Eight strategies built on three machines across two backends. The build-time column in the Phase 3 comparison table would compare an i5, a 3060 Ti and a 5070 Ti rather than comparing chunking strategies.

Resolved by **D12**: cost is reported on chunks emitted, tokens embedded, `index.bin` size and projected serving RAM — all hardware-independent. Wall-clock survives as a `meta.json` annotation tagged with `device_tag` and `backend`. Lands in job **J15**.

The honest sentence for the README, per `Rules.md` §1: *"index build time is reported per device because the builds were parallelised across three machines; the strategy comparison is made on chunk count, index size and retrieval quality, all of which are hardware-independent."*

---

## I14 — The 5070 Ti is the only unproven part of the toolchain

**Severity: P1 until J5 closes it.** Blackwell is sm_120 and needs CUDA 12.8 or newer. An older PyTorch wheel installs cleanly and then fails at the first kernel launch with `no kernel image is available for execution on the device`, which presents as a broken install rather than an architecture mismatch.

That is the same class of misleading symptom as the Groq 403 Cloudflare block and the Windows `curl` mangling in I12 — a failure whose message points away from its cause. `PREREQUISITES.md` §2.3 therefore requires a **real kernel launch** as the smoke test, not `torch.cuda.is_available()`, which returns `True` on a mismatched build.

It also sits on the critical path (J5 → J6 → J7). 45-minute timebox, then swap the EMBED and LLM roles. Nothing in the plan depends on which GPU runs which job, only on there being two of them.

---

## I15 — Amends I7: the Groq cap also rules out offline use

I7 records the 12,000-token window as capping Band B at roughly 50 queries. It also, and more severely, rules Groq out of the C4 proposition pass entirely: that job needs about **24 million output tokens**.

**C4 through Groq is not slow, it is arithmetically impossible.** See **D11**. C4 moves to a local 3B–7B model on the 5070 Ti, and the Groq quota is reserved for the scored runtime path — the Phase 5 generative fallback and the Band B benchmark.

Standing rule: no offline corpus processing touches Groq.

---

## I16 — RESOLVED: `tests/test_lexical.py` pushed before J11 exists

**Resolved by J11.** `lexical.py` is implemented and all 17 tests pass; the suite
is **52 passed** (was 35 plus a collection error). The original entry is kept
below because the failure mode — a test file committed ahead of the code it
tests, which aborts collection rather than reporting one red test — is worth
recognising quickly if it happens again.

**Severity: P2. Not an environment problem — do not spend prereq time on it.**

`tests/test_lexical.py` imports `BM25Index`, `detect_language`, `tokenize` from
`services/rag_core/retrieval/lexical.py`. That module is still the Phase 3 stub
(`"""bm25s wrapper, Indic-aware tokenization. Phase 3."""`, one line, no code) —
J11 (BENCH's lexical index job) has not started. The test file was committed and
pushed ahead of the implementation it tests.

Effect: `pytest` fails at **collection**, not at a test failure, which aborts the
whole run rather than reporting one red test.

```
.venv/Scripts/python -m pytest --ignore=tests/test_lexical.py
# 35 passed — matches PREREQUISITES.md §4 exactly
```

The `PREREQUISITES.md` §8 ready-check (`pytest` → 35 passed) should be read as
**35 passed, `test_lexical.py` excluded** until J11 lands. No other prerequisite
is affected — system detection, venv, base/bench imports, slice verify
(`7f9f7c59...`), the built C1 index, and the bench latency stub all pass clean.

Resolves itself the moment J11 is implemented; nothing to fix here, just
something to know before running a bare `pytest` and assuming the box is broken.

---

## I17 — CORRECTED: BM25 does not widen the en/hi gap; the gap is flat

**Severity: P2, downgraded from P1. The original claim was my own measurement
error and is retracted below.**

### What I first reported, and why it was wrong

J11 initially reported BM25 at en Recall@10 0.636 / hi 0.432, called the 0.204
gap "wider than dense C1's 0.188", and escalated it to P1 as the outcome J11's
brief warned to watch for.

**The two numbers were measured with different rulers.** The BM25 figure came
from a depth-50 search deduplicated to ten distinct passages; the dense 0.870
came from `05_eval_retrieval.py`, which takes the top-10 **chunks** and maps them
to passage ids *without* deduplicating, so one passage can occupy several of the
ten slots. The deduplicating method searches deeper for the same k and scores
strictly higher. Comparing one against the other is not a comparison.

This is I6's error repeated — a plausible conclusion resting on two different
baselines — which is exactly why I6 is written down. Caught while building J12's
harness, by insisting all three retrievers go through one scoring function.

### Measured consistently (`05b_eval_fusion.py`, all three through one function)

| retriever | en R@10 | hi R@10 | gap |
|---|---|---|---|
| dense | 0.896 | 0.696 | **+0.200** |
| bm25 | 0.628 | 0.424 | **+0.204** |
| fused | 0.836 | 0.608 | +0.228 |

**BM25's gap (0.204) is statistically indistinguishable from dense's (0.200).**
On 250 queries one query is 0.004, so the entire difference is a single query.
BM25 does not widen the gap; it reproduces it almost exactly while sitting about
0.27 lower overall.

### What survives, and what it means

The retracted claim was that BM25 is differentially bad for Hindi. It is not — it
is uniformly worse than dense in both languages, and the **en/hi gap is a
property of the corpus, not of the retriever**. Two independent retrieval methods
with completely different failure modes reproducing the same 0.20 gap is much
stronger evidence for I5's original reading: the Hindi side is machine-translated
and simply carries less recoverable signal. No retriever change is going to fix
that, which redirects I5's remaining hope to the reranker and to Phase 5.

Also worth noting: dense measures 0.896 here against the published 0.870, because
retrieving 50 with `ef_search=64` and truncating to 10 beats retrieving 10
directly. That is a real if minor free win and belongs to J15's remit.

The MT-drift hypothesis stands and is still untested: BM25 has no synonym
tolerance where dense embeddings do, so it should underperform *specifically* on
translated text. Testable against the `is_selected_any` English originals.

---

## I18 — Lexical search P99 breaches its own stage timeout on English

**Severity: P2.** `config.STAGE_TIMEOUT_MS["lexical_search"]` is 12.0 ms and the
budget is 10.0 ms. Measured on BENCH over the frozen 250, three passes, index
loaded from disk:

| | P50 | P99 | P100 |
|---|---|---|---|
| en | 2.93 ms | **13.26 ms** | 15.30 ms |
| hi | 2.04 ms | 9.14 ms | 9.92 ms |

English P99 exceeds the hard timeout, so `StageTimeout` would fire on roughly the
slowest 1% of English traffic. Hindi fits.

**The cost driver is not query length.** Correlation between token count and
latency is **−0.369** for English — negative. The slowest queries (`ipa stands
for what`, `cost to hire painter for kitchen cabinets`) are 4–7 tokens, the same
as the fastest. What they share is high-document-frequency terms (`for`, `what`,
`to`, `how`), so cost tracks total posting-list length scanned, not query size.

### Stopword removal was measured and rejected

The obvious fix makes things worse on the metrics that matter:

| variant | en P50 | en P99 | en MRR@10 | en Hit@1 |
|---|---|---|---|---|
| **baseline (shipped)** | **2.93 ms** | 13.26 ms | **0.341** | **0.208** |
| stopwords dropped, index + query | 8.20 ms | 11.21 ms | 0.346 | 0.212 |
| stopwords dropped, query only | 8.24 ms | 11.10 ms | 0.331 | 0.196 |

It buys ~2 ms at P99 and costs ~5.3 ms at P50 — a 2.8× median regression — while
moving quality by less than noise. Rejected on that trade alone.

**Unexplained and left unexplained deliberately.** Removing terms from the query
makes `bm25s` *slower* at the median, which contradicts the posting-list model
that correctly explains everything else here. It is not the index changing shape:
query-side-only removal against an unmodified on-disk index reproduces the
regression exactly (8.24 vs 8.20 ms). Suspect `bm25s`'s `backend_selection="auto"`
switching scoring paths on query characteristics. Not chased further because the
shipping decision does not depend on the answer — baseline wins on both P50 and
quality — but it is written down because the next person to reach for stopwords
will otherwise redo this.

### What to do about the breach

Not a J11 decision. The 10/12 ms allocation was written in Phase 2 as an estimate
*before this stage existed*, and changing it is a `Latency.md` 4 contract change
that has to be made against the whole budget (175 ms allocated, 25 ms reserve),
not against one stage in isolation. Options for whoever owns that call: widen the
lexical timeout from the reserve, or drop `LEXICAL_TOP_K` below 50 and re-measure.

### Unexpected good news: BM25 is immune to the I1 pathology

`query_id=156297` — 7,168 characters, 1,025 tokens, the repetition loop that cost
the dense path 119 ms and forced the 64-token input guard — **does not appear in
the Hindi slowest eight**. 1,025 tokens of one repeated phrase is only a handful
of *distinct* terms, and BM25 scans posting lists per distinct term. The guard
that rescues the dense path is simply not needed for this one, which is a point
in favour of the guard being a dense-stage concern rather than a global one.

---

## I19 — RRF fusion does not earn its place at the reranker's depth

**Severity: P1.** It is a live architectural question, not a bug: `fusion.py`
works, is tested, and was verified correct before this was written.

### The measurement

`05b_eval_fusion.py`, frozen 250, one scoring function for all three retrievers:

| depth | dense en | fused en | dense hi | fused hi |
|---|---|---|---|---|
| @10 | 0.896 | **0.836** | 0.696 | **0.608** |
| **@20** | 0.936 | **0.936** | 0.748 | **0.736** |
| @50 | 0.960 | **0.980** | 0.824 | **0.832** |

Read the rows in order and the shape is unmistakable. Fusion is **much worse at
10, exactly level at 20, and better at 50.**

### Why Recall@10 is the wrong number to judge it on

Fusion is not the final ranker. The cross-encoder reranks **exactly 20**
candidates (`Architecture.md` 3.6) and produces the order the user sees. So the
only thing fusion owes the system is that gold is *inside* the top 20 — the
reranker can fix order, but it cannot recover a document that never arrived.

At that depth fusion delivers **+0.000 en and −0.012 hi**. It costs a 10 ms
stage (I18, which does not currently fit its own timeout) and buys nothing.

### It is not an implementation bug — that was checked first

Falsification, all 250 English queries: fusing with `weights=[1.0, 0.0]`
reproduces dense's top-10 **exactly, 250/250**. The negative result is real.

The diagnosis is sharp. Fusion promotes **916** rows into the top-10 that dense's
top-10 did not contain, and **13 of them (1.4%) are gold**. Mean overlap between
the two top-50 lists is only 12.9 of 50, so the retrievers disagree about most of
what they return, and unweighted RRF treats both sides of every disagreement as
equally credible. Since BM25 is ~0.27 Recall@10 weaker, that trade loses.

**This is RRF behaving exactly as designed, on inputs it was not designed for.**
RRF assumes retrievers of broadly comparable quality; `1/(k+rank)` gives BM25's
rank-1 the same weight as dense's rank-1. Nothing about the constant k=60 is
wrong — Architecture.md 3.5's reasoning for choosing rank-based fusion over score
normalisation still holds, and I3 is still why scores cannot be added.

### The real tension this exposes

Fusion's only genuine contribution (+0.020 en, +0.008 hi) lands at **depth 50**,
and `Architecture.md` 3.6 already rejected reranking 50: *"doubles the cost for a
marginal gain"*. So the design contains a stage whose benefit appears only at a
depth another stage was explicitly tuned not to read.

Three ways out, for J16 to settle — **not a J12 decision**:

1. **Drop fusion from the default path.** Reclaims 10 ms of a 200 ms budget and
   removes I18 entirely. The code stays; it costs nothing to keep and Phase 5 may
   revive it.
2. **Rerank deeper than 20.** Revisits a tuned number with a measured reason,
   which is legitimate, but spends 25–45 ms to buy ~0.02 recall.
3. **Calibrate RRF weights.** `reciprocal_rank_fusion` already takes `weights`
   for this. Down-weighting lexical should recover most of the @10 loss while
   keeping the @50 gain. **This must happen on the dev partition, never on the
   frozen 250** (`Rules.md` 5) — fitting weights against the bench set would make
   every fused number in the comparison table self-congratulatory.

**Recommendation: option 1 for Phase 3's table, option 3 in Phase 5** once the
reranker exists and there is something to calibrate against. Fusion's value
genuinely cannot be judged before the reranker is real, because its entire case
rests on candidate-set quality rather than on final order.

### Report it either way

Per `Rules.md` 1 this goes in the README as a finding, not omitted as a dead end.
"We built hybrid retrieval, measured it against dense alone at the depth our
architecture actually consumes, and found it did not pay for itself" is a
stronger result than a fusion row quietly left out of the table.

---

## I20 — C7 as specified leaks the answer key, and A5 cannot be tested on this corpus

**Severity: P0.** Not because anything is broken — the shipped C7 is clean — but
because following `Phase3-Parallel.md` J4 literally would have put a fabricated
number into the comparison table, confirming a prediction the project had already
written down.

### The trap

J4 sizes C7 at *"roughly 30,000 extra vectors"* — 15,000 queries x 2 languages,
i.e. **every** query. But `bench/queries_250.jsonl` **is** the `test` split (ids
match exactly: 4578, 7451, 7821, …). Indexing a test query's text against its own
gold passage puts the answer key in the index: searching that query then matches
a vector that *is* the query, pointing at the passage being scored.

`Memory.md` A5 predicts C7 wins. It would have won enormously, and the result
would have read as confirmation rather than contamination.

### Measured, because the size of it is the point

Both variants were built and evaluated on the frozen 250 (`c7-leaky/` is a
diagnostic and is never published):

| | en R@10 | en MRR | en Hit@1 | hi R@10 | hi MRR | hi Hit@1 |
|---|---|---|---|---|---|---|
| c1 baseline | 0.896 | 0.518 | 0.340 | 0.696 | 0.382 | 0.252 |
| **c7 honest** | 0.872 | 0.508 | 0.336 | 0.656 | 0.353 | 0.228 |
| **c7 leaky** | 0.972 | 0.874 | **0.808** | 0.936 | 0.847 | **0.792** |

The leak is worth **+0.47 Hit@1 in English and +0.54 in Hindi**. It would also
have appeared to close I5's multilingual gap — Hindi Hit@1 more than triples —
so it would have looked like the single best result in the project.

### The deeper problem: the split filter does not rescue the strategy

Restricting to `corpus_only` removes the leak but cannot make C7 work here.
Real doc2query indexes *synthetic* queries, so a stored query can resemble a
future unseen query for the same passage. This corpus gives each passage group
exactly one real query, and for an evaluated passage that query IS the evaluation
query. So either the query is indexed (leakage) or the evaluated passage is
unaugmented (no effect). There is no third option.

That is exactly what the honest numbers show: **c7 is slightly WORSE than c1**
(-0.024 en, -0.040 hi Recall@10). The 24,000 added vectors are all attached to
passages no benchmark query asks about, so they can only compete for top-k slots.
The measurement is doing precisely what it should.

### Consequence for A5

**A5 is not confirmed and not refuted — it is untestable on this dataset**, and
that is the honest finding. Validating doc2query needs an LLM to generate queries
per passage, which is the same blocker as C4 (`Devices.md` 5, I15). If a GPU box
generates propositions for C4, generating a few synthetic queries per passage
would make C7 genuinely measurable; that is the only route to an A5 verdict.

`Rules.md` 1: this goes in the README as a finding. "Our predicted winner turned
out to be untestable, and the version that would have won was reading the answer
key" is a far better result than a C7 row quietly reporting 0.97.

### Guards now in place

- `c7_doc2query.py` defaults to `SAFE_SPLITS = {corpus_only}`; `dev` and `test`
  are excluded, and any opt-in sets `leaky: true` in `params()` and `meta.json`.
- `--leaky` writes to `artifacts/indexes/c7-leaky/`, never over the canonical
  `c7/` — the same precaution `--limit` already needed in `02_build_indexes.py`
  after a smoke run destroyed a finished index once.
- Six tests in `tests/test_derived_chunkers.py` pin the default and the flag.

---

## I21 — C5 and C6 are retrieval-identical to C1, by construction

**Severity: P2.** Measured on the frozen 250, all three score **exactly**
en 0.896 / 0.518 / 0.340 and hi 0.696 / 0.382 / 0.252.

This is the design working, not a failure. C5 changes the payload and C6 adds a
parent lookup; neither changes a span, a text or a vector — which is why
`02c_build_derived.py` can copy C1's `index.bin` outright and build each in about
60 seconds instead of 31 minutes. The identical scores are also the strongest
available evidence that the vector reuse is correct: any drift in spans would
have moved these numbers.

**The risk is presentational, and it lands on J15.** A comparison table listing
C1, C5 and C6 with three identical rows invites the reader to conclude that two
strategies "did nothing". What they actually bought is not measured by Recall@10:

- **C5** buys *conditional* retrieval — "answer-bearing Hindi passages only" —
  plus a latency win from a smaller candidate set. `hnswlib.knn_query(filter=…)`
  supports this natively (verified). Its value is a capability, and unfiltered it
  is C1 by definition.
- **C6** buys ~2,000 words of parent context for the answer stage. Its effect is
  on answer quality and prompt size, neither of which Phase 3 measures.

J15 should report both as ties **with the footnote**, and Phase 5 should measure
what they actually change. Reporting them as bare ties would be technically true
and substantively misleading.

---

## I22 — C4 is killed, on a costed impossibility rather than a deferral

**Decision, 19 August.** Not "ran out of time" — the cost was computed before the
attempt, and the arithmetic is the deliverable.

### The cost model

| quantity | value | source |
|---|---|---|
| passages to decompose | 295,890 | frozen slice |
| output tokens per passage | ~80 | `Phase3-Parallel.md` J6 sizing |
| **total output tokens** | **~23.7 M** | product of the above |
| 3B model, 4-bit, this CPU | ~15 tok/s | no usable GPU |
| **wall clock, 3B** | **~18 days** | 23.7M / 15 |
| 1B model, 4-bit, this CPU | ~40 tok/s | optimistic |
| **wall clock, 1B** | **~7 days** | 23.7M / 40 |
| time to code freeze | **< 3 days** | `Rules.md` 7 |

Hardware: the only GPU on BENCH is a **GT 710**, Kepler / sm_35, below the floor
of every current CUDA wheel. Groq is excluded by I15 — a 12,000-token window
against a 23.7-million-token job is not slow, it is arithmetically impossible.

### Why it is not worth chasing on another box either

`Phase3-Parallel.md` J6 already warns C4 "is a genuine risk of producing a
*worse* index than C1 while costing far more, because an LLM restating a
machine-translated Hindi passage is a lossy pass over an already-lossy text."
That risk is now better founded than when it was written: I17 established that
the en/hi gap is a property of the machine-translated corpus rather than of any
retriever. A generative pass over that text adds a second lossy translation on
top of the first, and there is no measurement in this project that would let us
attribute the result to the strategy rather than to compounding MT damage.

### What ships instead

The cost model above, published. "Costed at 23.7M output tokens, 7–18 days on
available hardware, killed on 19 Aug with the arithmetic shown" is an engineering
result. A half-built C4 is a broken index. Per `Rules.md` 1 this goes in the
README as a finding, not omitted.

**Reversal condition:** a working CUDA box with continuous batching makes C4 a
few hours rather than days. If EMBED or LLM comes up, C4 is reinstated on its
original terms — the decision is about available hardware, not about the idea.

---

## I23 — Strategy comparisons need PAIRED significance tests

**Severity: P1, and it changes how J15 must report every number.**

Measured on the frozen 250 with 10,000 bootstrap resamples:

| | value | 95% CI |
|---|---|---|
| c1 en Recall@10 | 0.896 | [0.856, 0.932] |
| c1 hi Recall@10 | 0.696 | [0.636, 0.752] |
| **unpaired CI half-width** | **±0.038 en / ±0.056 hi** | |
| **paired delta, c7 − c1, en** | **−0.024** | **[−0.044, −0.008] significant** |
| **paired delta, c7 − c1, hi** | **−0.040** | **[−0.064, −0.020] significant** |
| en/hi gap (c1) | +0.200 | [+0.140, +0.260] **real, not noise** |

### The trap, stated plainly

Read the unpaired CIs alone and every strategy in this project looks
indistinguishable — c7's ±0.04 sits well inside a ±0.038 error bar, and the
tempting conclusion is "the eval is saturated at 0.896, nothing can be measured,
stop building strategies."

**That conclusion is wrong, and the paired test proves it.** All strategies are
evaluated on *identical* queries, so the correct test is on the per-query
difference, which cancels the query-difficulty variance that dominates the
unpaired interval. Paired, c7's small deficit is unambiguously real in both
languages.

### Consequences

1. **J15 must report paired bootstrap deltas against C1**, not just absolute
   numbers with independent error bars. Reporting unpaired CIs would understate
   the eval's sensitivity by roughly 4x and invite exactly the wrong call.
2. A **standalone** claim ("C8 reaches 0.92") still needs to clear ~0.08 en /
   ~0.11 hi to mean anything in isolation. Paired-vs-C1 and absolute claims have
   different thresholds and must not be mixed in one sentence.
3. The **en/hi gap is statistically real**, which retires any lingering doubt in
   I5/I17 that it might be small-sample noise.
4. **Hit@1 is 0.340 en / 0.252 hi against Recall@10 0.896.** The right passage is
   in the top ten far more often than it is at rank one, so the remaining
   headroom is in *ranking*, not retrieval. Chunking cannot reach it; the Phase 5
   cross-encoder is the lever. This is the strongest available argument that
   Phase 5 matters more than any further chunking strategy.

---

## I21 — Eval results were assembled from runs with different query counts

**Severity: P1 for the Phase 3 decision. Found 18 Aug while evaluating C8.**

`05_eval_retrieval.py` defaults to `--limit 500`. The c1/c5/c6/c7 runs were made
with `--limit 250`; the c8 run used the default. Comparing the stored JSONs
therefore compared sample sizes, not strategies:

| stored file | queries | en Recall@10 |
|---|---|---|
| c1 | 250 | 0.896 |
| c8 | 500 | 0.870 |

That reads as "C8 is 0.026 worse". Re-run through one code path on the same 500
queries, **both score 0.870** — the gap was entirely an artifact.

**The real C8 result, from a paired bootstrap on identical queries (4,000
resamples):**

| | delta (C8 − C1) | 95% CI | |
|---|---|---|---|
| en Recall@10 | +0.000 | [−0.024, +0.022] | not significant |
| en Hit@1 | +0.010 | [−0.016, +0.036] | not significant |
| **hi Recall@10** | **−0.030** | **[−0.050, −0.010]** | **significant** |
| hi Hit@1 | +0.004 | [−0.018, +0.028] | not significant |

So late chunking does nothing for English and measurably **hurts** Hindi.

**Fix, and it is structural rather than a re-run.** The Phase 3 comparison table
must be produced by a single process that evaluates every strategy with identical
settings in one pass (J15), not assembled from separately-dated JSON files. Dated
immutable results are right for tracking a number over time (`Rules.md` §5); they
are the wrong input for a cross-strategy comparison, because nothing in the file
forces two of them to be comparable.

Paired bootstrap deltas belong in that table too. Unpaired confidence intervals
on 250-500 queries are wide enough to hide every effect measured in this phase.

---

## I24 — int8 cross-encoder scores depend on batch composition

**Severity: P1.** Found in Phase 5 by a test that was written to check something
else (that `attention_mask` was being fed correctly). It was, and the drift is
real anyway.

### What was measured

Seven passages of differing lengths, scored by the same model at batch size 1 and
batch size 7:

| build | max abs score delta |
|---|---|
| fp32 | **0.000000** |
| int8 | **0.279366** |
| int8, all passages the same length | **0.000000** |

The pattern identifies the cause exactly. It is not attention masking — the mask is
declared, fed, and correct. ONNX Runtime's dynamic quantization derives activation
scales **per tensor at run time**, so padding rows change the tensor the scale is
computed from, which perturbs the quantized values of the *real* tokens alongside
the padding. Equal-length batches need no padding and are bit-exact; fp32 has no
activation scales to derive and is bit-exact at any batch shape.

### Why it matters here, beyond tidiness

The median adjacent-rank logit gap for this model is **0.364** (measured in the
`03b_export_reranker.py` tau diagnostic). A perturbation of 0.279 is **77% of the
gap between neighbouring candidates**, which is large enough to reorder them. Two
consequences:

1. **Reproducibility.** The same (query, passage) pair scores differently depending
   on which passages happen to share its batch. Top-1 becomes batch-dependent, and
   top-1 is the extractive answer.
2. **Calibration.** Phase 6 sets the abstention floor on the top-1 rerank score
   (this is the whole point of I3 — the dense score cannot carry that signal). A
   threshold is only meaningful against a score that is a function of the pair,
   not of its batch neighbours.

### Resolution

The hot path scores **one pair at a time**. That makes the score a pure function of
(query, passage) and costs whatever batch parallelism was worth — measured in the
Phase 5 depth sweep rather than assumed, since the alternative (length-sorted
batching, as used for the embedder in Phase 2) only *reduces* padding rather than
eliminating it, and a partially-reproducible score is not a category worth having.

**This is the same class of bug as the Phase 2 int8 parity gate**, and the opposite
outcome. There, ordering instability turned out to be noise below the tie gap and
the gate was wrong. Here the perturbation sits just under the tie gap and is
consequential. The lesson is not "ordering tests are bad", it is **measure the
perturbation against the gap it has to survive** — which is now a documented step
in `03b_export_reranker.py` rather than an instinct.

---

## I25 — a stage's `timeout_ms` cannot interrupt a synchronous stage

**Severity: P0.** It invalidates a claim `Latency.md` makes about the whole design.

### What was measured

One pipeline, one stage, `timeout_ms=50`, two implementations of the same 120 ms of
work:

| stage body | wall clock | span status | stage completed |
|---|---|---|---|
| CPU spin (what ONNX inference is) | **123.7 ms** | **`ok`** | **yes** |
| `await asyncio.sleep` (what an HTTP call is) | 47.4 ms | `failed` | no |

The timeout is enforced with `asyncio.wait_for`, which can only fire when the
awaited coroutine yields to the event loop. `embed_query`, `dense_search` and
`rerank` are all synchronous ONNX or C++ calls that never yield, so their declared
timeouts are unreachable — and the overrun is reported as a clean `ok`, not as a
degradation.

### Why this is P0 rather than a curiosity

`Latency.md` §4.1 says of the budget mechanism: *"This is what makes the 200ms
figure a guarantee rather than an average."* Half of that mechanism does not exist
for the stages that consume the budget. What genuinely works is only the
**pre-stage gate**, which compares `remaining_ms` against the stage's allocation
and can decline to *start* it. Once a stage starts, nothing outside it can stop it.

Before Phase 5 this was harmless — every Band A stage measured single-digit
milliseconds, so no stage could plausibly overrun. The reranker changes that: it is
the first stage whose cost is a meaningful fraction of the whole budget, which is
what made this visible.

### Resolution, and its limit

`CrossEncoder.rerank()` takes a `deadline_ms` and checks it **between pairs**. This
is only possible because I24 already forced batch-size-1 scoring for
reproducibility, which leaves a yield point every ~11 ms. On expiry the unscored
candidates are demoted below the scored ones rather than dropped, and the method
returns `n_scored` so the trace can distinguish a partial rerank from a complete
one instead of presenting one as the other.

**The limit is real and worth stating:** this fixes the one stage that needed it.
`embed_query` still cannot be interrupted, which is exactly the mechanism behind
I1's 118 ms pathological query — it is not that the timeout was too generous, it is
that the timeout could never have fired. The Phase 6 input guard bounds that case
at the input instead, which is the correct layer for it and is now the *only* thing
protecting it.

A general fix would mean running sync stages in a thread pool. That buys a bounded
*response* time but not bounded CPU — Python cannot kill a running thread, so the
work continues burning a core after the caller gives up. On a 2 vCPU box that
trades a slow request for a degraded process, which is the wrong trade. Deadlines
checked inside the work are better where the work can be chunked.

---

## I26 — the abstention floor detects OFF-TOPIC input, not UNGROUNDED answers

**Severity: P0.** It corrects a claim the Phase 5 entry made about its own best result,
and it changes what Phase 6 has to build.

### The claim that was made

The Phase 5 calibration reported that `tau_low = -1.103` catches **100% of
genuinely-unanswerable queries and 100% of gibberish** at a 5% false-abstention
cost, and that this "vindicates Architecture.md 3.6" and makes requirement 6 real.

Both numbers are true. The conclusion drawn from them was not.

### What a council review forced, and what the data says

The challenge: *"100% abstention catch at 5% false-abstention cannot coexist with
'the LLM says INSUFFICIENT_CONTEXT on 50% of queries' and 'top-1 is right 40% of
the time'. Your floor is probably detecting out-of-distribution input rather than
absence of grounding."*

Re-analysed from the existing calibration file, no new run needed:

| measurement | value |
|---|---|
| mismatched pool caught (query vs a different query's candidates) | **100.0%** |
| gibberish caught | **100.0%** |
| **wrong top-1 that scores ABOVE the floor and is answered anyway** | **92.5%** |
| correct top-1 wrongly abstained on | 0.6% |
| **share of everything we ANSWER that is wrong** | **62.1%** (295 of 475) |

### The distinction that was being collapsed

The two negative populations used for calibration are not the same difficulty:

- **mismatched / gibberish** — the candidate pool is *topically unrelated* to the
  query. The cross-encoder scores this at a median of -7.28 against +8.30 for a
  correct hit. Enormous separation, trivially caught.
- **topically related but wrong** — the pool is about the right subject and the top
  passage simply does not answer the question. The cross-encoder scores these at a
  median of **+5.89**, barely below the +8.30 of a correct answer, and far above
  the floor.

Only the first was measured. The claim "the system knows when not to answer" was
then made on the strength of it.

**So: `tau_low` is an excellent out-of-domain detector and a poor grounding
detector.** Requirement 6 is genuinely satisfied for off-topic, gibberish and
unanswerable-from-corpus input — which is three of Phase 6's five adversarial
categories — and is genuinely NOT satisfied for the most common real case, where
retrieval returns something plausible and wrong.

### Why this is the same mistake as Phase 3, again

Phase 3's write-up claimed "nothing beats C1" and had to be corrected to "C1 and C8
are statistically tied on English". Both errors have the same shape: a real
measurement generalised past what it measured. The Phase 3 fix was to count only
independently-measured arms; the fix here is to name the population a number was
measured on whenever the number is quoted.

### What follows

1. **Never quote the 100% figure without its population.** The honest sentence is
   "100% of off-topic and unanswerable-from-corpus queries", not "100% of queries
   it should refuse".
2. **Phase 6's output guard is now load-bearing, not a nicety.** Groundedness has
   to be checked against the *answer*, not inferred from the retrieval score - term
   overlap plus a cheap entailment check is the intended mechanism and it is the
   only thing that addresses the 62.1%.
3. **The `is_selected` caveat is real but does not rescue this.** MS MARCO labels
   are sparse, so some passages counted wrong are genuinely useful. That inflates
   the 62.1% somewhat. It does not change the shape: the floor cannot tell the two
   cases apart, whatever the true rate is.

### Update, 20 Aug: the output guard exists now, and it does NOT close this

`guardrails/output_guard.py` is built and live, and it is the layer that reads
the answer rather than the scores. It is a real improvement and it is **not** the
fix for the 62.1%, so do not describe it as one.

What it measures is lexical overlap between the answer and its cited passages.
Measured on a worked example: a verbatim span 1.000, a FALSE sentence reassembled
out of the passage's own words **0.833**, a TRUE paraphrase **0.639**, an answer
about something else 0.062. **The false reassembly outscores the true
paraphrase**, which is the same lesson as this issue in miniature: the measure
answers "is this wording traceable to the source" and no cut on it answers "is
this claim correct".

So the floor is set at 0.35 to catch the bottom of that list, and the honest
sentence is that the system now checks its answers against their sources for
*provenance* rather than for *truth*. `tests/test_output_guard.py` pins the
inversion so this cannot drift into an overclaim without a test going red.

---

## What is explicitly *not* an issue

- **P50 of 3.31 ms.** Not too good to be true — the breakdown accounts for it (2.81 ms embed + 0.42 ms search + 0.03 ms answer), the warmup discard is honest, and the stub rig was independently validated to 0.05 ms of overhead in Phase 0.
- **int8 quantization loss.** Verified against fp32 on real retrieval: Recall@10 identical at 1.000, Hit@1 0.945 vs 0.935.
- **The chunker doing almost nothing.** C1 emits 1.28 chunks per passage because the corpus genuinely has short passages. That is decision D8 working as designed, not a bug.

---

## I27 — the score-gap ambiguity check does not survive measurement

**Severity: RESOLVED by rejecting it.** Nothing is broken. `Architecture.md` 7
Layer 2 specifies a score-gap check and it was built as a measurement first,
which is the only reason this is a finding rather than a shipped defect.

### The idea

When the top two reranked candidates score alike, no single passage is clearly
the answer, so refuse with `AMBIGUOUS_RETRIEVAL`. It is the obvious reading of
"knows when not to answer" for a query like `mercury`, and the Phase 6
adversarial eval showed exactly the gap it was meant to fill: the ambiguous
category was caught at **25%**, against 100% for injection and 75% for
off-topic.

### The measurement

Score gaps over `bench/adversarial.jsonl`, taken from the live service, over the
cases that were answered rather than already refused:

| gap cut | ambiguous caught | answerable lost |
|---|---|---|
| 0.10 | 2 of 9 | 1 of 14 |
| 0.50 | 4 of 9 | 2 of 14 |
| 0.75 | 5 of 9 | 4 of 14 |
| 2.00 | 8 of 9 | 6 of 14 |

There is no cut worth taking. Catching a bare majority of the ambiguous cases
costs 29% of real questions; catching nearly all of them costs 43%.

### Why, and it is not a threshold problem

The distributions do not merely overlap, they interleave. The real question
*"what happens during a docket call in court"* has a gap of **0.07**, which is
smaller than the gap on the single word *"mercury"* at **0.08**. Any threshold
that refuses mercury refuses the docket question first.

A small gap means several candidates scored alike. That happens when a query is
ambiguous, and equally when the corpus simply holds several good passages about
one subject — which, on a 295,890-passage web corpus, is the normal case for a
well-posed question. The gap cannot separate those two, so it is not a refusal
signal.

**Caveat, because the numbers are small.** Nine ambiguous and fourteen
answerable cases. This is enough to decide not to ship, and not enough to put a
precise price on it. The interleaving rather than the ratio is what makes the
call.

### The same shape as two earlier findings

I3 killed a confidence floor on the dense score because gibberish sat 0.05 from
a correct answer. I19 killed RRF fusion because it did not pay at the depth the
reranker actually reads. This is the third: a component specified in the
architecture, built, measured, and rejected on its own numbers rather than
shipped because the design document named it.

The reasoning lives in `services/rag_core/guardrails/retrieval_guard.py` so the
absence reads as a decision rather than an omission.

### What is left of Layer 2

The confidence floor, which ships in `answering/router.py` because the same
calibrated score also picks the answer path. The language-mismatch flag is
rejected separately and on design rather than data: answering a Hindi question
from the English twin of a passage is this project's cross-lingual claim,
observed firing on live spoken input on 20 Aug, and a guard there would refuse
the system's own headline capability.

**Ambiguity is therefore an open weakness, stated rather than closed.** The
adversarial eval reports it per category so the 25% is visible instead of being
averaged away.

---

## I28 — the embedder's ONNX thread pool was stealing the reranker's cores

**Severity: P0, and RESOLVED 20 Aug 2026.** It was the single largest latency
defect in the project, it had been present since Phase 2, and it was invisible
because every stage reported `ok`.

### What was wrong

`rag_core` holds two ONNX Runtime sessions and one request uses both: the
embedder runs at `embed_query`, the cross-encoder at `rerank`. Both were built
with `intra_op_num_threads = ONNX_THREADS_SERVING`. On the deployed
`n2-standard-4` that is two pools of four workers on four vCPUs, and ORT's pool
does not sleep the moment it finishes a task — it spins. So the embedder's
threads were still burning cores while the reranker ran.

### What was measured

`scripts/07c_ort_contention.py`, on the deployed box, same pairs in the same
order, 50 measured after 10 warmup. Figures are the rerank stage, not the
request:

| arm | depth 3 P50 | depth 5 P50 | embed P50 |
|---|---|---|---|
| reranker alone, no embedder in the process | 57.27 | 97.20 | — |
| embedder at 4 threads (**what shipped**) | **122.52** | **161.31** | 14.29 |
| embedder at 4 threads, spinning disabled | 73.83 | 121.87 | 8.69 |
| embedder at 2 threads | — | 139.13 | 12.19 |
| **embedder at 1 thread** | **58.90** | **97.27** | **8.40** |
| embedder at 1 thread, spinning disabled | — | 122.64 | 7.13 |

One thread for the embedder recovers the standalone reranker cost exactly, and
the embedder gets *faster* doing it — 14.29 ms to 8.40 — because it stops
fighting its own oversubscribed pool. Both sides win, which is rare enough to be
worth stating plainly.

Files: `bench/results/2026-08-20-134018-ort-n2std4.json` and
`-134156-ort-n2std4-d5.json`.

### What it cost while it was live

Band A through the deployed service, 250 frozen queries x 2 passes per language,
30 warmup discarded, before and after the one-line change
(`bench/results/2026-08-20-133614-banda-deployed-n2std4-d3-baseline.json` and
`-134551-...-d3-embed1thread.json`):

| | P50 before | P50 after | P100 before | P100 after | over 200 ms |
|---|---|---|---|---|---|
| en | 132.59 | **64.48** | 206.47 | **122.91** | 1/500 → 0/500 |
| hi | 142.30 | **75.88** | 223.44 | **147.88** | 6/498 → 0/498 |

### Why it was invisible for six phases

Nothing was broken. Every span closed `ok`, the arithmetic in `Latency.md` 4 was
consistent with what the stages reported, and the stage that looked expensive —
the reranker — genuinely is the expensive one. The only signal was a ratio
nobody had reason to compute: the cross-encoder costs ~18 ms per pair on this
box, so depth 3 should be ~55 ms, and the service was reporting 118. Comparing
the isolated component against the same component inside the process is what
found it, and it is worth doing routinely rather than once.

### What it invalidates

**Two Phase 7 decisions were taken against this defect and both were wrong.**

- `RERANK_TOP_K` was cut from 5 to 3 to fit depth 5's cost on the deployed box.
  That cost was the bug. Depth 5 fits with the fix, and depth 5 is the better
  arm on Hindi Hit@1, MRR and nDCG. **Reverted to 5.**
- `ONNX_THREADS_SERVING` was raised from 2 to 4 to track the resize from
  `n2-standard-2` to `n2-standard-4`. That raise is what made the contention
  severe. Measurement then showed the cross-encoder does not scale past two
  threads at all (17.78 ms per pair at 2, 17.88 at 4), so the raise bought
  nothing even without the contention. **Set to 2**, and the spare cores now buy
  worker processes instead — see I29.

`ISSUES.md` I6 is the same shape of finding one level down: more threads is not
more speed. I6 is one session oversubscribing itself; this is two sessions
oversubscribing each other. `Latency.md` 8 lever 4 says to pin thread counts and
measure, and it did not anticipate that the counts have to be chosen *against
each other* rather than one at a time.

---

## I29 — one uvicorn process serves one request at a time, whatever the box

**Severity: P1, MITIGATED 20 Aug 2026.** Band A was never wrong; the number a
person with a browser experiences was, and only under concurrent load.

### What was measured

Four concurrent clients against the 4-vCPU box, 250 queries, English
(`bench/results/2026-08-20-135949-banda-deployed-n2std4-d5-conc4.json`):

| | concurrency 1 | concurrency 4 |
|---|---|---|
| Band A P50 (in-process) | 102.48 | **101.04** |
| Band A P100 | 181.34 | 180.04 |
| client wall P50 | 135.82 | **417.61** |
| client wall P100 | 368.49 | **698.16** |

Per-request Band A does not move at all. That is the signature of perfect
serialization: nothing ran slower, requests queued. Every Band A stage is
synchronous ONNX or C++ that never yields to the event loop (the same property
`I25` is about), so one uvicorn process runs one pipeline at a time no matter
how many cores it is given.

**This is where the ~700 ms tail people were seeing came from**, and it is not
in Band A by construction — the trace starts when the pipeline starts, after the
queue wait.

### The fix, and why the numbers are what they are

`uvicorn --workers N` with `N = vCPUs / 2`, paired with
`ONNX_THREADS_SERVING = 2`. The pairing is the point: the cross-encoder stops
scaling at two threads, so anything above two is a core that could have been
serving a second request instead.

Deployed as 8 vCPU, 4 workers, 2 threads
(`bench/results/2026-08-20-140648-banda-deployed-n2std8-w4t2-conc1.json`,
`-140703-...-conc4.json`, `-140716-...-conc8.json`):

| concurrency | Band A P50 | Band A P100 | wall P50 | wall P100 | over 200 ms |
|---|---|---|---|---|---|
| 1 | 95.31 | 182.89 | 126.17 | 444.77 | 0/250 |
| 4 | 139.35 | 189.52 | 173.37 | 416.19 | 0/250 |
| 8 | 140.51 | 187.40 | 366.84 | 707.42 | 0/250 |

Wall clock at four concurrent clients falls from 698 ms to 416. At eight it
returns, because eight concurrent requests on four workers is a queue again —
honestly reported rather than tuned away. Band A stays inside 200 ms at every
concurrency measured, which is the claim that was made.

Each worker loads its own ~2.5 GB of index, passage store and models, so worker
count is bounded by RAM as well as by cores: 4 workers is ~11 GB of the 32 GB on
`n2-standard-8`.

---

## I30 — realtime partial transcripts arrive in the wrong script for Hindi

**Severity: P2. Measured, understood, and the feature is switched off rather
than shipped wrong.** It is not a bug in our code.

### What was asked for

A live transcript: the question appearing word by word under a blinking caret
while someone speaks, instead of arriving whole when they stop. The condition
set was explicit — build it only if the speech-to-text actually supports
streaming partials.

### It does, and that part works

`scripts/08_probe_realtime_stt.py`, against `saaras:v3-realtime` on the deployed
box, 5.8 s of synthesized speech: **19 partial events**, the first at 991 ms,
each extending the last word by word, and the final 385 ms after the audio
ended. Through our own relay and Caddy (`08b_probe_live_relay.py`): 7 partials
and a correct final. **This closes `Memory.md` A3**, open since Phase 4.

Two things had to be corrected to get there, and both would have failed quietly:

- `config.py` pointed at `wss://api.sarvam.ai/speech-to-text/ws`, the **legacy**
  streaming socket, whose documented interim-result behaviour is "None; only a
  final transcript per utterance". The realtime host is
  `speech-to-text-realtime/ws`.
- The realtime endpoint renames the parameters. `encoding=linear16` rather than
  `input_audio_codec=pcm_s16le`; `stream_type` takes `fast`/`balanced`/
  `simulated` and **not** `vad`; segmentation is a separate `endpointing`.

### What is wrong with it

Tried with a real microphone: English was excellent. **Hindi streams in Latin
script.** The partials read "Qatar ki rajdhani kya hai" and only the final
becomes "कतर की राजधानी क्या है".

Every combination was measured before giving up
(`scripts/08c_probe_hindi_partials.py`), same synthesized audio each time:

| language_code | mode | partials | final |
|---|---|---|---|
| auto | transcribe | romanised | correct |
| auto | codemix | romanised | correct |
| auto | verbatim | romanised | **corrupt for English** |
| auto | translit | romanised | romanised |
| **hi-IN** | transcribe | **Devanagari** | correct (Hindi audio) |
| **hi-IN** | transcribe, **English spoken** | devanagari | **`व्हाट इज द कैपिटल ऑफ कतार`** |
| en-IN | transcribe | latin | correct (English audio) |

`stream_type` `fast` and `balanced` are indistinguishable on this axis.

### Why it is off rather than pinned

The only setting that fixes Hindi partials is pinning `language_code`, and a
pinned socket does not merely mis-render the other language — **it corrupts the
FINAL**, which is the string sent to `rag_core`. `व्हाट इज द कैपिटल ऑफ कतार`
retrieves nothing, so a pinned socket trades a correct answer for a prettier
caption. Asking a visitor to declare their language before speaking would avoid
that and give up the auto-detection this system is built around, on the one
screen where cross-lingual behaviour is meant to be visible.

So `LIVE_TRANSCRIPT` in `frontends/_shared/app.js` is `false` and the transcript
appears whole, as it did before. The relay, the browser client, the caret and
the fallback are all built and tested; the constant's comment carries this table
so the next person does not re-derive it.

### What it costs elsewhere

**The `Latency.md` 5 speculative prefetch now has a second blocker.** It
specifies matching a stable partial against the final within a normalized edit
distance of 0.15. Under `language_code=auto` the partial and the final are in
different scripts for every Hindi utterance, so that comparison fails outright —
not marginally. Anyone building the prefetch has to solve the language problem
first, and it is not a threshold they can tune.

---

## I31 — it answers confidently with the wrong passage, and no threshold fixes it

**Severity: P1. Half of this is FIXED, half is measured and open.** Raised from a
user report: "many times the model retrieves something but it's unrelated to the
question — the answer may contain the word but the full answer is very
unrelated", plus "sometimes it retrieves Hindi answers for English questions".

### The method

Sixty general-knowledge questions were written **blind**, thirty English and
thirty Hindi, without looking at the corpus first — the questions a visitor
actually types, not questions reverse-engineered from what happens to be
indexed. They were run against the deployed service alongside the frozen 250 as
an in-corpus control (`scripts/09_relevance_floor.py`).

The existing Phase 6 adversarial set does not cover this band. Its off-topic and
gibberish cases score far below the floor and are caught. These do not.

### Finding 1: raising the abstention floor does not work

| floor | in-corpus kept | precision | blind-60 refused |
|---|---|---|---|
| **-1.103 (shipped)** | 93.4% | 36.9% | 20.0% |
| 2.00 | 79.8% | 39.4% | 40.0% |
| 3.00 | 73.3% | 40.2% | 50.0% |
| 4.00 | 64.9% | 41.4% | 61.7% |
| 6.00 | 49.5% | 42.9% | 70.0% |

The distributions interleave. In-corpus scores run P25 2.74 / P50 5.87 / P75
9.14; the blind-60 run P25 -0.19 / P50 2.90 / **max 10.93** — a higher maximum
than the in-corpus P75. Moving the floor to 4.0 buys **+4.5 points of precision
for -28 points of coverage**.

Two candidate signals were then scored directly on their ability to separate a
right top-1 from a wrong one, over 466 in-corpus queries:

| signal | AUC |
|---|---|
| absolute rerank score | **0.606** |
| margin over second | **0.586** |

0.500 is a coin flip. **Neither signal can carry a threshold.** This is the same
shape as I27, which rejected the score-gap check for ambiguity, and it is I26
restated: the score knows whether a question is in the corpus's world and does
not know whether the passage answers it.

**No fix ships for this half, deliberately.** A "low confidence" badge driven by
a signal with AUC 0.61 would be a second claim that the number does not support.
The honest surface is the one already there: the cited passage is on screen, and
the reader can see what the answer was drawn from.

### Finding 2: the language mismatch was real, and fixing it also fixed a metric

Top-1 came back in the **other** language on **9 of 499** in-corpus queries
(1.8%). Those nine scored:

| | Hit@1 |
|---|---|
| strict, as published | **0.0%** |
| comparing the passage and ignoring the language tag | **66.7%** |

Six of the nine had found the **right passage**. They were counted as complete
misses because gold ids are language-tagged (`gold_en_ids` / `gold_hi_ids`), so
a cross-language hit cannot match one by construction. Cross-lingual retrieval
was working *better* than same-language retrieval on those queries — 66.7%
against 37.6% — and the metric was recording it as a total failure.

**Fixed** by answering from the parallel twin: the corpus carries both languages
for **100%** of its 147,945 passage groups, so the twin always exists.
`build_answer(..., prefer_language=...)` swaps the cited passage for its twin in
the language that was asked. Nothing is dropped, reordered or re-scored, and the
trace records `answered in hi, N passage(s) swapped for their twin`.

Measured before and after, same 499 queries:

| | Hit@1 (strict) | language mismatches |
|---|---|---|
| before | 37.1% | 9 of 466 |
| **after** | **38.2%** | **0 of 466** |

**+1.07 points of Hit@1 that were being thrown away by the metric**, and the
presentation bug is gone.

This does not weaken the cross-lingual claim. The cross-lingual match still
happens and is still what the reranker scored; the reader is simply shown the
half of the parallel pair they can read.

---

## I32 — C3 semantic chunking loses to C1, and the overlap is why

**Not a defect. A measured result, recorded because it contradicts the reason
the strategy exists.** C3 was time-boxed out of Phase 3 and reported as
"reasoned, not measured"; it was built on 21 August in 61 minutes and measured.

### The result

Paired against C1 on the same 500 dev queries, 4000 bootstrap resamples, one
process, one query list, one embedder
(`bench/results/2026-08-20-190027-comparison-j15.json`):

| | C1 | C3 | delta | 95% CI | significant |
|---|---|---|---|---|---|
| en Recall@10 | 0.878 | 0.848 | **-0.030** | [-0.054, -0.006] | **YES** |
| hi Recall@10 | 0.714 | 0.660 | **-0.054** | [-0.078, -0.030] | **YES** |
| hi nDCG@10 | 0.453 | 0.432 | -0.021 | [-0.037, -0.006] | **YES** |
| en Hit@1 | 0.356 | 0.362 | +0.006 | [-0.026, +0.040] | no |
| hi Hit@1 | 0.226 | 0.236 | +0.010 | [-0.014, +0.034] | no |
| en MRR@10 | 0.521 | 0.515 | -0.006 | [-0.030, +0.017] | no |

**The Hit@1 gain is not a win and must not be quoted as one.** It points the
opposite way from recall and both intervals span zero. Quoting it would be
selecting the one metric that flatters a strategy which measurably hurt
retrieval — which is what I23 introduced paired tests to prevent.

### Why it lost

C3 cuts at sentence boundaries and its chunks **do not overlap**. C1 is a
96-token window with **24 tokens of overlap**, so text sitting near a boundary
appears in two C1 chunks and only one C3 chunk. C1 therefore gets more chances
to match a query, and it has 379,240 chunks against C3's 346,383 — 8.7% more,
for 56 MB more index.

**On this corpus the overlap is doing the work, not the boundary placement.**
Semantic coherence is the entire thesis of the strategy and it bought nothing
measurable. The corpus explains it: passages are p50 48 words and 3.14
sentences, so there is not enough document for "follow the meaning" to have
anything to follow. Semantic chunking is a technique for long documents, and
`Architecture.md` 4.1 already noted that nothing here splits long documents.

This is the third strategy to lose to a 96-token window with overlap — C7 on
contamination-free terms, C8 on Hindi, now C3 on recall — and C5 and C6 are C1
wearing a different payload. **The baseline keeps winning, which is a result
about the corpus rather than a failure to try harder.**

### What was done to keep the table honest

The comparison was **re-run from scratch with all seven strategies in one
process**, not appended to the existing six. I21 is about exactly that: a table
assembled from separate runs compares the runs, not the strategies. Every
pre-existing row reproduced to three decimals, which is also the first
reproducibility check this harness has had.

### Cost, for anyone tempted to tune it

61 minutes on an i5-12400F at 8 threads: 927,069 sentences embedded in 29.9 min,
346,383 chunks in 30.2 min, HNSW in 1.2 min. Retuning the percentile would cost
another hour per value **and would be fitting a published number to the
evaluation set**, which `Rules.md` 5 forbids. 92 is the specified value and it
stays.

---

## I33 - an LLM judge is not the correctness signal either, and Hit@1 is noisier than published

**Two findings, one measurement.** Raised from a proposal to cross-check our
answers against an external model, and from watching a rival system (pucho.me)
decline after a confidently wrong extraction.

### The proposal, and the version of it worth testing

The original idea was to compare our answer against an LLM's answer and warn the
user on disagreement. A council review killed that unanimously, and correctly: the
corpus peaks in 2017 (`bench/results/2026-08-20-193717-corpus-vintage.json`), so
a current model disagrees hardest on the answers **most faithful** to the corpus.
The flag would be anti-correlated with correctness.

**Context sufficiency is a different question and survives that objection.** It
never asks "is this true", only "do these passages answer this question".
Staleness cannot poison it: passages about India's population do answer a
question about India's population whatever the number says. That is the
mechanism pucho.me uses, and `DONT-FORGET.md` 10 already recorded the base rate -
gpt-oss-20b returns INSUFFICIENT_CONTEXT on 50% of queries given our top-3. What
had never been measured is whether that judgement **correlates with our top-1
being wrong**. A signal that fires on half of everything is useless if it fires
at random.

### Finding 1: it fires at random. Our own score is better.

`scripts/11_llm_judge.py`, 115 queries from the frozen set, both languages,
sufficiency-only prompt, `openai/gpt-oss-20b`
(`bench/results/2026-08-20-195750-llm-judge-sufficiency.json`):

| target | LLM judge AUC | our rerank score AUC |
|---|---|---|
| strict gold (the exact labelled passage) | **0.542** | 0.616 |
| topical (the right passage group) | **0.632** | 0.696 |

0.500 is a coin flip. **The judge is worse than the signal we already compute for
free**, on both readings. As a detector of a wrong answer it scores precision
0.706 at recall **0.185** - it catches under a fifth of them.

It says SUFFICIENT on 90.0% of our right answers and 81.5% of our wrong ones.
That 8.5-point gap is the entire signal.

So the answer to "is an LLM judge the correctness signal our own scores failed to
be" is **no**, and I31's conclusion stands: this system has no usable correctness
signal. Three candidates have now been measured and rejected - absolute score
(0.606), margin over second (0.586), and an LLM sufficiency judge (0.542).

**Caveat, stated because it bounds the claim:** this is one model, and a small
one. A stronger judge might separate better. It would still have to beat 0.696 to
be worth anything, and it could not run live regardless - I7 caps Groq at 12,000
tokens per window, and the fast path cannot contain a network call
(`Latency.md` 2).

### Finding 2, unplanned and more important: Hit@1 is dominated by label noise

Of the 65 answers this study scored as WRONG, **49 (75%) retrieved a passage from
the same query candidate set** - the right topic cluster, just not the one
MS MARCO labelled `is_selected`. Only 16 were genuinely off-topic.

Under a topical target, **99 of 115 (86%) are right**, against 43% under strict
gold.

`config.py` already suspected this where `ROUTE_TAU_HIGH` is set - "a
topically-correct but unlabelled passage scores zero here and is still useful to
a reader" - and I26 notes that sparse `is_selected` labels inflate the number.
This quantifies it for the first time: **three quarters of the exact-gold misses
are not off-topic retrieval failures.**

**What this does and does not license.** It does NOT mean the system is 86%
correct: the same query candidate set contains passages that do not answer the
question, so "right group" is weaker than "right answer", and the LLM judge -
which agreed on 88% of those rows - is itself near chance and cannot settle it.
What it does mean is that **I26 62.1% is a statement about exact gold labels and
must not be quoted as "62% of answers are useless"**. Those are different claims
and the gap between them is large.

The honest published sentence stays what `DONT-FORGET.md` 7 already says: the
floor knows when a question is outside the corpus and does not know when the
answer it found is wrong.

### A trap worth not re-paying

`gpt-oss-20b` is a REASONING model. With `max_tokens: 8` it spends the budget in
its `reasoning` field and returns `finish_reason: "length"` with an **empty**
`content` string, so the first run of this study collected zero samples and
reported nothing wrong. `config.py` already records this exact behaviour for
`qwen3.6-27b` one model over. The verdict costs 53 completion tokens; 64 is the
floor, with `reasoning_effort: "low"`.
