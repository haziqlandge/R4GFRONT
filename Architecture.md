# Architecture.md

Team OK4T, HH Goa 2026 Task 2.

---

## 1. System overview

Three deployable units. Keep them separate so the latency-critical one can be tuned independently.

```
┌────────────────────────────────────────────────────────────┐
│  BROWSER (Next.js, Vercel edge, ap-south)                   │
│  mic capture -> PCM16 16kHz -> WS -> render answer + trace  │
└───────────────┬─────────────────────────┬──────────────────┘
                │ WSS (audio frames)      │ HTTPS (query)
                ▼                         ▼
┌───────────────────────────┐  ┌────────────────────────────┐
│  STT GATEWAY (FastAPI)    │  │  RAG CORE (FastAPI)         │
│  relays to Sarvam WS      │  │  ── the 200ms budget lives  │
│  holds the API key        │  │     entirely inside here    │
│  emits partial+final      │  │                             │
└───────────┬───────────────┘  │  in-process, zero network:  │
            │                  │   embedder (ONNX int8)      │
            ▼                  │   HNSW dense index (RAM)    │
      Sarvam Saaras v3         │   BM25 lexical index (RAM)  │
      saaras:v3-realtime       │   cross-encoder reranker    │
                               │   extractive answerer       │
                               │   groundedness scorer       │
                               │                             │
                               │  network, only on fallback: │
                               │   Groq LLM (llama-3.3-70b)  │
                               └────────────────────────────┘
```

**The single most important architectural rule: the RAG Core makes zero network calls on the fast path.** Embedder, both indexes, reranker and answerer all live in the same process, in RAM. Every network hop is 20 to 80ms of pure latency you cannot get back.

---

## 2. Request flow, annotated with budget

### 2.1 Voice path (full)

| # | Step | Where | Budget | Counted in 200ms? |
|---|---|---|---|---|
| 1 | Mic capture, 16kHz PCM16 frames | Browser | continuous | No |
| 2 | Frames streamed to STT gateway | WSS | continuous | No |
| 3 | Sarvam emits partial transcripts | Sarvam | continuous | No |
| 4 | VAD fires `speech_end`, final transcript | Sarvam | ~150ms | No, reported separately |
| 5 | **Transcript enters RAG Core. Timer starts.** | RAG Core | t=0 | — |
| 6 | Input guardrail chain | in-proc | 3 to 8 ms | Yes |
| 7 | Query embed, ONNX int8 | in-proc | 5 to 15 ms | Yes |
| 8 | Dense HNSW search, top-50 | in-proc | 2 to 8 ms | Yes |
| 9 | BM25 search, top-50 | in-proc | 1 to 5 ms | Yes |
| 10 | Reciprocal rank fusion | in-proc | < 1 ms | Yes |
| 11 | Cross-encoder rerank, top-20 to top-5 | in-proc | 20 to 45 ms | Yes |
| 12 | Confidence routing decision | in-proc | < 1 ms | Yes |
| 13a | **Fast path:** extractive span answer | in-proc | 2 to 6 ms | Yes |
| 13b | **Fallback path:** Groq generation | network | 250 to 700 ms | Yes, and it blows the budget. Reported separately. |
| 14 | Output guardrail, groundedness | in-proc | 5 to 15 ms | Yes |
| 15 | **Timer stops.** Response serialized | RAG Core | 1 to 3 ms | Yes |

**Fast path total: 40 to 100 ms.** Comfortably inside 200ms.
**Fallback path total: 300 to 800 ms.** Outside 200ms, and we say so.

### 2.2 Prefetch optimization

Sarvam emits partial transcripts as the user speaks. We exploit this: when a partial transcript stabilizes (unchanged for 250ms) and is at least four tokens, we **speculatively run steps 6 through 11** against it. If the final transcript matches the partial, the results are already computed and step 5 to 12 costs ~0ms. This is the single largest perceived-latency win available and it is free, because the compute happens while the user is still talking.

Speculative results are discarded if the final transcript differs beyond a normalized edit-distance threshold of 0.15.

