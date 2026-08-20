# Rules.md

Hard boundaries for team OK4T. These exist so that nobody has to relitigate a decision at 2 AM on 21 August.

A rule marked **HARD** is not negotiable without the whole team agreeing in writing. A rule marked **SOFT** is a strong default you may break with a one-line justification recorded in `Memory.md`.

---

## 1. The prime directive

**HARD.** Every architectural choice is judged against the 200ms budget first and everything else second. If a library, service or abstraction adds a network round trip to the hot path, it is rejected regardless of how nice it is.

**HARD.** No dishonest measurement. We publish the measurement boundary explicitly and we publish the numbers on both sides of it. A fabricated sub-200ms number that a judge can poke a hole in is worse than an honest 340ms.

---

## 2. Hot path rules

The "hot path" is everything inside `rag_core` between receiving a transcript and returning a response.

### 2.1 Forbidden on the hot path (HARD)

| Forbidden | Why |
|---|---|
| Any hosted embedding API (OpenAI, Cohere, Voyage, Jina cloud) | 80 to 200ms round trip. Budget gone. |
| Any hosted vector DB (Pinecone, hosted Qdrant, Weaviate Cloud, Chroma Cloud) | Same. Use in-process hnswlib. |
| LangChain, LlamaIndex, Haystack as the runtime orchestrator | Deep call stacks, hidden retries, hidden network calls, unpredictable overhead. You cannot budget what you cannot see. Read their source for ideas; do not import them into the hot path. |
| PyTorch inference at request time | 40 to 80ms per forward pass. Use ONNX Runtime with int8 quantization. |
| Synchronous blocking I/O inside an async handler | Blocks the event loop, destroys P100 under concurrency. |
| Disk reads at request time | Indexes are `mmap`ed and warm at startup, never lazily loaded. |
| `print()` or synchronous logging | Use async structured logging with a bounded queue. |
| Any LLM call on the extractive path | The extractive path is defined by not making one. |
| Regex compilation at request time | Precompile every pattern at module import. |
| JSON schema construction at request time | Build pydantic models once, reuse. |

### 2.2 Required on the hot path (HARD)

- Every stage declares a `timeout_ms` and it is enforced.
- Every stage emits a span into the trace.
- Every stage has a defined behaviour when it is skipped for budget reasons.
- All model sessions (ONNX) are created once at startup with a fixed thread count, never per request.
- `onnxruntime` intra-op threads set explicitly (start with 2), not left to default. The default oversubscribes and slows things down on small models.

---

## 3. Library allowlist

### 3.1 Use these (HARD, deviations need justification)

**Python, retrieval and inference**
- `onnxruntime` for all model inference
- `optimum[onnxruntime]` for export and int8 quantization
- `hnswlib` for the dense index
- `bm25s` for the lexical index
- `numpy`, `scipy` (already dependencies of the above)
- `indic-nlp-library` for Indic tokenization and normalization
- `pydantic` v2 for every I/O boundary
- `fastapi` + `uvicorn[standard]` for the services
- `httpx` for the Groq call, with an explicit connect timeout
- `websockets` for the Sarvam relay
- `orjson` for serialization, it is measurably faster than stdlib `json`
- `opentelemetry-sdk` for spans

**Python, offline only (build scripts, not the hot path)**
- `datasets`, `huggingface_hub` for corpus download
- `sentence-transformers` for offline index building and eval only
- `pandas`, `pyarrow` for parquet handling
- `ragas` or hand-rolled metrics for retrieval eval
- Any LLM SDK for the offline proposition-chunking pass (C4)

**Frontend**
- **Amended 20 Aug 2026: no framework at all.** The shipped site is static HTML,
  two stylesheets and ES modules under `frontends/`, served by
  `python -m http.server`. Next.js 15 + Tailwind were built first, in `apps/web`,
  and removed. The reasoning that follows is what survived, and it is what
  produced the amendment: one screen with no routing, no data layer of its own
  and no state outliving a reload does not need a build step. See the 20 Aug
  entry in `Memory.md`. Reintroducing a framework needs a reason this surface
  does not currently have.
