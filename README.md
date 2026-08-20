# Shruti — a voice-enabled RAG system

Team **OK4T** · HH Goa 2026, Shortlisting Task 2 · `#RAGInGoa`

**Live: https://shrutirag.duckdns.org**

Speak a question in Hindi or English, get an answer grounded in the AI4Bharat MSMARCO-XI corpus, cited, with a live per-stage latency breakdown — and a system that refuses to answer when it cannot ground the answer.

```
Voice → STT → input guard → embed → hybrid retrieve → fuse → rerank
     → confidence route → [extractive | LLM | abstain] → output guard → response
```

**Status: Phases 0-5, 7 and 8 complete, Phase 6 partial.** Deployed at **https://shrutirag.duckdns.org**. Band A P50 **95.89 ms** English / **115.88 ms** Hindi against a 200 ms budget, measured through the deployed service, **0 of 998 requests over budget**, en Recall@10 **0.878**, **seven chunking strategies** built and compared in one process, 246 tests green, `mypy --strict` clean. Voice input, reranking, routing, abstention, guardrails and the site all work end to end. Guardrail layers 1 and 4 are live and measured over 60 adversarial cases plus 16 controls; layer 2 was measured and deliberately not shipped ([`ISSUES.md`](ISSUES.md) I27). **Phase 9 videos are now the top priority.** See [`HANDOFF.md`](HANDOFF.md) 1A to reach the deployed box and 1B for what changed on 21 August, [`DONT-FORGET.md`](DONT-FORGET.md) 12 for the decisions waiting on a human, and read [`ISSUES.md`](ISSUES.md) I24-I27 before quoting any Phase 5 or 6 number. **Two figures are routinely misread and both are documented:** Hit@1 measures the exact `is_selected` label, and 75% of its "misses" retrieve the right passage group ([`ISSUES.md`](ISSUES.md) I33), so 62.1% is not "62% of answers are useless"; and no threshold predicts whether an answer is wrong - three candidates measured, best AUC 0.606 against a 0.500 coin flip ([`ISSUES.md`](ISSUES.md) I31, I33).

---

## The honest version of the latency claim

The brief asks for sub-200ms. We publish three bands and state the boundary for each, because the alternative looks like hiding something.

| Band | Boundary | Target | Measured |
|---|---|---|---|
| **A — Core RAG** | Transcript in → response serialized. Guardrails, embedding, dense + lexical search, fusion, reranking, routing, extractive answering, groundedness. No STT, no LLM network call. | < 200 ms | **95.89 ms P50 en, 115.88 ms hi**, on the deployed box. P100 183.35 / 182.20, 0 of 998 over budget |
| **B — Core RAG + generation** | Band A routed through the Groq LLM fallback. | reported honestly | **643.83 ms P50.** Over budget, published anyway |
| **C — Full wall clock** | User stops speaking → answer painted. | reported honestly | Sarvam alone 527-911 ms. Reported separately |

250 frozen queries x 2 passes per language, 30 warmup runs discarded, measured
**through the deployed service** in Mumbai — `Latency.md` 6 has always required
that, and until 20 August the published figures came from a development machine
instead.

**It did not start out meeting the target, and how it got there is the more
useful story.** The first deploy measured English P50 190.47 ms and Hindi
200.87 ms — over the line, with the cross-encoder at 94% of the budget. Two
levers were pulled from the optimization list: a bigger instance, then rerank
depth 5 to 3. Both were reasonable and neither was the fix.

The fix was that `rag_core` holds two ONNX Runtime sessions, the embedder and the
cross-encoder, and gave each of them four intra-op threads on a four-vCPU box.
ONNX Runtime's thread pool spins rather than sleeping when it finishes, so the
embedder was burning cores the reranker needed. **Giving the embedder one thread
halved the rerank stage and made the embedder faster at the same time** — English
P50 132.59 ms to 64.48. The rerank depth cut was then reverted, because the cost
that justified it was the bug. [`ISSUES.md`](ISSUES.md) I28 has the tables.

