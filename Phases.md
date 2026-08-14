# Phases.md

Nine phases across nine days. 14 August to 22 August 2026.

Every phase has an **exit criterion**. A phase is not done when the code is written; it is done when the exit criterion is demonstrably met and the `Memory.md` entry is written. Do not start phase N+1 with phase N unfinished. Half-finished phases stacked on top of each other is exactly the failure mode this document exists to prevent.

**The ordering principle:** measurement before optimization, thin end-to-end slice before depth, deployment before polish. The riskiest unknowns are front-loaded.

---

## Phase 0: Foundation and measurement
**Day: 14 August (today) | Owner: whole team | Duration: half a day**

Nothing else can be evaluated until we can measure. This is deliberately first.

**Status: DONE, 14 August.** See `Memory.md` [Phase 0].

**Tasks**
- Repo created, public, `README.md` skeleton, `.env.example`, `.gitignore`
- Monorepo skeleton per `Architecture.md` section 5, empty modules with correct names
- `harness/trace.py`: trace id, monotonic span timing, span serialization
- `scripts/04_bench_latency.py`: takes a callable, runs it N times, reports P50/P70/P90/P99/P100 with `numpy.percentile`, writes a dated JSON to `bench/results/`
- A trivial stub pipeline (`sleep(10ms)` stages) benched end to end to prove the harness measures correctly
- Sarvam account created, free credits confirmed, key in local `.env`
- Groq account created, key in local `.env`
- Fly.io or Render account created, India region confirmed available

**Exit criterion:** `python scripts/04_bench_latency.py --stub` prints P50/P70/P100 for a stub pipeline and writes a results file. The measurement rig works before any real code exists.

**Why first:** Every decision from here is downstream of these numbers. A team that builds the RAG first and measures on day 7 discovers the architecture is wrong with two days left.

---

## Phase 1: Corpus slice and freeze
**Day: 14 to 15 August | Owner: 1 person | Duration: half a day**

**Status: DONE, 14 August.** See `Memory.md` [Phase 1].

**Tasks**
- `scripts/00_download_dataset.py`: pull `validation/hinval.parquet` from `ai4bharat/MSMARCO-XI` via `hf_hub_download`. **Not `load_dataset`** — the repo's loader script `ms_marco_translations.py` resolves `.jsonl` paths that no longer exist (the repo holds `.parquet`), so `load_dataset` fails and the HF dataset viewer returns `500 dataset generation failed`. There are no per-language *configs*; there are per-language *files*.
- Assert the parquet schema on the real file before sampling: `query`, `Answer`, `query_id`, `query_type`, `passages{is_selected, English_passages, Translated_passages}`, `Eng_Query`, `Eng_Answer`, `source_lang`, `target_lang`. The HF README is stale in at least two places, so nothing about the schema is taken on trust.
- `scripts/01_freeze_slice.py`: sample a fixed slice with a fixed seed, write `artifacts/slice_manifest.json` containing language list, row counts, seed, dataset revision SHA, split id lists and a content hash.
- Normalize into the internal `Passage` record — see `Architecture.md` §4.2. **There is no `url` field in this dataset.**
- Write `passages.parquet`, `queries.parquet` and `bench/queries_250.jsonl` (frozen now, before there is anything to tune against).

**Exit criterion:** `slice_manifest.json` exists, is committed, and a second team member can reproduce the identical slice from it on their machine — `python scripts/01_freeze_slice.py --verify artifacts/slice_manifest.json` passes on their box.

**Risk note:** individual MSMARCO-XI validation files are large (Telugu is 474 MB, Hindi 440 MB; train files are ~3.7 GB each and are not needed). Do not download all fourteen languages. English comes free inside every language file, so English + Hindi is a single download.

---

## Phase 2: Thin vertical slice, text only
**Day: 15 August | Owner: 2 people | Duration: 1 day**

The goal is one working query path, end to end, with no voice, one chunking strategy, no guardrails, no reranker. Prove the shape before adding depth.

**Tasks**
- `scripts/03_export_onnx.py`: export `multilingual-e5-small` to ONNX, quantize int8, verify output parity against the PyTorch model within tolerance
- `retrieval/embedder.py`: ONNX session, correct `query: ` / `passage: ` prefixes, batch encode
- `chunking/c1_fixed.py`: 256 tokens, 40 overlap. The baseline only.
- `scripts/02_build_indexes.py`: build the C1 dense index with hnswlib, serialize to disk
- `retrieval/dense.py`: load, mmap, search
- `answering/extractive.py`: naive version, return the top passage verbatim
- `harness/pipeline.py`: Stage protocol, Pipeline runner, Context model, span emission
- `main.py`: `POST /v1/answer`, lifespan warmup, health check gated on index load
- Bench it. Record the number.