---

## 3. Component decisions and rationale

### 3.1 Speech to text: Sarvam, not ElevenLabs

The brief allows either. Sarvam is correct here for three reasons.

1. The dataset is AI4Bharat MSMARCO-XI, fourteen Indian languages (`as bn gu hi kn ml mr ne or pa sa ta te ur` — Sanskrit included, which every earlier draft of these docs omitted). Sarvam's Saaras v3 covers 22 Indian languages plus English and is trained on Indian audio. Corpus and STT are matched.
2. Sarvam publishes sub-150ms time-to-first-token in fast mode and supports true partial transcripts via `saaras:v3-realtime`, which is what makes the prefetch optimization in 2.2 possible.
3. Code-mixed input (Hinglish, Tanglish) is explicitly supported, which is how people actually speak.

Endpoint: `saaras:v3-realtime` WebSocket, `stream_type=vad`, `language_code=auto`. Auto-detection returns a `language` field on partials and a `language_confidence` on finals, both of which we log and use as retrieval metadata.

Note: raw PCM only on the realtime endpoint, 16kHz, mono. The browser must downsample. Do not send WebM or Opus to this endpoint.

### 3.2 Embeddings: multilingual, small, quantized, local

Model: `intfloat/multilingual-e5-small` (384 dims, 12 layers), exported to ONNX and quantized to int8 via `onnxruntime`.

Why:
- Multilingual is mandatory given the corpus.
- 384 dims keeps the index small and search fast.
- int8 ONNX on CPU embeds a short query in single-digit milliseconds. A PyTorch fp32 forward pass would cost 40 to 80ms and eat half the budget.
- `e5` requires the `query: ` and `passage: ` prefixes. Do not forget these; omitting them silently degrades recall by a large margin.

Do not use a hosted embedding API. One network round trip to OpenAI or Cohere is 80 to 200ms and the budget is gone.

### 3.3 Vector index: hnswlib in-process, not a hosted DB

Use `hnswlib` directly, or `qdrant` in embedded/local mode. Not Pinecone, not hosted Qdrant, not hosted Weaviate. Every hosted vector DB adds a network round trip.

Parameters: `M=32`, `ef_construction=200`, `ef_search=64`. Tune `ef_search` against the latency budget in Phase 5. Lower `ef_search` trades recall for speed and is the primary latency dial.

Index is built offline, serialized to disk, and `mmap`ed at process start. Startup loads it into RAM before the health check passes.

### 3.4 Lexical index: bm25s

`bm25s` is a fast pure-numpy/scipy BM25 implementation. Sub-5ms over hundreds of thousands of documents. Needed because dense retrieval alone is weak on rare entities, numbers, and proper nouns, which MS MARCO queries are full of.

Tokenization must be language-aware. Use `indic-nlp-library` tokenizers for Indic scripts; whitespace tokenization destroys BM25 quality on Devanagari and Tamil.

### 3.5 Fusion: Reciprocal Rank Fusion

`score(d) = sum over retrievers of 1 / (k + rank(d))`, with `k=60`.

RRF over score normalization, because dense cosine scores and BM25 scores live on incomparable scales and normalizing them well requires per-query calibration we do not have time for. RRF is rank-based and needs no calibration.

### 3.6 Reranker: cross-encoder, ONNX int8, top-20 only

Model: `cross-encoder/ms-marco-MiniLM-L-6-v2`, ONNX int8.

Rerank exactly 20 candidates. This is a tuned number: 20 costs ~25 to 45ms on CPU and captures nearly all the recall benefit. Reranking 50 doubles the cost for a marginal gain. Reranking 10 saves 15ms and loses meaningful accuracy.

The reranker score is also the **confidence signal** that drives routing and abstention. This is the key structural insight in the design: one mechanism serves latency routing (requirement 3) and guardrails (requirement 6) simultaneously.

### 3.7 Answer generation: dual path