What found it was a ratio nobody had computed for six phases: the cross-encoder
costs ~18 ms per pair in isolation on that box, so depth 3 should have cost
~55 ms, and the service was reporting 118. Timing a component alone and then
inside the process is now step zero of the optimization list in
[`Latency.md`](Latency.md) 8, ahead of every lever.

**The tail is held by a mechanism rather than by margin.** The reranker's
deadline used to ask whether it had already overrun, which cannot bound a stage
nothing can interrupt. It now refuses to *start* a pair that will not fit. Nine
requests over budget became zero, and the three slowest rerank runs in a
250-query pass land within 0.16 ms of each other. It truncates 0.8% of English
and 3.2% of Hindi requests to depth 4, recorded in the trace and quoted here
because a guarantee held by degrading is a different claim from one held by
being fast.

`ISSUES.md` I8 predicted the deployment gap and is closed by it. The full table
with P70 and P90, the per-stage breakdown and the boundary for each band are on
the site's documentation page and in [`Latency.md`](Latency.md).

A pipeline containing a hosted LLM call cannot reliably finish in 200 ms — time-to-first-token alone consumes the budget before retrieval starts. So the fast path contains no LLM call: when reranker confidence is high the answer is a verbatim span from a cited passage, which is both faster and structurally incapable of hallucinating. Full reasoning in [`Latency.md`](Latency.md).

The measurement methodology (30-run warmup discard, `perf_counter_ns`, `numpy.percentile` with `method="nearest"`, P100 as the true maximum, dated immutable results) was fixed in Phase 0, **before** there was any pipeline to tune.

---

## Knowing when not to answer

Requirement 6, measured rather than asserted. `bench/adversarial.jsonl` holds 60
adversarial cases across five categories plus 16 answerable controls sampled from
the dev split, and `scripts/06_eval_guardrails.py` reports precision and recall
per category.

| category | n | refused |
|---|---|---|
| prompt injection | 12 | 100% |
| unsafe | 12 | 100% |
| off topic | 12 | 75% |
| unanswerable from corpus | 12 | 75% |
| **ambiguous** | 12 | **25%** |
| answerable (control) | 16 | 12% refused in error |

Overall abstention recall **0.750**, precision **0.957**.

The control group is the point. An abstention eval with only adversarial cases is
won by refusing everything, so the 16 answerable questions are the false-refusal
denominator and they are reported alongside.

**Ambiguity at 25% is a real weakness and it is published rather than averaged
away.** The obvious fix, refusing when the top two candidates score alike, was
built as a measurement and rejected: catching 5 of 9 ambiguous cases costs 4 of
14 real questions, because the distributions interleave. A genuine question
("what happens during a docket call in court") has a smaller score gap than the
single word "mercury". Full reasoning in [`ISSUES.md`](ISSUES.md) I27.

---

## Reproducing the corpus

Requires Python **3.12** (`onnxruntime`, `hnswlib` and `bm25s` have no 3.14 wheels).

```bash
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python scripts/00_download_dataset.py
.venv/Scripts/python scripts/01_freeze_slice.py
.venv/Scripts/python scripts/01_freeze_slice.py --verify artifacts/slice_manifest.json
```

The last command rebuilds the slice from the committed manifest and asserts it matches. That check, run on a second machine, is the Phase 1 exit criterion.

### The frozen slice

| | |
|---|---|
| Source | `ai4bharat/MSMARCO-XI`, revision `bf5cdc1f`, `validation/hinval.parquet` |
| Languages | English + Hindi (Latn + Deva) |
| Queries | 15,000 — test 1,000 / dev 2,000 / corpus-only 12,000 |
| Passages | 295,890 (147,945 per language, parallel-aligned) |
| Seed | 20260814 |

