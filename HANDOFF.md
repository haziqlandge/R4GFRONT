# HANDOFF.md

**For a teammate picking this up on their own machine, with or without an AI coding session.**

The repo is the handoff. This file covers only what the repo cannot: what a human has to do by hand, what was decided and why it is not in the code yet, and what is deliberately not committed.

Last updated: 20 August 2026, after Phases 4, 5 and 8. The frontend was replaced and `apps/web` removed - see section 5A.

> **On a brand-new machine, read [`PREREQUISITES.md`](PREREQUISITES.md) first.** It takes a bare box to a verified one. This file assumes that is done.

---

## 1. Sixty-second orientation

Team OK4T is building **Shruti**, a voice-enabled RAG system for HH Goa 2026 Shortlisting Task 2. Speak a question in Hindi or English, get an answer grounded in AI4Bharat's MSMARCO-XI corpus, cited, with a live per-stage latency breakdown — and the system refuses to answer when it cannot ground the answer.

**Deadline 22 August 2026, 11:59 PM IST. Code freeze 21 August. No resubmissions.**

Read in this order, and do not skip the first one:

1. `Memory.md` — decisions, reversals, phase log, open assumptions. Carries the *why*, which is the expensive part to reconstruct.
2. `Devices.md` — which of the three machines you are on, and what it may publish.
3. `Phase3-Parallel.md` — the Phase 3 job board (J1–J16) and who owns what.
4. `Rules.md` — HARD and SOFT constraints. HARD ones are not negotiable without the whole team agreeing in writing.
5. `ISSUES.md` — measured open problems.
6. `Architecture.md`, `Latency.md` — the design and the budget.

**Phases 0-3 are complete.** Band A P50 **3.31 ms** against a 200 ms budget,
en Recall@10 **0.878**, `mypy --strict` clean.

**Phases 4, 5 and 8 are also complete**, on branch `p4-p5-voice-rerank`.
Band A P50 **59.99 ms** en / **73.77 ms** hi with the reranker in the path, Band B
**653.6 ms**, 202 tests green, `mypy --strict` clean. Voice input, routing,
abstention and the site all work end to end. See `Memory.md` Phase 4, 5 and 8
entries, and **read `ISSUES.md` I24, I25 and I26 before trusting any Phase 5
number** - I26 in particular corrects a claim an earlier draft made about
abstention.

**The frontend was replaced on 20 Aug.** The site is now `frontends/`: static
HTML, one stylesheet and ES modules, no build step, served by
`python -m http.server` on :3000. `apps/web` has been deleted; it is recoverable
from git history if anyone wants it back. Section 5A below is the current
frontend handoff and the 20 Aug entry in `Memory.md` records why.

**START HERE.** The order a council recommended on 19 Aug given a 21 Aug code
freeze, with what has since been done marked. Items 2, 3 and 5 are done; **1, 4,
6, 7 and 8 are not**, and item 4 has been promoted to the top:

1. ~~Input guard~~ **STILL NOT BUILT, and now the only thing bounding `embed_query`** -
   `ISSUES.md` I25 found that a stage timeout cannot interrupt synchronous ONNX
   work, so the 118 ms pathological query has no other guard. Phase 6.
2. ~~Phase 5 reranker~~ **DONE.** The honest result: it closed most of the Hindi
   gap (+0.073, significant) and left English roughly where it was (+0.033, CI
   spans zero). **A6 is false**, D2's reversal condition fired, and the
   fast/accurate mode toggle is built.
3. ~~Calibrate abstention~~ **DONE, and read `ISSUES.md` I26 before quoting the
   result.** `tau_low = -1.103` catches 100% of off-topic and gibberish input, but
   **92.5% of wrong top-1 answers pass it** - it is an out-of-domain detector, not
   a grounding detector.
4. **Phase 6 guardrails + adversarial set. NOW THE TOP PRIORITY.** I26 makes the
   OUTPUT guard load-bearing rather than decorative: the retrieval-score floor
   cannot catch the 62.1% of answers that are wrong, only checking groundedness
   against the answer text can. Explicitly scored: "show your system knows when
   NOT to answer."