```
rerank_top1_score = s

if s >= 0.72:          -> EXTRACTIVE   (no LLM, ~4ms)
elif s >= 0.35:        -> LLM FALLBACK (Groq, ~300ms)
else:                  -> ABSTAIN      (~0ms)
```

Thresholds are calibrated in Phase 5 against a labelled dev slice, not guessed. The numbers above are starting points.

**Extractive path.** MS MARCO passages are short and answer-bearing by construction; the dataset ships with `is_selected` flags marking answer-bearing passages. When the reranker is confident, the answer is a span inside the top passage. We select it with a lightweight span scorer (sentence-level cosine against the query embedding, already computed) and return the best one to three sentences verbatim with a citation. Zero hallucination risk, zero LLM cost, ~4ms.

**LLM fallback.** Groq, `llama-3.3-70b-versatile` or `llama-3.1-8b-instant`. Groq is chosen for time-to-first-token, which is where the entire latency story lives; independent benchmarks put Groq's TTFT and throughput well ahead of other hosted providers. Streamed, so the user sees tokens quickly even though total completion exceeds the budget. Temperature 0. Hard-capped at 160 output tokens.

**Abstain.** Return a structured refusal with the reason and the top scores, rendered in the UI as the "why I did not answer" panel.

### 3.8 The harness

Not a wrapper around a prompt. A typed pipeline runner. See section 6.

---

## 4. Chunking architecture

Requirement 2 says the strategy must be "vast" and explicitly rejects a single naive fixed-size approach. We implement eight strategies as pluggable `Chunker` implementations sharing one interface, index each into its own namespace, and expose a runtime toggle.

| # | Strategy | Description | Why it earns its place |
|---|---|---|---|
| C1 | Fixed-size + overlap | 96 tokens, 24 overlap | Baseline. Every comparison needs one. **Not 256/40** — see the passage-length note below; at 256 tokens this strategy is a no-op on this corpus. |
| C2 | Sentence-window | Embed a single sentence, return a window of n=2 neighbours at retrieval | Precise matching, wide context. Classic small-to-big. |
| C3 | Semantic breakpoint | Split where consecutive-sentence cosine distance exceeds the 92nd percentile | Chunks follow meaning, not character count. |
| C4 | Proposition / atomic fact | Offline LLM pass decomposes each passage into standalone factual assertions | Highest precision retrieval unit. Expensive offline, free at query time. |
| C5 | Metadata-aware | Boundaries and payload filters on `language`, `script`, `query_type`, `is_selected_any` and passage `position`; metadata written into the payload for pre-filtered search | Enables pre-filtered search, which is faster and more accurate than post-filtering. `query_type` is the strongest signal available: NUMERIC queries want different passages than DESCRIPTION ones, and the slice is 51% DESCRIPTION / 24% NUMERIC. |
| C6 | Hierarchical parent-child | Passage-level chunks embedded, **the query_id passage group returned as the parent**. Two-level tree. | Retrieval precision with generation context. The parent must sit *above* the passage, not below it — passages here are already short (see below). |
| C7 | Doc2query / query-aligned | MSMARCO-XI ships query-passage pairs. Index the *paired query* as an additional vector pointing at the passage. | Nearly free given the dataset shape, and it directly closes the vocabulary gap between spoken questions and written passages. This is the highest-leverage strategy for this specific corpus. |
| C8 | Late chunking | Embed the full passage with a long-context encoder, then mean-pool per chunk span so each chunk vector carries whole-passage context | Solves the context-loss problem of naive chunking. |

Each strategy is evaluated in Phase 5 on Recall@10, MRR@10, nDCG@10 and index build time. Ship the winner as default; keep all eight selectable so the demo can show the comparison.

### 4.1 Passage length: measured, and it changes the chunking problem

Measured on the frozen slice (Phase 1, 295,890 passages):

| | p50 | p90 | p99 | max |
|---|---|---|---|---|
| English, words | 48 | 76 | 115 | **205** |
| Hindi, words | 55 | 85 | 133 | 4,093 (outlier) |