**Note for anyone else using this dataset:** the documented `load_dataset("ai4bharat/MSMARCO-XI", "hi")` does not work. The repo's loader script resolves `.jsonl` paths that no longer exist — the repo holds `.parquet`. This is also why the HF dataset viewer errors. Download the parquet files directly with `hf_hub_download`.

Every row carries parallel `English_passages` and `Translated_passages`, so one language file yields two aligned corpora. `parallel_id` links the twins, which makes cross-lingual retrieval (ask in Hindi, cite the English source) a checkable event rather than a demo anecdote.

---

## Benchmarking

```bash
.venv/Scripts/python scripts/04_bench_latency.py --stub --breakdown
```

Every run writes a dated, immutable JSON to `bench/results/`. Results are never overwritten — a new run is a new file, tagged with its git SHA.

---

## Running it

Three processes: the pipeline, the speech gateway, and a static server for the site.

```bash
run-dev.bat
```

Then open <http://localhost:3000>. `frontends\serve.bat` does the same and opens the browser for you.

**Port 3000 is not a preference.** `stt_gateway` allows CORS from `localhost:3000` only, because it is the process holding the Sarvam key. On any other port typing works and speaking fails with a CORS error that reads like a broken microphone. Use `localhost` rather than a LAN IP for the same class of reason: `getUserMedia` needs a secure origin and `192.168.x.x` is not one, so the mic silently never prompts.

The site is `frontends/` — plain HTML, one stylesheet and ES modules, served by `python -m http.server`. **There is no build step and no `node_modules`.** It replaced the Next.js app that used to live in `apps/web`, which has been removed; see [`frontends/README.md`](frontends/README.md) and the 20 August entry in [`Memory.md`](Memory.md).

Check <http://localhost:8000/health> before testing. It reports which capabilities actually came up — a dense-only process and a fully reranked one are both "ok" and answer differently.

---

## Repository layout

| Path | What |
|---|---|
| `services/rag_core/` | The 200ms budget lives entirely inside here. Zero network calls on the fast path. |
| `services/rag_core/harness/` | Typed pipeline: stages, timeouts, retries, circuit breaker, remaining-budget counter, tracing |
| `services/rag_core/chunking/` | Eight strategies, one per file, one shared protocol |
| `services/rag_core/guardrails/` | Four layers: input, retrieval, generation, output |
| `services/stt_gateway/` | WebSocket relay to Sarvam. Holds the key; the browser never sees it. |
| `frontends/` | **The site.** Static HTML, one stylesheet, ES modules. No build step. Served on :3000. |
| `scripts/` | Offline: download, freeze, index build, ONNX export, benchmarks, evals |
| `bench/` | Frozen query sets and dated results |

## Before you change anything

[`DONT-FORGET.md`](DONT-FORGET.md) — the facts that are easy to get wrong and expensive to rediscover, each with the file that proves it. Which chunking strategies were actually built, why one published threshold is not the calibrated one, what the abstention floor does and does not detect, and why serving the site on any port but 3000 breaks speech and not typing.

## Joining the project

**On a new machine? Start with [`PREREQUISITES.md`](PREREQUISITES.md)** — per-box setup from bare metal to a verified working box.

Then [`HANDOFF.md`](HANDOFF.md) — what a human still has to do by hand, and the traps already paid for.

## Planning documents

[`Devices.md`](Devices.md) the three build machines · [`Phase3-Parallel.md`](Phase3-Parallel.md) the Phase 3 job board · [`ISSUES.md`](ISSUES.md) measured open problems

[`Project.md`](Project.md) scope and success criteria · [`Architecture.md`](Architecture.md) the design · [`Rules.md`](Rules.md) hard constraints · [`Phases.md`](Phases.md) the schedule · [`Latency.md`](Latency.md) the budget · [`Design.md`](Design.md) the interface system · [`Submission.md`](Submission.md) deliverables · [`Memory.md`](Memory.md) decisions, reversals and what they cost

`Memory.md` is the one to read first on a cold start. It carries the *why*.