**Exit criterion:** `curl -X POST localhost:8000/v1/answer -d '{"query":"..."}'` returns a cited passage, and `scripts/04_bench_latency.py` reports a real P50 for this path over 250 queries.

**This is the most important checkpoint in the project.** If the P50 here is already over 150ms, the architecture is wrong and there are seven days to fix it. If it is under 40ms, there is headroom for the reranker and guardrails.

---

## Phase 3: Chunking depth
**Day: 16 to 17 August | Owner: 2 people, parallelizable | Duration: 1.5 days**

Requirement 2 is a scoring category. Eight strategies, each in its own file, each behind the same protocol.

**Tasks, splittable across two people**

Person A:
- `c2_sentence_window.py`: sentence-level embedding, window expansion at retrieval
- `c3_semantic.py`: cosine-distance breakpoint splitting at the 92nd percentile
- `c6_hierarchical.py`: parent-child, child embedded, parent returned
- `c8_late.py`: full-passage encode, per-span mean pooling

Person B:
- `c4_proposition.py`: offline LLM decomposition into atomic facts. Slow, run it overnight on the slice.
- `c5_metadata.py`: metadata-aware boundaries, payload filtering, pre-filtered search
- `c7_doc2query.py`: index the paired MS MARCO query as an extra vector pointing at its passage
- `retrieval/lexical.py`: bm25s index with Indic-aware tokenization
- `retrieval/fusion.py`: RRF, k=60

Then together:
- `scripts/02_build_indexes.py` extended to build all eight into separate namespaces
- `scripts/05_eval_retrieval.py`: Recall@10, MRR@10, nDCG@10, index build time, index size per strategy
- Results table committed to `bench/results/`

**Exit criterion:** eight indexes built, one results table comparing all eight on four metrics, and a documented decision on which is the default with the reasoning recorded in `Memory.md`.

**Note on C7:** this strategy is likely to win, because MSMARCO-XI ships query-passage pairs and indexing the query text directly closes the vocabulary gap between how people ask and how passages are written. Expect it to be the default. Verify rather than assume.

---

## Phase 4: Voice input
**Day: 17 to 18 August | Owner: 2 people | Duration: 1 day**

**Tasks**
- `apps/web`: Next.js scaffold, single page, design tokens from `Design.md` wired in
- `lib/audio/recorder.ts`: `getUserMedia`, AudioWorklet, downsample to 16kHz mono PCM16. This is fiddlier than it looks; budget real time for it.
- `services/stt_gateway`: FastAPI WebSocket endpoint, relays frames to Sarvam `saaras:v3-realtime`, holds the key
- Sarvam config: `stream_type=vad`, `language_code=auto`, VAD tuned via `silence_duration_ms`
- Emit partials and finals back to the browser as typed JSON
- `MicOrb.tsx` and `TranscriptStream.tsx`: push to talk, live partial rendering
- Text input fallback (F16) wired to the same `/v1/answer` endpoint

**Exit criterion:** speak a question into the browser, see partial transcripts appear live, see a final transcript, see a retrieved answer. End to end, one machine, no polish.

**Gotchas to expect**
- The realtime endpoint accepts raw PCM only. Sending WebM or Opus fails silently or with an unhelpful 400.
- PCM must be 16kHz. The browser's native sample rate is usually 48kHz. Resample properly, do not just drop samples.
- Odia's language code differs between the legacy WS (`od-IN`) and the realtime endpoint (`or-IN`). Only matters if Odia is in the slice.

---

## Phase 5: Reranking, routing and calibration
**Day: 18 to 19 August | Owner: 2 people | Duration: 1 day**

This phase is where the 200ms target is actually won or lost.

**Tasks**
- Export and quantize `ms-marco-MiniLM-L-6-v2` to ONNX int8
- `retrieval/rerank.py`: rerank exactly top-20. Bench at 10, 20, 50 and pick on the accuracy-vs-latency curve.
- `answering/router.py`: confidence thresholds driving extractive / generative / abstain
- **Calibrate the thresholds** against a labelled dev slice. Plot rerank top-1 score against answer correctness and pick the thresholds off the curve. Do not guess them.
- `answering/generative.py`: Groq client, streaming, temperature 0, 160 max tokens, strict grounding system prompt
- `harness/policies.py`: retry, timeout, circuit breaker, and the **remaining-budget counter** that skips stages which cannot fit
- Tune `ef_search` on the HNSW index against the latency budget
- Full re-bench: P50/P70/P90/P99/P100 for the fast path, the fallback path, and the blended distribution

**Exit criterion:** published P50/P70/P100 for the measured band, under 200ms at P50, with the fallback-path numbers published alongside. `Latency.md` filled in with real numbers replacing the estimates.

---