**Zero English passages exceed 256 words.** A 256-token fixed-size chunker would emit exactly one chunk per passage and do nothing at all. The naive strategy the brief warns against is not merely naive on this corpus, it is inert.

This reframes requirement 2. The interesting axis here is not *splitting long documents* — there are none — but **choosing and composing the retrieval unit**:

- **Sub-passage units** (C1 at 96/24, C2 sentence-window, C3 semantic breakpoint, C4 propositions, C8 late chunking) test whether a unit smaller than a passage retrieves better.
- **Supra-passage units** (C6 hierarchical, grouping the ~10 passages sharing a `query_id` into a parent document) test whether a unit larger than a passage generates better.
- **Alternative-representation units** (C7 doc2query, C5 metadata-filtered) change what is indexed rather than how it is cut.

Saying this explicitly is a stronger answer to requirement 2 than eight splitters would be, because it shows the strategy was chosen against measured corpus properties rather than applied by reflex.

**Hindi outlier guard.** The longest Hindi passage is 4,093 words against a 205-word English source — a translation-model repetition loop. Any strategy that embeds whole passages must cap input length or a handful of degenerate rows will dominate index build time. Cap at the 99.5th percentile and log the truncations.

**Corpus slice, frozen in Phase 1.** Do not index all fourteen languages at full size; the Telugu validation parquet alone is 474 MB. The frozen slice is English + Hindi, 15,000 queries, 295,890 passages, seed 20260814, recorded in `artifacts/slice_manifest.json` and reproducible from it. Reproducibility matters more than corpus size for this task.

---

### 4.2 Internal data model

Frozen in Phase 1 by `scripts/01_freeze_slice.py`. Written to `artifacts/passages.parquet` and `artifacts/queries.parquet`.

```python
class Passage(BaseModel):          # pydantic v2, frozen
    passage_id: str                # f"{query_id}:{position}:{lang}"
    text: str                      # verbatim; the extractive path returns spans of it
    language: Literal["en", "hi"]
    script: Literal["Latn", "Deva"]
    query_id: int                  # first occurrence after dedup
    position: int                  # index within the source passage list
    parallel_id: str               # f"{query_id}:{position}" — links the en/hi twins
    text_sha1: str                 # dedup key
    is_selected_any: bool          # was this text ever answer-bearing, anywhere in the slice
```

Three decisions here are load-bearing and are not obvious:

**There is no `url` field.** MSMARCO-XI does not carry one, unlike the original MS MARCO. C5 is defined against the metadata that exists (§4).

**Dedup is keyed on the English text, for both languages.** MS MARCO reuses passages across queries. Deduplicating each language independently would collapse different row sets and break the en/hi pairing. Keying both on the English sha1 keeps every parallel pair intact — 1,857 duplicate pairs were dropped and `parallel_id` remains a perfect bijection.

**`is_selected` is not on the passage.** It is a property of a *(query, passage)* pair, and after dedup a passage's owning query is arbitrary. Ground truth lives on the query instead, as `gold_en_ids` / `gold_hi_ids` — which is the shape Recall@10, MRR@10 and nDCG@10 actually consume in Phase 3. The passage keeps `is_selected_any` only as a corpus-level signal for C5.

`parallel_id` is what makes the cross-lingual claim measurable rather than anecdotal: asking in Hindi and citing the English twin is a checkable retrieval event, not a demo anecdote.

---

## 5. Folder and file structure

