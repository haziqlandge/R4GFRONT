# PREREQUISITES.md

**Start here if you just cloned this repo.** Go from a bare machine to a verified working box without asking anyone a question.

Find your box in §2 and read only that section. The three boxes need different things, and following another box's instructions is the main way to waste an hour.

> **You do not need any API keys** unless you are on BENCH. EMBED and LLM only download public files from HuggingFace. See §5.

---

## 0. Detect this machine first

Right after cloning, before installing anything else:

```bash
git clone https://github.com/haziqlandge/RAG_OK4T.git
cd RAG_OK4T
python scripts/00_detect_system.py
```

Stdlib only — it runs before any `pip install`. It writes `LOCAL_SYSTEM_INFO.md`
(this machine's specs: CPU, RAM, GPU, VRAM, compute capability) and
`LOCAL_SYSTEM_ADDITIONS.md` (a log of local directories it created). **Both are
gitignored and per-machine** — three boxes writing to the same tracked file would
fight over whose specs "win", so neither file is ever committed. Re-run it any
time; it overwrites the info file and appends to the additions log.

It prints a suggested role (BENCH-like / EMBED-like / LLM-like) from what it
actually finds — real VRAM, real compute capability — not from what you meant to
install. **`Devices.md` stays the authority; treat the suggestion as a sanity
check.** If they disagree, say so in the team channel before doing anything else —
a box that doesn't match its documented role invalidates comparisons across
machines (`ISSUES.md` I13, I14).

---

## 1. Which box are you?

| Tag | Hardware | You own | Jobs |
|---|---|---|---|
| **BENCH** | i5-12400F, GT 710 | reference numbers, lexical, eval, integration | J9–J16 |
| **EMBED** | Ryzen 7 + RTX 3060 Ti (8 GB) | dense embedding factory | J1–J4 |
| **LLM** | Core Ultra 9 + RTX 5070 Ti (16 GB) | local LLM, whole-passage encode | J5–J8 |

Job IDs are defined in [`Phase3-Parallel.md`](Phase3-Parallel.md) §2. What each box may publish is in [`Devices.md`](Devices.md).

**The GT 710 on BENCH is not a usable GPU.** It is Kepler-class and below the floor of every current CUDA wheel. BENCH is CPU-only. Do not install CUDA on it.

---

## 2. Setup by box

### 2.0 Everyone: Python 3.12 and the virtualenv

**Python must be 3.12.** Not 3.13, not 3.14. `onnxruntime`, `hnswlib` and `bm25s` have no wheels above 3.12, and pip will fail confusingly (it tries to build from source and dies on a missing compiler).

Check what you have:

```bash
python --version
```

If it is not 3.12.x, install 3.12 from [python.org/downloads](https://www.python.org/downloads/release/python-3129/) and use the launcher to select it. On Windows `py -3.12` picks a specific version even when 3.14 is your default.

```bash
git clone https://github.com/haziqlandge/RAG_OK4T.git
cd RAG_OK4T
py -3.12 -m venv .venv
```

Every command in this repo assumes the venv's interpreter. On Windows that is `.venv/Scripts/python`, on Linux/macOS `.venv/bin/python`. **Examples below use the Windows path** — substitute if you are not on Windows.

```bash
.venv/Scripts/python -m pip install --upgrade pip
.venv/Scripts/python -m pip install -r requirements-dev.txt
```

Verify:

```bash
.venv/Scripts/python -c "import numpy, pyarrow, onnxruntime, hnswlib, tokenizers; print('base OK')"
```

Expected: `base OK`. Anything else, stop — nothing downstream works.

---

### 2.1 BENCH — i5-12400F, CPU only

Two extra packages for the lexical index (J11):

```bash
.venv/Scripts/python -m pip install bm25s indic-nlp-library
```

```bash
.venv/Scripts/python -c "import bm25s, indicnlp; print('bench OK')"
```

Expected: `bench OK`.

**Do not install `torch`, CUDA, or anything GPU on this box.** Its numbers are the project's reference numbers ([`Devices.md`](Devices.md) §1) and its environment must stay the one Phase 2 was measured on.

Now go to §3.

---

### 2.2 EMBED — Ryzen 7 + RTX 3060 Ti

sm_86 is the best-supported CUDA target there is. This box should come up without a fight.

```bash
.venv/Scripts/python -m pip install torch --index-url https://download.pytorch.org/whl/cu124
.venv/Scripts/python -m pip install sentence-transformers
```

**Smoke test — run both parts. The second one matters more.**

```bash
.venv/Scripts/python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Expected: `True NVIDIA GeForce RTX 3060 Ti`

```bash
.venv/Scripts/python -c "import torch; x=torch.randn(2000,2000,device='cuda'); print((x@x).sum().item())"
```

Expected: a number. This is a **real kernel launch** — `cuda.is_available()` returning `True` does not prove kernels run. Never trust the availability check alone.

If either fails, see §4.

> `sentence-transformers` is allowed here. [`Rules.md`](Rules.md) §3.1 permits it **for offline index building and eval only**. It must never appear in `services/rag_core` — that would violate §2.1's ban on PyTorch at request time. Your GPU code lives in `scripts/_gpu_embedder.py` (job J1) and nowhere else.

Now go to §3.

---

### 2.3 LLM — Core Ultra 9 + RTX 5070 Ti

**This is the riskiest setup in the project. Timebox it to 45 minutes.**

The 5070 Ti is Blackwell, compute capability **sm_120**. It needs **CUDA 12.8 or newer**. An older wheel installs perfectly cleanly and then dies at the first kernel launch with:

```
no kernel image is available for execution on the device
```

That message reads like a broken install. It is not — it is an architecture mismatch, and reinstalling the same wheel will not fix it. (Same class of misleading symptom as the Groq 403 and the Windows `curl` mangling in §6.)

```bash
.venv/Scripts/python -m pip install torch --index-url https://download.pytorch.org/whl/cu128
```

**Smoke test — the second command is the one that actually proves it works:**

```bash
.venv/Scripts/python -c "import torch; print(torch.version.cuda, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Expected: `12.8 True NVIDIA GeForce RTX 5070 Ti` (CUDA 12.8 or higher)

```bash
.venv/Scripts/python -c "import torch; x=torch.randn(4000,4000,device='cuda',dtype=torch.float16); print((x@x).sum().item())"
```

Expected: a number. **If this throws `no kernel image is available`, your torch build does not support sm_120** — the availability check above will still have said `True`.

#### If it is not working after 45 minutes: swap roles

Do not keep debugging. Nothing in the plan depends on *which* GPU runs which job, only on there being two working GPUs.

1. Tell the team. EMBED and LLM swap roles.
2. C4 proposition generation (J6) moves to the 3060 Ti with a smaller model — an 8 GB card runs a 3B instruct model at 4-bit comfortably.
3. This box either takes the embedding work if its CUDA stack later comes up, or runs CPU-only. The Ultra 9 is a strong CPU in its own right.

Recorded as [`ISSUES.md`](ISSUES.md) I14 and [`Devices.md`](Devices.md) §4.3.

#### LLM serving stack (for J6)

In preference order:

1. **`vllm`** if it installs cleanly on cu128 — continuous batching, and the reason C4 finishes overnight rather than over days.
2. **`llama.cpp`** server with a GGUF — far more forgiving about a new GPU architecture, and the safer choice if vLLM fights back.

Model: a **3B–7B instruct model at 4-bit**. Bigger is not better here. C4's task is "restate this passage as standalone atomic facts" — rewriting, not reasoning.

Now go to §3.

---

## 3. The reproduction contract — every box runs this

Four commands, ~10 minutes, before any Phase 3 work. They rebuild the frozen corpus locally.

**Nothing under `artifacts/` is in git** (~1.7 GB). Every box regenerates it. Do not copy these files between machines — rebuilding is faster than transferring 655 MB over a home network and gives you one less thing to get wrong.

```bash
.venv/Scripts/python scripts/00_download_dataset.py
.venv/Scripts/python scripts/01_freeze_slice.py
.venv/Scripts/python scripts/01_freeze_slice.py --verify artifacts/slice_manifest.json
.venv/Scripts/python scripts/03_export_onnx.py
```

### Checkpoints — all four must match exactly

| after | must show |
|---|---|
| `00_download_dataset.py` | 440 MB downloaded, **97,941 rows**, `schema OK` |
| `01_freeze_slice.py` | **15,000 queries, 295,890 passages** |
| `01_freeze_slice.py --verify` | **`slice reproduces exactly.`** — `records_sha256` starts `7f9f7c59` |
| `03_export_onnx.py` | **`PASS int8 matches fp32 on retrieval`**, int8 file ~113 MB |

### 🛑 If `--verify` does not pass, that box does not build indexes

This is not a warning, it is a stop. An index built against a *near-identical* slice produces retrieval numbers that look completely plausible and are wrong — the whole Phase 3 comparison table would be silently corrupted. [`Rules.md`](Rules.md) §5 exists for this.

Post the mismatch to the team and stop. Do not "just rebuild and see".

---

## 4. Verify the pipeline works

```bash
.venv/Scripts/python -m pytest
```

Expected: **90 passed**. (Was 35 at end of Phase 2; J11 lexical, J12 fusion and
the C5/C6/C7 chunkers added the rest.)

BENCH only (the other boxes have no index yet):

```bash
.venv/Scripts/python scripts/04_bench_latency.py --stub
```

Expected: P50 near 72.5 ms and `PASS: harness overhead is within 5 ms.`

---

## 5. Secrets — who needs what

`.env` is gitignored and **must never be committed**. Copy `.env.example` to `.env` if you need one.

| Variable | BENCH | EMBED | LLM | Notes |
|---|---|---|---|---|
| `SARVAM_API_KEY` | Phase 4 | ❌ | ❌ | speech-to-text, runtime only |
| `GROQ_API_KEY` | Phase 5 | ❌ | ❌ | LLM fallback, runtime only |
| `HF_TOKEN` | optional | optional | optional | **read scope**; only raises download rate limits |

**EMBED and LLM need no keys at all.** Every script you run downloads public files. If you want an `HF_TOKEN` for faster downloads, make your own at huggingface.co → Settings → Access Tokens with **read** scope. Never a write token — it buys nothing and a leaked one can modify your own HF repos.

Format matters: write `KEY=value`. **Not** `KEY= "value"` — a quote read as part of a secret produces a 401 that looks like a bad key rather than a bad file. This has already cost us time once.

**Groq is not a build resource.** Its free tier is 12,000 tokens per window; the C4 proposition pass needs ~24 million output tokens. That is not slow, it is impossible ([`Devices.md`](Devices.md) §5). Groq tokens are reserved for the Phase 5 runtime fallback and the Band B benchmark. No offline job touches it.

---

## 6. Failures already paid for

Do not spend time rediscovering these.

| Symptom | Reality |
|---|---|
| `no kernel image is available for execution on the device` | Architecture mismatch, not a broken install. sm_120 needs CUDA 12.8+. §2.3. |
| `load_dataset("ai4bharat/MSMARCO-XI", "hi")` fails | It does not work. The repo's loader script points at `.jsonl` paths that no longer exist. Use `hf_hub_download` — our scripts already do. |
| Hindi query via `curl -d` returns nonsense | PowerShell/`curl` mangles non-ASCII on Windows. Test Indic endpoints with a real HTTP client. `ISSUES.md` I12. |
| Groq returns `403, error code: 1010` | Cloudflare fingerprint block on default Python User-Agents, **not** an auth failure. Set an explicit User-Agent (`config.USER_AGENT`). |
| `git commit -m "multi-line"` mangles the message | PowerShell 5.1 breaks embedded quotes. Use `git commit -F <file>`. |
| Everything is slow / a thread count seems wrong | Thread counts are tuned per-CPU. Re-measure against **real C1 chunks**, never a synthetic workload. `ISSUES.md` I6 records how a synthetic benchmark gave a directionally wrong answer. |
| A finished index was replaced by a tiny one | `--limit` is a smoke test. It now writes to `<strategy>-smoke/` and can no longer touch the canonical index — but if you see a `meta.json` with far fewer passages than expected, that is what happened. Check `counts.passages` before trusting an index. |

---

## 7. What to read next, and how to start your Claude session

Read in this order. `Memory.md` first — it carries the *why*, which is the expensive part to reconstruct.

1. [`Memory.md`](Memory.md) — decisions, reversals, phase log
2. [`Devices.md`](Devices.md) — what your box is and what it may publish
3. [`Phase3-Parallel.md`](Phase3-Parallel.md) — your job IDs
4. [`Rules.md`](Rules.md) — HARD constraints
5. [`ISSUES.md`](ISSUES.md) — measured open problems
6. [`Architecture.md`](Architecture.md), [`Latency.md`](Latency.md) — design and budget

Per-box cold-session prompts are in [`HANDOFF.md`](HANDOFF.md) §7. Paste yours into a fresh Claude session after finishing §3.

### Two working rules that are easy to get wrong

**You add exactly one file.** `chunking/registry.py`, `scripts/02_build_indexes.py` and `scripts/05_eval_retrieval.py` are **owned by BENCH**. Your work goes in a single `services/rag_core/chunking/cN_*.py` (plus `scripts/_gpu_embedder.py` if you are EMBED on J1). Three people editing one dispatch function on three branches is the merge conflict nobody has time for.

**No AI attribution in commits.** No `Co-Authored-By`, no crediting an assistant in commits, PRs, README or docs. Message format is `[P3][J6] short description`.

---

## 8. Ready check

You are ready to start your jobs when all of these are true:

- [ ] `python scripts/00_detect_system.py` ran and `LOCAL_SYSTEM_INFO.md`'s suggested role matches `Devices.md`
- [ ] `python --version` is 3.12.x inside the venv
- [ ] base import check prints `base OK`
- [ ] your box's extra packages installed and smoke-tested (GPU boxes: **the real kernel launch**, not just `is_available()`)
- [ ] `01_freeze_slice.py --verify` prints `slice reproduces exactly.`
- [ ] `03_export_onnx.py` prints `PASS int8 matches fp32 on retrieval`
- [ ] `pytest` shows 90 passed
- [ ] you know your job IDs from [`Phase3-Parallel.md`](Phase3-Parallel.md) §2

If any box fails a check, say so in the team channel before starting work. A box that quietly proceeds past a failed `--verify` corrupts the comparison table for everyone.