- No component library. See `Design.md`; shadcn defaults are exactly the "stale and repetitive" look the brief warns against.
- No charting library for the latency waterfall. It is four divs and a CSS transform. Adding `recharts` for this is 90 KB for nothing.
- No API key, ever, anywhere under `frontends/`. The browser talks to
  `stt_gateway`; the gateway talks to Sarvam. This one is HARD, see section 4.

### 3.2 Do not use (HARD)

| Banned | Reason |
|---|---|
| LangChain / LlamaIndex / Haystack in `rag_core` runtime | See 2.1 |
| Any auth library | No login. Not in scope. |
| Any ORM or database | There is no persistent state. Do not invent one. |
| Redis, Celery, RabbitMQ | Not needed. One process, in memory. |
| Docker Compose in production | One container per service. Compose is for local dev only. |
| `requests` (sync) anywhere in a service | Blocks the loop. Use `httpx.AsyncClient`. |
| Vercel serverless functions for `rag_core` | Cold starts. Non-negotiable. |
| `localStorage` / `sessionStorage` in artifacts | Not supported in the preview environment |
| Any paid service without a free tier we have verified | Budget risk mid-build |

### 3.3 Model allowlist (SOFT, benchmark before deviating)

| Role | Model | Alternate if it underperforms |
|---|---|---|
| Embedder | `intfloat/multilingual-e5-small` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| Reranker | **`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`** — changed in Phase 5, see below | `BAAI/bge-reranker-base` (slower, no int8 build published) |
| LLM | Groq `llama-3.3-70b-versatile` | Groq `llama-3.1-8b-instant` if TTFT matters more than quality |
| Toxicity | ~~a small distilled ONNX classifier~~ **shipped as a pattern set, Phase 6** | keyword list plus the LLM path only |
| STT | Sarvam `saaras:v3-realtime` | Sarvam `saaras:v3` legacy WS. **Not** ElevenLabs, we picked one. |

**Reranker deviation, taken under this rule's own "benchmark before deviating"
clause.** The original entry was `cross-encoder/ms-marco-MiniLM-L-6-v2`, which is
English-only, and half the frozen slice is Hindi. Both were measured on 300 dev
queries through an identical path (`scripts/05d_eval_rerank.py`):

| | en Hit@1 | hi Hit@1 |
|---|---|---|
| dense, no rerank | 0.360 | 0.233 |
| `ms-marco-MiniLM-L-6-v2` | 0.447 | **0.120** |
| `mmarco-mMiniLMv2-L12-H384-v1` | 0.417 | **0.307** |

The English-only model wins English and takes Hindi *below the no-rerank baseline*.
The replacement is the only arm that significantly improves both languages. Cost of
the change: 113 MB int8 instead of 22 MB, and English gives up ~0.03 Hit@1.
Recorded in `Memory.md`, 19 Aug.

**Toxicity deviation, taken under this rule's own fallback clause.** The row
above allows "keyword list plus the LLM path only" as the alternate, and that is
what shipped in Phase 6: intent patterns in `guardrails/input_guard.py`, not a
classifier. A classifier is another model to load, warm and budget for on a
2 vCPU box, and it would have landed the day before a code freeze without its
false-positive rate measured. What makes the pattern set defensible rather than
merely cheaper is the control group in `tests/test_input_guard.py`: nine
legitimate questions about weapons, medicine, crime and hacking must all pass,
because a web corpus legitimately covers those subjects. Measured: unsafe intent
caught 12 of 12, zero false positives on the control group.

**HARD:** The brief says pick one STT provider. We picked Sarvam. Do not add ElevenLabs "as a fallback"; it reads as indecision and it doubles the integration surface.

---

## 4. API and key handling

**HARD.**
- No API key ever reaches the browser. Sarvam and Groq keys live only in `services/`.
- The browser talks to our STT gateway, never to Sarvam directly.
- `.env` is gitignored. `.env.example` is committed with empty values and a comment per variable.
- Before the repo goes public, run a secret scanner over the full git history, not just the working tree. A key in an old commit is still a leaked key.
- The Firecrawl key that appeared in the onboarding doc during setup is a session key and should be treated as compromised. Rotate it and do not commit it.