```
ok4t-voice-rag/
├── README.md
├── Project.md
├── Architecture.md
├── Rules.md
├── Phases.md
├── Design.md
├── Latency.md
├── Submission.md
├── Memory.md
├── .env.example
├── requirements.txt                   # runtime deps for services/
├── requirements-dev.txt               # offline build + measurement tooling
├── pytest.ini
├── deploy/gcp.md                      # host setup, see Memory.md R3
├── docker-compose.yml
│
├── apps/
│   └── web/                          # Next.js 15, App Router
│       ├── app/
│       │   ├── layout.tsx
│       │   ├── page.tsx              # the single screen
│       │   └── api/health/route.ts
│       ├── components/
│       │   ├── MicOrb.tsx            # the central affordance
│       │   ├── TranscriptStream.tsx  # partials, live
│       │   ├── AnswerCard.tsx
│       │   ├── CitationChip.tsx
│       │   ├── LatencyWaterfall.tsx  # F12, the demo money shot
│       │   ├── AbstentionPanel.tsx   # F15
│       │   └── StrategyToggle.tsx    # F13
│       ├── lib/
│       │   ├── audio/recorder.ts     # getUserMedia -> PCM16 16k
│       │   ├── audio/resampler.ts    # AudioWorklet downsample
│       │   ├── ws/sttClient.ts
│       │   └── api/ragClient.ts
│       └── styles/tokens.css         # see Design.md
│
├── services/
│   ├── stt_gateway/
│   │   ├── main.py                   # FastAPI, WS relay
│   │   ├── sarvam.py                 # Saaras v3 realtime client
│   │   ├── vad.py                    # client-hint VAD reconciliation
│   │   └── config.py
│   │
│   └── rag_core/
│       ├── main.py                   # FastAPI app, lifespan warmup
│       ├── harness/
│       │   ├── pipeline.py           # Stage, Pipeline, StageResult
│       │   ├── stages.py             # concrete stage impls
│       │   ├── policies.py           # retry, timeout, circuit breaker
│       │   ├── trace.py              # trace ids, span timing
│       │   └── errors.py             # typed error hierarchy
│       ├── retrieval/
│       │   ├── embedder.py           # ONNX int8 e5-small
│       │   ├── dense.py              # hnswlib wrapper
│       │   ├── lexical.py            # bm25s wrapper
│       │   ├── fusion.py             # RRF
│       │   └── rerank.py             # ONNX cross-encoder
│       ├── chunking/
│       │   ├── base.py               # Chunker protocol
│       │   ├── c1_fixed.py
│       │   ├── c2_sentence_window.py
│       │   ├── c3_semantic.py
│       │   ├── c4_proposition.py
│       │   ├── c5_metadata.py
│       │   ├── c6_hierarchical.py
│       │   ├── c7_doc2query.py
│       │   └── c8_late.py
│       ├── answering/
│       │   ├── router.py             # confidence -> path
│       │   ├── extractive.py         # span selection
│       │   ├── generative.py         # Groq client, streaming
│       │   └── schemas.py            # pydantic I/O contracts
│       ├── guardrails/
│       │   ├── input_guard.py        # lang, toxicity, injection, PII
│       │   ├── retrieval_guard.py    # OOD / confidence floor
│       │   ├── output_guard.py       # groundedness, citation check
│       │   └── policies.yaml         # thresholds, externalized
│       └── config.py
│
├── scripts/
│   ├── 00_download_dataset.py
│   ├── 01_freeze_slice.py            # writes slice manifest + seed
│   ├── 02_build_indexes.py           # all 8 strategies
│   ├── 03_export_onnx.py             # fetch ONNX from the Hub + parity gate
│   ├── 04_bench_latency.py           # P50/P70/P100 harness
│   ├── 05_eval_retrieval.py          # Recall/MRR/nDCG per strategy
│   └── 06_eval_guardrails.py         # abstention precision/recall
│
├── bench/
│   ├── queries_250.jsonl             # the fixed benchmark set
│   ├── adversarial.jsonl             # guardrail eval set
│   └── results/                      # committed, dated, immutable
│
├── artifacts/                        # gitignored, built locally
│   ├── indexes/
│   ├── onnx/
│   └── slice_manifest.json
│
└── tests/
    ├── test_harness.py
    ├── test_chunkers.py
    ├── test_guardrails.py
    └── test_latency_contract.py      # CI fails if P50 regresses
```

---

## 6. The harness

Requirement 5 asks for structured orchestration, not a raw prompt-in text-out call. Concretely, the harness is a typed pipeline with these properties.

### 6.1 Stage contract

Every stage implements:

```python
class Stage(Protocol):
    name: str
    timeout_ms: int
    retries: int
    fallback: Stage | None

    async def run(self, ctx: Context) -> StageResult: ...
```

`Context` is a frozen pydantic model threaded through the pipeline; each stage returns a new `Context` rather than mutating. This makes the whole run replayable from a trace log.

### 6.2 Guarantees the harness provides

| Property | Implementation |
|---|---|
| Per-stage timeout | `asyncio.wait_for`, budget declared per stage, exceeding it raises `StageTimeout` |
| Retry with backoff | Only on idempotent stages. Network stages get 2 retries, 50ms base, jittered. In-process stages get 0. |
| Circuit breaker | Groq client opens after 5 failures in 30s, routes everything to extractive or abstain until half-open probe succeeds |
| Graceful degradation chain | rerank fails -> use RRF order. Groq fails -> extractive. Extractive fails -> abstain. Never a 500 to the user. |
| Structured I/O | Pydantic models at every boundary. LLM output is parsed against a schema with one repair retry, then abandoned. |
| Tracing | Every request gets a trace id. Every stage emits start/end monotonic timestamps. The trace is returned in the response body and rendered in the UI. |
| Budget enforcement | Pipeline carries a remaining-budget counter. A stage that cannot fit in the remaining budget is skipped and its fallback runs. |

### 6.3 Why the budget counter matters

This is the part that turns the harness from decoration into the mechanism that makes the 200ms target real. If retrieval overran and only 30ms remain, the reranker is skipped automatically and RRF order is used. The system degrades on quality to protect latency, deliberately and observably. Every skipped stage appears in the returned trace, so the demo can show it happening.

---

## 7. Guardrail architecture

Four layers. Requirement 6 asks the system to know when *not* to answer.

### Layer 1: input guard (pre-retrieval, 3 to 8ms)
- Language identification; reject if not in the supported set
- Length bounds; reject empty or absurdly long transcripts
- Toxicity and unsafe-intent classification, small local ONNX classifier, not a network call
- Prompt-injection detection: pattern set plus a classifier, for "ignore previous instructions" style transcripts
- PII detection with regex plus `presidio`-style rules; redact before logging, never before retrieval

### Layer 2: retrieval guard (post-rerank, < 1ms)
- Confidence floor. If `rerank_top1 < 0.35`, abstain. This is the out-of-distribution and off-topic detector.
- Score-gap check. If top1 and top2 are within 0.02 and both are low, the retrieval is ambiguous. Abstain rather than pick arbitrarily.
- Language mismatch. If the query language and the retrieved passage languages disagree entirely, flag it.

### Layer 3: generation guard (LLM path only)
- System prompt constrains the model to the provided context and forbids outside knowledge
- Output is schema-parsed; a response with no citation index is rejected and retried once
- Max tokens hard cap

### Layer 4: output guard (post-generation, 5 to 15ms)
- Groundedness score: token and n-gram overlap between answer and cited passage, plus a cheap NLI entailment check on the top claim
- If groundedness falls below threshold, downgrade the response to the extractive answer or abstain
- Citation validity: every cited index must exist in the retrieved set

Abstention is never silent. It returns a typed reason (`OFF_TOPIC`, `LOW_CONFIDENCE`, `UNSAFE_INPUT`, `UNGROUNDED_OUTPUT`, `AMBIGUOUS_RETRIEVAL`) which the UI renders. This is what makes requirement 6 visible in a demo video.

---

## 8. Tech stack