## Phase 6: Guardrails
**Day: 19 to 20 August | Owner: 2 people | Duration: 1 day**

**Tasks**
- `guardrails/input_guard.py`: language ID, length bounds, toxicity classifier, prompt-injection detection, PII redaction for logs
- `guardrails/retrieval_guard.py`: confidence floor, score-gap ambiguity check, language mismatch flag
- Generation guard: grounding system prompt, schema-enforced output with citation indices, one repair retry
- `guardrails/output_guard.py`: groundedness scoring via overlap plus a cheap NLI entailment check, citation validity
- `guardrails/policies.yaml`: every threshold externalized with a comment saying what calibrated it
- `bench/adversarial.jsonl`: build the eval set. Minimum 60 cases across five categories: off-topic, unsafe, prompt-injection, unanswerable-from-corpus, ambiguous.
- `scripts/06_eval_guardrails.py`: abstention precision and recall per category
- `AbstentionPanel.tsx`: render the typed refusal reason in the UI

**Exit criterion:** the adversarial eval runs and reports per-category abstention precision and recall. Three specific demo cases work reliably and are scripted for Video 2: one off-topic, one unsafe, one low-confidence.

**Do not skip the eval set.** "We added guardrails" with no measurement is worth much less than "our abstention recall on off-topic queries is 0.91 across 60 adversarial cases."

---

## Phase 7: Deploy and harden
**Day: 20 August | Owner: 2 people | Duration: 1 day**

Deploy early enough that deployment problems are not deadline problems. This is deliberately not the last phase.

**Tasks**
- Dockerfile for `rag_core` with indexes baked into the image or pulled at boot
- Deploy `rag_core` and `stt_gateway` to Fly.io or Render, **Mumbai region, always-on**
- Deploy frontend to Vercel, India region
- Health check gated on index load plus one warmup query through the full pipeline
- Keepalive ping every 60 seconds
- Re-bench **against the deployed URL**, not localhost. Network latency is real and it will move the numbers.
- CORS, rate limiting per IP, request size caps
- Secret scan across full git history before making the repo public
- Error boundaries in the frontend so a backend hiccup does not white-screen the demo

**Exit criterion:** a teammate on a different network, on mobile data, opens the live URL and completes a voice query successfully. Benchmarks re-run against production and committed.

---

## Phase 8: Demo surfaces and polish
**Day: 21 August | Owner: whole team | Duration: 1 day**

Everything in this phase exists to make requirements 2, 3, 4 and 6 *visible* in a two-minute video. Work already done that a judge cannot see scores zero.

**Tasks**
- `LatencyWaterfall.tsx`: per-stage timing bars from the returned trace. This is the money shot of Video 2.
- `StrategyToggle.tsx`: switch chunking strategy live, watch retrieval change
- `CitationChip.tsx`: click a citation, see the source passage
- Failure injection mode: a query param that forces an LLM 429 so the demo can show the circuit breaker and fallback working live
- Design pass against `Design.md`: typography, motion, the mic orb states
- Mobile layout check
- README: architecture diagram, latency table, reproduction steps, the honest measurement-boundary statement
- Populate `Latency.md` and `Memory.md` with final numbers

**Exit criterion:** a full dry run of Video 2's script, performed live, with no errors and nothing needing explanation that the UI does not already show.

**HARD: code freeze at 11:59 PM on 21 August.**

---

## Phase 9: Videos, posting, submission
**Day: 22 August | Owner: whole team | Duration: 1 day**

No code. See `Submission.md` for the full checklist.

**Tasks**
- Video 1: 90 seconds, process not product. Screen recordings of the phase board, the benchmark runs, the chunking comparison table, whiteboard moments, commits. Show how the team worked.
- Video 2: end-to-end demo. Script in `Submission.md`.
- Both videos exported at 1080x1080 (Instagram) and 16:9 (X, LinkedIn)
- **Every member** posts both videos to Instagram, X and LinkedIn with `#RAGInGoa`
- At least one Instagram account confirmed public
- Post links collected in `Submission.md`
- Final verification pass by a second person
- Form submitted once: https://forms.gle/MNvCjcv23Hn2Eeu58

**Exit criterion:** form submitted, all post links recorded, live URL verified working from a fresh browser one final time.

---

## Slack and contingency

Two half-days of slack are built in (Phases 0 and 1 are half-days). If a phase overruns, take the slack. If slack is exhausted, cut in this order:

1. F17 to F20 (nice-to-haves) go first
2. Chunking strategies C8 and C6 go second, leaving six strategies which is still comfortably "vast"
3. F13 strategy toggle goes third, replaced by a static comparison table in the README
4. Never cut: the guardrail eval set, the latency benchmark, the deployment, the videos, the posting

The last four are scored requirements. The rest is depth.