5. ~~Phase 4 voice~~ **DONE** (mic path untested against a real microphone - see
   `HANDOFF.md` 5A).
6. **Phase 7 deploy** to the idle GCP Mumbai VM (`34.100.222.236`). Start early;
   do not let day 3 be the first deploy.
7. ~~**Phase 8 frontend**~~ **DONE.** Replaced on 20 Aug: `frontends/`, static,
   no build step. Demo page and a documentation page carrying every published
   number with its source file named. Section 5A.
8. **Phase 9 videos + social posting by every member.**

Never cut (own planning docs): guardrail eval, latency benchmark, deployment,
videos, posting.

---

## 2. Local setup, start to finish

### 2.1 Prerequisites you install yourself

| Tool | Version | Note |
|---|---|---|
| **Python** | **3.12** | Not 3.13/3.14. `onnxruntime`, `hnswlib` and `bm25s` have no wheels above 3.12. On Windows, `py -3.12` selects it. |
| Git | any recent | |
| Node | - | **No longer required.** The site is static and served by Python. It was needed for `apps/web`, which has been removed. |

### 2.2 Clone and build

```bash
git clone https://github.com/haziqlandge/R4GFRONT.git
cd R4GFRONT
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
.venv/Scripts/python -m pytest                                    # 202 tests, all must pass
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

**Only BENCH needs the Sarvam and Groq keys.** They are Phase 4/5 runtime concerns. EMBED and LLM run corpus and index jobs against public HuggingFace downloads and need **no keys at all** — see `PREREQUISITES.md` §5. Fewer copies of a secret is strictly better.

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
| **A7 → R3** | **GCP Compute Engine, `asia-south1` (Mumbai), x86** | Render has no India region and its 512 MB tiers OOM against our ~1.2 GB. Oracle was chosen first, then superseded: GCP is x86, which retired the ARM risk (A11) without an experiment. |
| **D9-D12** | **Phase 3 splits across three machines** | Split by resource consumed, not strategy count. C4 on Groq is arithmetically impossible (~24M tokens vs a 12k window), so it moves to local hardware. Build time stops being a comparison metric. |

And two reversals — the highest-value entries, because they are the mistakes that would otherwise be repeated:

- **R1: MSMARCO-XI has no `url` field**, unlike upstream MS MARCO. C5 metadata chunking was respecified around `query_type`. Lesson: assert schemas against the file, not the dataset card.
- **R2: `is_selected` cannot live on a passage.** It describes a *(query, passage)* pair, so after dedup it would be arbitrary — and would have produced plausible-looking but wrong Recall@10. Ground truth moved onto the query.

### Recommendations on the table, not yet decided

- **Write the Dockerfile before Phase 7.** It is the entire portability story between hosts and makes a switch a non-event — which already paid off once, when the target moved from Render to Oracle to GCP.
- **All eight chunking indexes cannot be resident at once** (~4 GB+). The F13 strategy toggle should load on switch. A deliberate toggle is not the hot path, so this is acceptable — but it changes F13's design.
- **Cap passage length at the 99.5th percentile** before any whole-passage encoder. One Hindi passage is 4,093 words against a 205-word English source — a translation repetition loop — and a handful of those will dominate index build time.

---

## 5A. The frontend, for whoever picks it up next

**`frontends/` is the site.** Static HTML, one stylesheet and ES modules, served
by `python -m http.server` on port 3000. No build step, no `node_modules`, no
framework. A change is visible on reload.

It replaced `apps/web` (Next.js 15 + React 19 + TypeScript) on 20 Aug 2026.
`apps/web` has been **deleted from the working tree** and is recoverable from git
history if anyone wants it back. The 20 Aug entry in `Memory.md` records the
reasoning; the short version is that the surface a judge sees is a page, not an
application, and the build step was buying nothing.

Read `frontends/README.md` before touching it. It is longer than this section
and it carries the parts a diff cannot explain.

### Running the whole stack locally

Three processes. The site is useless without the other two.

```bash
run-dev.bat                 # all three, each in its own window
frontends\serve.bat         # the same, and opens the browser
```

Or by hand:

```bash
# 1. rag_core - the 200ms pipeline
cd services && ../.venv/Scripts/python -m uvicorn rag_core.main:app --port 8000