| Layer | Choice | Locked reason |
|---|---|---|
| Frontend | Next.js 15, App Router, TypeScript | Team has shipped Vercel projects already |
| Styling | Tailwind + CSS custom properties | Token system in `Design.md` |
| Audio | Web Audio API + AudioWorklet | Only reliable way to get 16kHz PCM16 in-browser |
| Frontend host | Vercel, `bom1` / India region | Team has an account; edge close to judges |
| Backend | Python 3.11, FastAPI, uvicorn | Ecosystem for ONNX, hnswlib, bm25s |
| Backend host | **GCP Compute Engine `n2-standard-2`, `asia-south1` (Mumbai), always-on** | Serverless cold starts are fatal to P100; `e2` burst throttling is fatal to it too |
| STT | Sarvam `saaras:v3-realtime` | Requirement 1; Indic-native; partial transcripts |
| Embeddings | `intfloat/multilingual-e5-small`, ONNX int8 | Multilingual, small, fast, local |
| Dense index | `hnswlib`, in-process | No network hop |
| Lexical index | `bm25s` | Sub-5ms, pure numpy |
| Reranker | `ms-marco-MiniLM-L-6-v2`, ONNX int8 | Confidence signal + accuracy |
| LLM | Groq, `llama-3.3-70b-versatile` | Best-in-class TTFT among hosted providers |
| Validation | Pydantic v2 | Structured I/O for the harness |
| Tracing | OpenTelemetry SDK + custom span exporter | Feeds the UI waterfall |
| Benchmarking | Custom `04_bench_latency.py` + `numpy.percentile` | Must control the measurement boundary exactly |
| CI | GitHub Actions | Latency contract test on every push |

---

## 9. API contract

### `POST /v1/answer`

Request:
```json
{
  "query": "string",
  "language": "auto | hi-IN | ta-IN | ...",
  "strategy": "c1 | c2 | ... | c8 | auto",
  "trace": true
}
```

Response:
```json
{
  "trace_id": "uuid",
  "status": "ANSWERED | ABSTAINED",
  "path": "EXTRACTIVE | GENERATIVE | NONE",
  "answer": "string | null",
  "abstain_reason": "OFF_TOPIC | LOW_CONFIDENCE | UNSAFE_INPUT | UNGROUNDED_OUTPUT | AMBIGUOUS_RETRIEVAL | null",
  "citations": [
    { "passage_id": "string", "score": 0.0, "text": "string", "language": "string" }
  ],
  "confidence": { "rerank_top1": 0.0, "score_gap": 0.0, "groundedness": 0.0 },
  "trace": {
    "total_ms": 0.0,
    "budget_ms": 200,
    "stages": [
      { "name": "input_guard", "ms": 0.0, "status": "ok | skipped | fallback | failed" }
    ]
  }
}
```

The `trace` object is what `LatencyWaterfall.tsx` renders. It is the demo.

### `WS /v1/stt`

Client sends binary PCM16 16kHz mono frames. Server sends:
```json
{ "type": "partial", "text": "...", "language": "hi-IN" }
{ "type": "final", "text": "...", "language": "hi-IN", "language_confidence": 0.94 }
{ "type": "error", "code": "..." }
```

---

## 10. Deployment topology

```
Vercel (bom1)  ──────►  GCP Compute Engine (asia-south1 Mumbai, always-on)
   Next.js                 n2-standard-2, 2 vCPU, 8 GB, x86
                           ├─ stt_gateway  (thin, async, low CPU)
                           └─ rag_core     (warm indexes in RAM)
                                  │
                                  └──────► Groq API (fallback path only)
                                  └──────► Sarvam API (via gateway)
```

Host decided 15 Aug 2026. Full setup in `deploy/gcp.md`; the reasoning, including what was rejected, is reversal R3 in `Memory.md`. Short version: Mumbai region, **x86** (which retires the ARM risk an Oracle Ampere box would have carried), a VM rather than Cloud Run because a ~1.2 GB warm index cannot survive cold starts, and `n2` rather than `e2` because `e2` is burstable and burst throttling destroys P100.

Constraints:
- `rag_core` must be a long-running process, never serverless. Index load takes seconds; paying that per request destroys P100.
- Health check must not pass until indexes are loaded and one warmup query has run through the full pipeline. Cold ONNX sessions are slow on first inference.
- A keepalive pings `/health` every 60s to prevent host-level sleep.
- Region must be India. A US-East deployment adds 200ms of round trip on its own and the task becomes unwinnable.
