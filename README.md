# Shruti — a voice-enabled RAG system

Team **OK4T** · HH Goa 2026, Shortlisting Task 2 · `#RAGInGoa`

Speak a question in Hindi or English, get an answer grounded in the AI4Bharat MSMARCO-XI corpus, cited, with a live per-stage latency breakdown — and a system that refuses to answer when it cannot ground the answer.

```
Voice → STT → input guard → embed → hybrid retrieve → fuse → rerank
     → confidence route → [extractive | LLM | abstain] → output guard → response
```

**Status: Phase 3 of 9 complete.** Band A P50 **3.31 ms**, en Recall@10 **0.878**. Chunking explored and settled ([`Memory.md`](Memory.md) Phase 3); the reranker, guardrails, voice and deploy remain. See [`HANDOFF.md`](HANDOFF.md) to pick up.

---

## The honest version of the latency claim

The brief asks for sub-200ms. We publish three bands and state the boundary for each, because the alternative looks like hiding something.

| Band | Boundary | Target | Measured |
|---|---|---|---|
| **A — Core RAG** | Transcript in → response serialized. Guardrails, embedding, dense + lexical search, fusion, reranking, routing, extractive answering, groundedness. No STT, no LLM network call. | < 200 ms | _pending Phase 5_ |
| **B — Core RAG + generation** | Band A routed through the Groq LLM fallback. | reported honestly | _pending Phase 5_ |
| **C — Full wall clock** | User stops speaking → answer painted. | reported honestly | _pending Phase 7_ |

A pipeline containing a hosted LLM call cannot reliably finish in 200 ms — time-to-first-token alone consumes the budget before retrieval starts. So the fast path contains no LLM call: when reranker confidence is high the answer is a verbatim span from a cited passage, which is both faster and structurally incapable of hallucinating. Full reasoning in [`Latency.md`](Latency.md).

The measurement methodology (30-run warmup discard, `perf_counter_ns`, `numpy.percentile` with `method="nearest"`, P100 as the true maximum, dated immutable results) was fixed in Phase 0, **before** there was any pipeline to tune.

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

## Repository layout

| Path | What |
|---|---|
| `services/rag_core/` | The 200ms budget lives entirely inside here. Zero network calls on the fast path. |
| `services/rag_core/harness/` | Typed pipeline: stages, timeouts, retries, circuit breaker, remaining-budget counter, tracing |
| `services/rag_core/chunking/` | Eight strategies, one per file, one shared protocol |
| `services/rag_core/guardrails/` | Four layers: input, retrieval, generation, output |
| `services/stt_gateway/` | WebSocket relay to Sarvam. Holds the key; the browser never sees it. |
| `apps/web/` | Next.js single screen. Mic orb, transcript, answer, latency waterfall. |
| `scripts/` | Offline: download, freeze, index build, ONNX export, benchmarks, evals |
| `bench/` | Frozen query sets and dated results |

## Joining the project

**On a new machine? Start with [`PREREQUISITES.md`](PREREQUISITES.md)** — per-box setup from bare metal to a verified working box.

Then [`HANDOFF.md`](HANDOFF.md) — what a human still has to do by hand, and the traps already paid for.

## Planning documents

[`Devices.md`](Devices.md) the three build machines · [`Phase3-Parallel.md`](Phase3-Parallel.md) the Phase 3 job board · [`ISSUES.md`](ISSUES.md) measured open problems

[`Project.md`](Project.md) scope and success criteria · [`Architecture.md`](Architecture.md) the design · [`Rules.md`](Rules.md) hard constraints · [`Phases.md`](Phases.md) the schedule · [`Latency.md`](Latency.md) the budget · [`Design.md`](Design.md) the interface system · [`Submission.md`](Submission.md) deliverables · [`Memory.md`](Memory.md) decisions, reversals and what they cost

`Memory.md` is the one to read first on a cold start. It carries the *why*.