# 2. stt_gateway - holds the Sarvam key, browser never talks to Sarvam directly
cd services && ../.venv/Scripts/python -m uvicorn stt_gateway.main:app --port 8001

# 3. the site
cd frontends && ../.venv/Scripts/python -m http.server 3000
```

Then open `http://localhost:3000`.

**Port 3000 is load bearing, not a habit.** `services/stt_gateway/config.py`
allows CORS from `localhost:3000` and `127.0.0.1:3000` only, because that is the
process holding the Sarvam key and a wildcard origin on a credential-holding
service is not acceptable (`Rules.md` 4). `rag_core` holds no key and is
permissive, which makes the failure confusing: on any other port **typing works
and speaking fails**, with a CORS rejection that reads exactly like a broken
microphone.

**Use localhost, not a LAN IP** - `getUserMedia` requires a secure origin, and
`localhost` counts while `192.168.x.x` does not. On the deployed box this means
HTTPS is mandatory or the microphone silently never prompts.

Check `http://localhost:8000/health` first. It reports which capabilities
actually came up (`reranker`, `generative`, `passage_store`); a dense-only
process and a fully-reranked one are both "ok" and answer differently.

### What is where

| file | what it is |
|---|---|
| `frontends/index.html` | the demo page. Bespoke markup carrying `data-sh` hooks |
| `frontends/docs.html` | the documentation page. A thin shell over `renderDocs()` |
| `frontends/theme.css` | every visual decision: palette, type, layout, ornament, motion |
| `frontends/console.js` | the on-page console panel. Styling, not a shell |
| `_shared/pcm-worklet.js` | 48 kHz -> 16 kHz PCM16 with a windowed-sinc low-pass. **The riskiest file in the frontend.** Read its header before touching it. |
| `_shared/core.js` | getUserMedia, the worklet graph, both service clients, the session analytics store |
| `_shared/data.js` | **every published number, once.** Each block names the dated file under `bench/results/` it came from |
| `_shared/ui.js` | answer, citations, abstention panel, waterfall, analytics renderers |
| `_shared/docs.js` | the documentation page renderer and its sticky section bar |
| `_shared/app.js` | the controller that binds the markup to all of the above |
| `_shared/base.css` | structure only. Sets no colour, no font and no border of its own |

The split between `base.css` and `theme.css` is the one piece of the eight-theme
experiment worth keeping. Structure reads a token contract; the theme defines it.
That is why a full visual redirection costs a stylesheet rather than a rewrite,
and it is why deleting seven treatments cost nothing.

### Rules that are not negotiable in this directory

- **Every number on screen is mono and tabular.** `Design.md` 3.2. This is the
  one typographic rule that makes it feel like an instrument rather than a web
  page.
- **The abstention panel gets equal visual weight to an answer**, and is never
  styled as an error. It is a correct outcome, and it is the single most
  convincing shot in Video 2.
- **The measurement boundary is stated on screen**, not only in the README.
  `pipeline` and `speech` are separate readouts. A judge times from when they
  stop speaking, and a 200 ms claim that quietly excludes speech reads as
  cherry-picking, which is worse than being slower.
- **No API key may appear anywhere under `frontends/`.** `Rules.md` 4 is HARD.
  The browser talks to `stt_gateway`; the gateway talks to Sarvam.
- **No figure is typed into markup.** Everything measured comes from
  `_shared/data.js`, which names its source file. Two different P50s in one
  submission costs the reader's trust in every other number on the page.

`Design.md` was written for `apps/web` and its type and colour specifics no
longer describe this surface. The rules above are the parts of it that survived
the change and they still hold; the rest of that document is now history.

### Known gaps, in priority order

1. **The microphone path has never run against real audio.** No mic on the build
   box. The gateway was proven by feeding Sarvam TTS back through STT, so
   `getUserMedia` -> AudioWorklet -> resampler is the unexercised stretch. Test
   this before anything else on a machine that has a microphone.
