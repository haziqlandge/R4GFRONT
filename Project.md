# Project.md

**Team:** OK4T
**Event:** HH Goa 2026, Shortlisting Task 2
**Deadline:** 22 August 2026, 11:59 PM IST
**Hashtag:** `#RAGInGoa`

---

## 1. What we are building

A voice-first Retrieval-Augmented Generation system. A user speaks a question into a browser. The audio is transcribed, the transcript is used to retrieve grounded passages from the AI4Bharat MSMARCO-XI corpus, and a grounded answer is returned. The system refuses to answer when it cannot ground the answer in retrieved evidence.

The product name is **Shruti** (Sanskrit: "that which is heard"). Working title, change freely.

Pipeline shape mandated by the brief:

```
Voice input -> Speech-to-text -> Chunking / Retrieval (vector DB) -> Answer generation
```

Our implementation extends this with a routing layer and a guardrail layer:

```
Voice -> STT -> Input guard -> Query encode -> Hybrid retrieve -> Fuse -> Rerank
      -> Confidence route -> [Extractive answer | LLM answer | Abstain] -> Output guard -> Response
```

---

## 2. The real constraint

The brief asks for the full process, "chunking + vector DB retrieval + everything through to final output", to complete in under 200ms.

This is the hardest requirement in the task and it is the one that should drive every architectural decision. Our measured reality:

| Stage | Realistic cost |
|---|---|
| Query embedding (ONNX int8, CPU, short query) | 5 to 15 ms |
| HNSW dense search, ~500k vectors, top-50 | 2 to 8 ms |
| BM25 lexical search (bm25s, in-memory) | 1 to 5 ms |
| Reciprocal rank fusion | < 1 ms |
| Cross-encoder rerank, top-20, ONNX int8 | 20 to 45 ms |
| Extractive span selection | 2 to 6 ms |
| Groundedness check | 5 to 15 ms |
| **Subtotal, no LLM** | **~40 to 90 ms** |
| Hosted LLM time-to-first-token (Groq, best case) | 100 to 400 ms |
| Sarvam Saaras v3 STT, streaming, after speech ends | ~150 ms and up |

The conclusion is unavoidable and we state it openly rather than hiding it: **a single pipeline that includes both a network STT call and a network LLM call cannot reliably finish in 200ms.** Any team claiming otherwise is either measuring only part of the pipeline or reporting a single lucky run.

Our answer is a **dual-path design**. See `Latency.md` for the full budget and the measurement contract.

---

## 3. Target users

**Primary: the judging panel.** Be honest about this. The system is evaluated, not adopted. Every feature should be legible to someone watching a two minute demo. If a judge cannot see it, it does not count.

**Stated user persona: the voice-first Indian information seeker.** MSMARCO-XI is an AI4Bharat multilingual corpus covering fourteen languages: Assamese, Bengali, Gujarati, Hindi, Kannada, Malayalam, Marathi, Nepali, Odia, Punjabi, Sanskrit, Tamil, Telugu and Urdu. Every row also carries the original English, so English is a fifteenth language for free. The natural user is someone who would rather speak a question in Hindi or Tamil than type it in English. This is the real, defensible framing and it is why Sarvam is the correct STT choice over ElevenLabs.

**Secondary: developers evaluating latency-critical RAG.** The harness, the latency report and the guardrail eval set are reusable artifacts. This is what makes the repo worth starring after the hackathon ends.

---

## 4. Feature list

### 4.1 Must ship (task fails without these)