**HARD.** Rate limits are handled in code, not by hoping. Groq client wraps every call in the circuit breaker from `harness/policies.py`. A 429 opens the breaker and routes to extractive.

---

## 5. Data rules

**HARD.**
- The corpus slice is frozen in Phase 1 and never silently changed. `artifacts/slice_manifest.json` records the languages, row counts, random seed and dataset revision hash.
- If the slice changes, every benchmark number in `bench/results/` is invalidated and must be regenerated. Do not mix results from different slices.
- `bench/queries_250.jsonl` is frozen before any optimization starts. You may not tune against a benchmark you are still editing.
- Benchmark results are committed, dated, and never overwritten. New run, new file.

**HARD.** Do not index the full MSMARCO-XI. Individual validation splits run to hundreds of megabytes; the whole thing will not fit in a hackathon-budget container and indexing it wastes days.

**SOFT.** Target slice: English plus Hindi, Tamil and Bengali. Roughly 150k to 400k passages total. Enough to be non-trivial, small enough to iterate on.

---

## 6. Code rules

**HARD.**
- Type hints on every function in `rag_core`. `mypy --strict` on that package in CI.
- No bare `except:`. Every caught exception is a named type from `harness/errors.py`.
- No magic numbers in the hot path. Thresholds live in `guardrails/policies.yaml` and `config.py`.
- Every threshold in the guardrail config has a comment explaining what calibrated it.

**SOFT.**
- Functions under 40 lines.
- One chunker per file, all implementing the same protocol. Do not add a ninth strategy by adding an `if` to an existing one.
- Tests for the harness, the chunkers and the guardrails. Not for the UI.

**HARD.** `tests/test_latency_contract.py` runs the 250-query benchmark in CI and fails the build if P50 regresses past the committed threshold. This is the only way the target survives contact with a week of commits.

---

## 7. Git and process rules

**HARD.**
- `main` is always deployable. Feature work on branches.
- One branch per phase from `Phases.md`. Merge only when the phase exit criteria are met.
- Commit messages reference the phase: `[P3] add semantic breakpoint chunker`.
- `Memory.md` is updated at the end of every phase, before the merge. A phase is not done until its `Memory.md` entry exists.

**HARD.** Code freeze 24 hours before the deadline: 21 August, 11:59 PM. The final day is video, deployment verification and social posting only. Nothing else.

**HARD.** There are no resubmissions. The form is submitted once, by one designated person, after every checklist item in `Submission.md` is ticked by a second person.

---

## 8. Connectors, plugins and tooling

These are development-time tools, not runtime dependencies. Nothing here ships in the deployed app.

| Tool | Used for | Rule |
|---|---|---|
| Firecrawl MCP | Scraping Sarvam docs, Groq docs, HF dataset cards, competitor writeups during research | Research only. Never called at runtime. Keys never committed. |
| Hugging Face MCP | Dataset inspection, model card lookup, checking ONNX export availability | Read-only. |
| Vercel MCP | Frontend deploy, build log inspection, runtime error triage | Frontend only. `rag_core` does not deploy here. |
| Google Drive | Video files, shared assets | Do not put keys or `.env` files here. |
| Canva | Video 1 and Video 2 editing, thumbnails, social crops | Export at 1080x1080 for Instagram, 16:9 for X and LinkedIn. |
| Coding agent | Implementation | Feed it `Rules.md` and the current phase from `Phases.md` at the start of every session. |

**HARD.** No MCP connector, Firecrawl included, appears anywhere in `services/`. They are for the humans and the coding agent, not for the product.

---

## 9. What to do when a rule blocks you

1. Check whether the rule is HARD or SOFT.
2. If SOFT, break it and write one line in `Memory.md` saying why.
3. If HARD, do not break it silently. Raise it with the team. If the team agrees, amend this file in the same commit as the change, so `Rules.md` never lies about what the code does.

A rule that everybody quietly ignores is worse than no rule, because it makes the rest of the document untrustworthy.