2. The realtime socket (`/v1/stt/live`) is still unwired, so partials and the
   `Latency.md` 5 prefetch remain hypothetical and must not be claimed.
3. Not built: the live strategy toggle (F13), the failure-injection param that
   forces a 429 to demo the circuit breaker, and the citation matched-span
   highlight.
4. `frontends/_backup/03-terminal-v1/` is the previous version of this design,
   kept self-contained so a rollback is a copy. Delete it once nobody wants it.

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

Finish `PREREQUISITES.md` first — the prompts below assume a verified box.

**Shared preamble, every box:**

> You are working on team OK4T's HH Goa 2026 Task 2 submission: a voice-enabled RAG system with a 200 ms latency target on the core pipeline.
>
> Read these first, in order: `PREREQUISITES.md`, `Memory.md` (decisions and reversals — the *why*), `Devices.md` (what this machine is and what it may publish), `Phase3-Parallel.md` (the job board), `Rules.md` (HARD constraints), `ISSUES.md` (measured open problems), `Architecture.md`, `Latency.md`.
>
> Key context: the fast path makes zero network calls. Extractive answering when reranker confidence is high, Groq LLM fallback when moderate, abstention when low. No LangChain, no hosted vector DB, no hosted embeddings — everything in-process on ONNX int8. Deploy target is **GCP Compute Engine `n2-standard-2` in `asia-south1` (Mumbai), x86** (see `Memory.md` reversal R3; an earlier draft said Oracle ARM and that is superseded).
>
> Phase 2 is complete: Band A P50 3.31 ms, en Recall@10 0.870. Phase 3 is running across three machines.

**Then add your box's line:**

| Box | Add this |
|---|---|
| **BENCH** | *"I am on BENCH (i5-12400F, CPU only). I own jobs J9–J16 and the shared files `registry.py`, `02_build_indexes.py`, `05_eval_retrieval.py`. J9 and J10 are already done. Start with J11 (BM25) unless I say otherwise."* |
| **EMBED** | *"I am on EMBED (Ryzen 7 + RTX 3060 Ti). I own jobs J1–J4. **J1 is blocking — the GPU parity gate must pass before any GPU-built index is trusted** (see `Memory.md` D10 and assumption A13). I add only `scripts/_gpu_embedder.py` and my `chunking/cN_*.py` files; I never edit `registry.py` or the build/eval scripts."* |
| **LLM** | *"I am on LLM (Core Ultra 9 + RTX 5070 Ti, Blackwell sm_120). I own jobs J5–J8. **J5 is timeboxed to 45 minutes** — if CUDA is not working by then we swap roles with EMBED (`Devices.md` §4.3, `ISSUES.md` I14). J6 is the critical path and starts as soon as J5 closes. I add only my `chunking/cN_*.py` files."* |

**Close with:**

> Tell me which jobs I own and what their exit criteria are before writing any code.

**Two working rules that are easy to get wrong:**

- **Commits carry no AI attribution.** No `Co-Authored-By`, no crediting an assistant in commits, PRs, README or docs. Message format is `[P3] add semantic breakpoint chunker`, referencing the phase.
- **A phase is not done when the code is written.** It is done when its exit criterion is demonstrably met *and* its `Memory.md` entry exists. That is a HARD rule in `Rules.md` §7.

---

## 8. Splitting the work from here

**Phase 3 onward runs across three machines.** `Phase3-Parallel.md` is the operative plan and `Devices.md` says what each box is. The split is by *resource consumed* rather than by strategy count: GPU embedding to EMBED, LLM work to LLM, zero-embedding and CPU-lexical work to BENCH.

The real win there is scheduling, not throughput. Most of Phase 3 is unattended compute, so it runs on the two spare boxes **while Phases 4 and 5 proceed on BENCH** — and those two touch disjoint code (voice was `stt_gateway` + the frontend, reranking is `rag_core`). That overlap is the only realistic recovery from the slip recorded in `ISSUES.md` I11.

`Phases.md` also carries the cut order if time runs short. Never cut: the guardrail eval set, the latency benchmark, the deployment, the videos, the posting. Those are scored requirements; everything else is depth.
