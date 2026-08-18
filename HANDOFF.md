# HANDOFF.md

**For a teammate picking this up on their own machine, with or without an AI coding session.**

The repo is the handoff. This file covers only what the repo cannot: what a human has to do by hand, what was decided and why it is not in the code yet, and what is deliberately not committed.

Last updated: 15 August 2026, end of Phase 1.

---

## 1. Sixty-second orientation

Team OK4T is building **Shruti**, a voice-enabled RAG system for HH Goa 2026 Shortlisting Task 2. Speak a question in Hindi or English, get an answer grounded in AI4Bharat's MSMARCO-XI corpus, cited, with a live per-stage latency breakdown — and the system refuses to answer when it cannot ground the answer.

**Deadline 22 August 2026, 11:59 PM IST. Code freeze 21 August. No resubmissions.**

Read in this order, and do not skip the first one:

1. `Memory.md` — decisions, reversals, phase log, open assumptions. Carries the *why*, which is the expensive part to reconstruct.
2. `Rules.md` — HARD and SOFT constraints. HARD ones are not negotiable without the whole team agreeing in writing.
3. `Phases.md` — find the current phase and its exit criterion.
4. `Architecture.md`, `Latency.md` — the design and the budget.

**Phases 0 and 1 are complete.** Phase 2 (thin vertical slice, text only) is next and is the most important checkpoint in the project: if its P50 is already over 150 ms, the architecture is wrong with a week left.

---

## 2. Local setup, start to finish

### 2.1 Prerequisites you install yourself

| Tool | Version | Note |
|---|---|---|
| **Python** | **3.12** | Not 3.13/3.14. `onnxruntime`, `hnswlib` and `bm25s` have no wheels above 3.12. On Windows, `py -3.12` selects it. |
| Git | any recent | |
| Node | 20+ | Not needed until Phase 4 (the Next.js frontend) |

### 2.2 Clone and build

```bash
git clone https://github.com/haziqlandge/RAG_OK4T.git
cd RAG_OK4T
py -3.12 -m venv .venv                      # macOS/Linux: python3.12 -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
```

### 2.3 Rebuild everything from scratch

**Nothing under `artifacts/` is committed except `slice_manifest.json`** — it is ~1.7 GB of data. All of it regenerates from the repo, and the manifest is what guarantees you get a **byte-identical** corpus rather than a similar one.

Run these in order. Total ~45 minutes, most of it the index build:

```bash
.venv/Scripts/python scripts/00_download_dataset.py      # 440 MB download, ~3 min
.venv/Scripts/python scripts/01_freeze_slice.py          # ~2 min
.venv/Scripts/python scripts/01_freeze_slice.py --verify artifacts/slice_manifest.json
.venv/Scripts/python scripts/03_export_onnx.py           # 578 MB download + parity gate, ~6 min
.venv/Scripts/python scripts/02_build_indexes.py         # ~31 min, CPU-bound
```

**Checkpoints — if any of these differ, stop and investigate before building on top:**

| after | must show |
|---|---|
| `01_freeze_slice.py --verify` | `slice reproduces exactly.` — 15,000 queries, 295,890 passages, `records_sha256` starting `7f9f7c59` |
| `03_export_onnx.py` | `PASS int8 matches fp32 on retrieval`, int8 file ~113 MB |
| `02_build_indexes.py` | 379,242 chunks, `index.bin` ~655 MB |

A failed `--verify` means every number in `bench/results/` is invalid against your slice. Do not proceed.

### 2.4 Verify the rig and the pipeline

```bash
.venv/Scripts/python -m pytest                                    # 35 tests, all must pass
.venv/Scripts/python scripts/04_bench_latency.py --stub --breakdown
.venv/Scripts/python scripts/05_eval_retrieval.py                 # correctness gate
.venv/Scripts/python scripts/04_bench_latency.py --pipeline --lang en --breakdown
```

Expected: the stub reports P50 near 72.5 ms with `PASS: harness overhead is within 5 ms`. `05_eval_retrieval.py` reports **en Recall@10 ≈ 0.870, hi ≈ 0.682** and prints `PASS retrieval is sound`.

To run the service:

```bash
cd services && ../.venv/Scripts/python -m uvicorn rag_core.main:app --port 8000
```

`/health` returns 503 until warmup finishes (~2 s), then 200.

### 2.5 What does NOT transfer between machines

**Latency numbers.** `Latency.md` §6 fixes the measurement environment, and a different CPU produces different results. The Phase 2 figures (en P50 3.31 ms) were measured on an i5-12400F. Results in `bench/results/` are tagged with a git SHA and machine details for exactly this reason — **do not compare numbers across machines, and do not publish numbers from a dev box.** The figures that ship come from the deployed GCP instance (`Latency.md` §6, issue I8).

