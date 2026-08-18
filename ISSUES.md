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
| I13 | Build time is no longer comparable across strategies | **P2** | Phase 3 (J15) |
| I14 | The 5070 Ti is the only unproven part of the toolchain | **P1** | Phase 3 (J5) |
| I15 | Amends I7 — the Groq cap also rules out offline use | **P1** | Phase 3 (J6) |
| I16 | `tests/test_lexical.py` pushed before J11 exists — collection fails | **RESOLVED** | — |
| I17 | ~~BM25 widens the en/hi gap~~ — **corrected**: the gap is flat across retrievers | **P2** | Phase 5 |
| I18 | Lexical P99 breaches its 12 ms stage timeout on English | **P2** | Phase 3 (J12) / Phase 5 |
| I19 | RRF fusion does not earn its place at the reranker's depth | **P1** | Phase 3 (J16) / Phase 5 |
| I20 | C7 as specified leaks the answer key; A5 cannot be tested here | **P0** | Phase 3 (J16) |
| I21 | C5 and C6 are retrieval-identical to C1 by construction | **P2** | Phase 3 (J15) |

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

## What is explicitly *not* an issue

- **P50 of 3.31 ms.** Not too good to be true — the breakdown accounts for it (2.81 ms embed + 0.42 ms search + 0.03 ms answer), the warmup discard is honest, and the stub rig was independently validated to 0.05 ms of overhead in Phase 0.
- **int8 quantization loss.** Verified against fp32 on real retrieval: Recall@10 identical at 1.000, Hit@1 0.945 vs 0.935.
- **The chunker doing almost nothing.** C1 emits 1.28 chunks per passage because the corpus genuinely has short passages. That is decision D8 working as designed, not a bug.