| # | Feature | Maps to brief requirement |
|---|---|---|
| F1 | Browser mic capture, push-to-talk and VAD auto-stop | Voice input |
| F2 | Sarvam Saaras v3 streaming STT over WebSocket | Req 1, Speech-to-text |
| F3 | Eight distinct chunking strategies, indexed and comparable | Req 2, Chunking |
| F4 | Hybrid retrieval, dense HNSW plus BM25, RRF fused | Pipeline |
| F5 | Dual-path answering, extractive fast path plus LLM fallback | Req 3, Latency |
| F6 | Per-stage latency instrumentation with trace IDs | Req 4, Analytics |
| F7 | P50 / P70 / P100 report over 250+ queries, published | Req 4, Analytics |
| F8 | Typed orchestration harness with retries and fallbacks | Req 5, Harness |
| F9 | Four-layer guardrails with explicit abstention | Req 6, Guardrails |
| F10 | Live deployed URL | Submission |
| F11 | Public GitHub repo with README and reproduction steps | Submission |

### 4.2 Should ship (materially raises the score)

| # | Feature | Why |
|---|---|---|
| F12 | Live latency waterfall in the UI, per stage, per query | Makes req 3 and 4 visible in the demo video without narration |
| F13 | Chunking strategy A/B toggle in the UI | Makes req 2 visible instead of buried in code |
| F14 | Citation chips linking to the source passage | Groundedness made visible |
| F15 | "Why I did not answer" panel on abstention | Makes req 6 visible, this is the single most demo-able guardrail |
| F16 | Text input fallback alongside voice | Judges may demo on a machine with no mic permission |

### 4.3 Nice to have (only if Phase 6 finishes early)

| # | Feature |
|---|---|
| F17 | TTS response via Sarvam Bulbul, closing the voice loop |
| F18 | Retrieval quality metrics (Recall@k, MRR@10, nDCG@10) per chunking strategy |
| F19 | Multilingual query in one language, retrieval across all thirteen |
| F20 | Warm-cache mode with a semantic query cache for repeat questions |

### 4.4 Explicitly out of scope

- User accounts, login, persistence of history
- Multi-turn conversation memory
- Fine-tuning any model
- Mobile native apps
- Ingesting any corpus other than MSMARCO-XI

---

## 5. Success criteria

The build is done when all of the following are true.

1. A cold visitor can open the live URL, grant mic access, speak a question, and see a cited answer with no login.
2. The published P50 for the measured band is under 200ms across at least 250 distinct queries.
3. The full wall clock including STT and LLM is published alongside it, unhidden.
4. At least eight chunking strategies are implemented, indexed, and individually selectable.
5. The system visibly abstains on at least three demo cases: an off-topic query, an unsafe query, and a query whose retrieval confidence falls below threshold.
6. The harness recovers visibly from at least one injected failure (STT timeout or LLM 429) during the demo.
7. Both videos are uploaded to Instagram, X and LinkedIn by every team member with `#RAGInGoa`, and at least one Instagram account is public.

---

## 6. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| 200ms is unachievable end to end | High | Dual-path design plus an explicit, honest measurement contract. Documented in `Latency.md`. Do not fudge. |
| MSMARCO-XI is large (the Telugu validation split alone is 474 MB) and blows the RAM budget | High | Subset to a fixed corpus slice in Phase 1, freeze it, document the slice. Do not index the whole thing. |
| Sarvam free credits exhaust mid-build | Medium | Cache all demo audio transcriptions. Keep a Web Speech API fallback path behind a flag. |
| Cold-start latency on serverless destroys P100 | High | Do not deploy the retrieval service on serverless. Long-running container, warm index in RAM, health-check keepalive. |
| Deploy region far from the user adds 100ms+ of pure network | High | Deploy in an India region. Colocate the frontend edge and the retrieval service. |
| Team forgets the promotion requirement | Medium | It is a mandatory requirement with a per-member obligation. Tracked in `Submission.md` with a checklist per person. |
| No resubmissions allowed | High | Freeze code 24 hours before deadline. Final 24 hours are for video, deploy verification and posting only. |

---

## 7. Non-negotiables

- **No resubmissions.** Submit once, when final.
- **Every team member posts on all three platforms.** Not one shared post.
- **At least one Instagram account must be public.**
- Video 1 is 90 seconds and is about *process*, not product.
- Video 2 is a working end-to-end demo.