**Thread counts.** `ONNX_THREADS_BUILD=8` was tuned on a 6-physical-core CPU (see `ISSUES.md` I6). On a machine with a different core count, re-measure against **real C1 chunks** before changing it — a synthetic benchmark gave a directionally wrong answer once already.

**Retrieval quality numbers DO transfer.** Recall@10, MRR@10 and Hit@1 depend only on the frozen slice and the model, both of which are reproducible. If those differ, something is genuinely wrong.

---

## 3. Secrets — what you need and how to get them

`.env` is gitignored and **must never be committed**. Copy `.env.example` to `.env` and fill it in. `Rules.md` §4 is a HARD rule: no key ever reaches the browser, and keys live only in `services/`.

| Variable | Needed for | How to get it |
|---|---|---|
| `SARVAM_API_KEY` | STT, Phase 4 | [dashboard.sarvam.ai](https://dashboard.sarvam.ai) — free credits on signup |
| `GROQ_API_KEY` | LLM fallback, Phase 5 | [console.groq.com/keys](https://console.groq.com/keys) — free tier |
| `HF_TOKEN` | Optional | huggingface.co settings → Access Tokens. **Read scope.** Only raises download rate limits; the dataset is public. |

**Format matters:** write `KEY=value`. Not `KEY= "value"`. A quote read as part of the secret produces a 401 that looks like a bad key rather than a bad file — this cost us time already. `config.load_env()` now strips them defensively, but other tools will not.

**Do not share keys over chat, or put `.env` in Google Drive** (`Rules.md` §8). Each person makes their own keys — all three services have free tiers, so there is no reason to share.

### Verified account facts

- **Groq's edge returns `403, error code: 1010`** — a Cloudflare fingerprint block — to any request with a default `urllib`/`requests` User-Agent. It looks exactly like an auth failure and is not one. Set an explicit User-Agent; `config.USER_AGENT` exists for this.
- **Groq free tier is 12,000 tokens per window.** A full 250-query Band B benchmark needs ~250k tokens and will throttle. Band B must be a ~50-query sample, stated in the methodology.
- **A minimal Groq call measured 352 ms** end to end — 5 max tokens, no retrieval. That is the floor, and it is already 1.75× the entire 200 ms budget. This is our own measurement and it is the strongest single piece of evidence for the dual-path design.

---

## 4. What only a human can do

Code cannot close these. They are tracked here because forgetting them is how this task gets lost on non-engineering grounds.

| # | Task | Owner | Status | Blocks |
|---|---|---|---|---|
| H1 | GCP project + VM in `asia-south1`, **and a budget alert** | | ☐ in progress | Phase 7 deploy |
| H2 | Sarvam account + key | | ✓ done | Phase 4 |
| H3 | Groq account + key | | ✓ done | Phase 5 |
| H4 | Vercel account for the frontend | | ☐ | Phase 7 |
| H5 | Record footage **during** Phases 2–7, not reconstructed on the 22nd | everyone | ☐ | Video 1 |
| H6 | Every member posts both videos to Instagram, X and LinkedIn with `#RAGInGoa` | everyone | ☐ | **Submission** |
| H7 | At least one Instagram account set public, decided before posting day | | ☐ | **Submission** |
| H8 | Secret scan over full git history before the repo is judged | | ☐ | Submission |

**H5 is the one that gets forgotten.** `Submission.md` §2 wants a genuine failure moment on camera — a bench run over budget, then the fix, then the run under budget. You cannot stage that convincingly on the last day. Record every benchmark run from Phase 2 onward; you will not know which is the good clip until later.

**H6 and H7 are mandatory, per-member, and across three platforms.** Teams lose on this, not on engineering.

### GCP setup, since H1 is in flight

Full procedure in `deploy/gcp.md`. The parts that are easy to get wrong:

- **Region `asia-south1` (Mumbai)**, `n2-standard-2`, Ubuntu 22.04, always on. **Not `e2`** — `e2` is burstable and burst throttling wrecks P100, which is the number that fails.
- **Not Cloud Run.** A ~1.2 GB warm index cannot survive cold starts.
- **Set a budget alert immediately** ($50 / $150 / $250). If the $300 drains, every resource stops and Compute Engine data is marked for deletion with a 30-day grace period. Losing the live URL during the September selection rounds would be an unforced submission failure.
- Reserve a **static IP before** creating the VM, so the URL never moves.
- The trial ends at $300 **or** 90 days, whichever comes first — roughly 13 November from a 15 August signup. You are not charged unless you manually upgrade.

---

## 5. Decisions taken this session that are not obvious from the code

Full reasoning lives in `Memory.md`; this is the index.

| ID | Decision | One-line why |
|---|---|---|
| D1 | Sarvam over ElevenLabs | Indic corpus, Indic STT, and partial transcripts enable the prefetch optimization |
| D2 | Dual path: extractive fast, LLM fallback | A hosted LLM call cannot fit 200 ms. Measured: 352 ms floor. |
| D3 | Everything in-process, no hosted vector DB or embeddings | Every network hop is 20–80 ms; the budget survives none |
| D4 | No LangChain/LlamaIndex at runtime | You cannot budget what you cannot see |
| D5 | Freeze a corpus slice | Reproducibility beats size for this task |
| D6 | Publish three latency bands honestly | An honest 340 ms beats a fabricated 190 ms a judge can poke |
| D7 | Always-on container in India, never serverless | Cold starts destroy P100 |
| **D8** | **Chunking here is composition, not splitting** | **English passages max out at 205 words. A 256-token chunker is inert on this corpus.** |
| **A7** | **Oracle Cloud Hyderabad, free** | **Render has no India region and its 512 MB tiers OOM against our ~1.2 GB** |

And two reversals — the highest-value entries, because they are the mistakes that would otherwise be repeated:

- **R1: MSMARCO-XI has no `url` field**, unlike upstream MS MARCO. C5 metadata chunking was respecified around `query_type`. Lesson: assert schemas against the file, not the dataset card.
- **R2: `is_selected` cannot live on a passage.** It describes a *(query, passage)* pair, so after dedup it would be arbitrary — and would have produced plausible-looking but wrong Recall@10. Ground truth moved onto the query.

### Recommendations on the table, not yet decided

- **Write the Dockerfile in Phase 2, not Phase 7.** It is the entire portability story between Render and Oracle and makes the host switch a non-event.
- **All eight chunking indexes cannot be resident at once** (~4 GB+). The F13 strategy toggle should load on switch. A deliberate toggle is not the hot path, so this is acceptable — but it changes F13's design.
- **Cap passage length at the 99.5th percentile** before any whole-passage encoder. One Hindi passage is 4,093 words against a 205-word English source — a translation repetition loop — and a handful of those will dominate index build time.

---

## 6. Traps already paid for

Do not rediscover these.

| Trap | Reality |
|---|---|
| `load_dataset("ai4bharat/MSMARCO-XI", "hi")` | **Fails.** The loader script points at `.jsonl`; the repo holds `.parquet`. Same reason the HF dataset viewer 500s. Use `hf_hub_download` directly. |
| `asyncio.sleep` for sub-15 ms timing on Windows | Timer granularity is ~15.6 ms. Every stage under 15 ms measures identically. Use a `perf_counter_ns` spin. |
| `.gitignore` with `artifacts/` | Git cannot un-ignore a file inside an ignored directory. Must be `artifacts/*` so `slice_manifest.json` can be committed. |
| PowerShell 5.1 + `git commit -m "multi-line"` | Mangles embedded double quotes. Use `git commit -F <file>`. |
| ~45% of MS MARCO rows have no `is_selected` | They are useless as retrieval ground truth. The slice oversamples 2.0× to compensate. |
| Python 3.14 | Machine default on the original dev box, and has no wheels for the stack. Use 3.12. |

---

## 7. Starting an AI coding session on this repo

Clone, follow §2, then paste:

> You are working on team OK4T's HH Goa 2026 Task 2 submission: a voice-enabled RAG system with a 200 ms latency target on the core pipeline.
>
> Read these files first, in order: `HANDOFF.md`, `Memory.md` (context and decisions), `Rules.md` (hard constraints), `Phases.md` (find the current phase), `Architecture.md` (the design), `Latency.md` (the budget).
>
> Key context: the fast path makes zero network calls. Extractive answering when reranker confidence is high, Groq LLM fallback when moderate, abstention when low. No LangChain. No hosted vector DB. No hosted embeddings. Everything in-process on ONNX int8. Deploy target is Oracle Cloud Ampere A1 in Hyderabad, which is **ARM**.
>
> Tell me which phase we are on and what its exit criterion is before writing any code.

**Two working rules that are easy to get wrong:**

- **Commits carry no AI attribution.** No `Co-Authored-By`, no crediting an assistant in commits, PRs, README or docs. Message format is `[P3] add semantic breakpoint chunker`, referencing the phase.
- **A phase is not done when the code is written.** It is done when its exit criterion is demonstrably met *and* its `Memory.md` entry exists. That is a HARD rule in `Rules.md` §7.

---

## 8. Splitting the work from here

Phase 2 is one vertical slice and does not parallelise well — one person should own it. Phase 3 splits cleanly across two people (the chunker list in `Phases.md` is already divided). Phases 4 and 5 run in parallel: voice input touches only `stt_gateway` and `apps/web`, while reranking and routing touch only `rag_core`.

`Phases.md` also carries the cut order if time runs short. Never cut: the guardrail eval set, the latency benchmark, the deployment, the videos, the posting. Those are scored requirements; everything else is depth.
