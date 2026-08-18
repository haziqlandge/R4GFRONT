# Devices.md

Machine inventory for team OK4T, added 18 August 2026 at the start of Phase 3.

Until now the project has assumed one machine. It does not any more. This file records what each box is, what it is allowed to produce, and what must never be published from it. `Phase3-Parallel.md` says who does what; this file says what each box *is*.

---

## 1. The three boxes

| Tag | CPU | GPU | Role | Compute class |
|---|---|---|---|---|
| **BENCH** | i5-12400F (6 physical / 12 logical) | GT 710 | Reference, lexical, eval, integration | CPU only |
| **EMBED** | Ryzen 7 | RTX 3060 Ti, 8 GB | Dense embedding factory | CUDA, sm_86 |
| **LLM** | Intel Core Ultra 9 | RTX 5070 Ti, 16 GB | Local LLM generation, whole-passage encode | CUDA, sm_120 |

**GT 710 is not a GPU for this project.** It is Kepler-class, 2 GB, and below the compute capability floor of any current PyTorch or onnxruntime-gpu wheel. Treat BENCH as CPU only and do not spend an hour discovering this at 2 AM.

### Why BENCH is the reference box and stays so

Every number in `bench/results/` and every figure in `Memory.md` Phase 2 was measured on the i5-12400F. `ONNX_THREADS_BUILD=8` was tuned against its 6 physical cores (`ISSUES.md` I6). `Latency.md` §6 fixes the measurement environment. Adding two machines does not change any of that; it means BENCH keeps its identity as the only box whose local numbers are comparable to the ones already committed.

**Nothing about latency is published from EMBED or LLM. Ever.** Not as a comparison, not as a footnote. `ISSUES.md` I8 already says published figures come from the deployed GCP instance; three dev boxes make that rule more important, not less.

---

## 2. What transfers between boxes and what does not

This is the whole basis of the split. Get it wrong and the Phase 3 comparison table is worthless.

| Quantity | Transfers? | Why |
|---|---|---|
| Frozen slice (`passages.parquet`, `queries.parquet`) | **Yes, exactly** | Seeded and content-hashed. `01_freeze_slice.py --verify` proves byte-level identity. |
| Recall@10, MRR@10, nDCG@10, Hit@1 | **Yes** | Functions of the slice and the model only. If these differ across boxes, something is genuinely broken. |
| Chunk counts, index size, tokens embedded | **Yes** | Properties of the strategy, not the hardware. |
| **Wall-clock build time** | **No** | A GPU-built index and a CPU-built index are not comparable. See §3. |
| **Any latency percentile** | **No** | Different CPU, different answer. This is already `Latency.md` §6. |
| Thread-count tuning | **No** | 8 was tuned on 6 physical cores. Re-measure per box against real C1 chunks, never against a synthetic workload (`ISSUES.md` I6). |

### The reproduction contract

Every box runs the same four commands before doing any Phase 3 work, and every box must land on the same three checkpoints from `HANDOFF.md` §2.3:

```
00_download_dataset.py     -> 440 MB, 97,941 rows
01_freeze_slice.py         -> 15,000 queries, 295,890 passages
01_freeze_slice.py --verify -> records_sha256 starts 7f9f7c59
03_export_onnx.py          -> PASS int8 matches fp32 on retrieval
```

**If `--verify` does not pass on a box, that box does not build indexes.** An index built against a near-identical slice produces plausible-looking retrieval numbers that are wrong, which is the failure mode `Rules.md` §5 exists to prevent.

Nothing under `artifacts/` is transferred between machines. Each box regenerates it in about 10 minutes plus the index build. Moving 655 MB files over a home network is slower than rebuilding and gives you a second thing to get wrong.

---

## 3. Build time stops being a comparison metric

`Phases.md` Phase 3 lists "index build time" as one of four columns in the comparison table. With eight strategies split across three machines and two backends, that column now compares hardware rather than strategies.

**Replacement, effective Phase 3:** the cost columns become

| Column | Machine-invariant? |
|---|---|
| chunks emitted | yes |
| tokens embedded (sum of chunk token counts) | yes |
| index.bin size, MB | yes |
| resident RAM estimate at serve time | yes |
| wall-clock build, tagged `device=EMBED backend=cuda-fp16` | no, informational only |

`meta.json` gains three fields so every index self-describes where it came from:

```json
"build_env": {
  "device_tag": "EMBED",
  "backend": "cuda-fp16",
  "gpu": "RTX 3060 Ti",
  "threads": 8
}
```

The honest sentence for the README and for Video 1: *"index build time is reported per device because the builds were parallelised across three machines; the strategy comparison is made on chunk count, index size and retrieval quality, all of which are hardware-independent."* That is a stronger statement than a fake single-machine timing table, and it is consistent with `Rules.md` §1's no-dishonest-measurement rule.

---

## 4. Per-box setup

Everything in `HANDOFF.md` §2 still applies on every box: Python 3.12, `.venv`, `requirements-dev.txt`. The additions below are per box.

### 4.1 BENCH (i5-12400F)

Nothing extra. This box is already the working configuration. Do not install CUDA anything on it.

New Phase 3 dependency, CPU only:

```
pip install bm25s indic-nlp-library
```

### 4.2 EMBED (Ryzen 7 + 3060 Ti)

sm_86 is the best-supported CUDA target there is; this box should come up first and it is the one to trust.

```
pip install torch --index-url https://download.pytorch.org/whl/cu124
pip install sentence-transformers
```

`Rules.md` §3.1 explicitly allows `sentence-transformers` **for offline index building and eval only**. It does not go anywhere near `services/rag_core` at request time. That boundary is not negotiable and it is what keeps this within the rules rather than a deviation needing a `Memory.md` justification.

Smoke test before anything else, and it must print `True` plus the device name:

```python
import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))
```

### 4.3 LLM (Core Ultra 9 + 5070 Ti)

**This box carries the only real setup risk in the plan.** The 5070 Ti is Blackwell, sm_120. It needs CUDA 12.8 or newer and a matching PyTorch build; older wheels will install cleanly, then fail at the first kernel launch with `no kernel image is available for execution on the device`, which reads like a broken install rather than an architecture mismatch.

```
pip install torch --index-url https://download.pytorch.org/whl/cu128
```

**Timebox the setup to 45 minutes.** Run the same `torch.cuda.is_available()` smoke test, then a real forward pass, not just the availability check. If it is not working in 45 minutes:

**Fallback:** swap the EMBED and LLM roles. Move the C4 LLM pass to the 3060 Ti with a smaller model (an 8 GB card runs a 3B instruct model at 4-bit comfortably), and give the 5070 Ti box the embedding work only if its CUDA stack later comes up, or run it CPU-only on the Ultra 9, which is a strong CPU in its own right. Nothing in the split depends on *which* GPU does which job, only on there being two of them.

Serving stack for the C4 pass, in preference order:

1. `vllm` if it installs cleanly on cu128, for continuous batching. This is the throughput option and the one that makes C4 finish overnight.
2. `llama.cpp` server with a GGUF, which is far more forgiving about a new architecture and is the safer choice if vLLM fights back.

Model: a 3B to 7B instruct model, 4-bit. Bigger is not better here. The C4 task is "restate this passage as standalone atomic facts", which is a rewriting task, not a reasoning task.

---

## 5. Groq is not a build resource

`ISSUES.md` I7: the free tier is 12,000 tokens per window. `Phases.md` Phase 3 originally specified C4 as an "offline LLM decomposition, run it overnight on the slice". At roughly 80 output tokens per passage across 295,890 passages, that pass needs about 24 million output tokens. Groq's free tier supplies 12,000 per window.

**C4 through Groq is not slow. It is arithmetically impossible.**

This is the single strongest reason the three-box split is the right move rather than a nice-to-have. The 5070 Ti turns C4 from impossible into an overnight job, and it does so while *preserving* the Groq quota for the thing it is actually scored on: the Phase 5 generative fallback path and the roughly 50-query Band B benchmark.

**Standing rule from here:** Groq tokens are spent on the runtime fallback path and on Band B measurement. No offline corpus processing touches Groq. If an offline job needs an LLM, it runs on the 5070 Ti.

---

## 6. RAM is the constraint that decides the winner

`ISSUES.md` I4: one C1 index is 655 MB of `index.bin` plus 50 MB of chunk metadata, and the full serving footprint for one strategy is about 1.16 GB. The GCP box is `n2-standard-2` with 8 GB.

Strategies that emit more chunks scale that footprint linearly. C2 sentence-window and C4 propositions plausibly emit two to three times C1's chunk count, which puts them at 1.3 to 2 GB of `index.bin` alone.

**Therefore Phase 3's decision is not "which strategy has the best Recall@10".** It is "which strategy has the best Recall@10 *among those that serve inside 8 GB alongside the embedder, the reranker, BM25 and the passage store*". A strategy that wins on recall and does not fit is not a winner, it is a finding for the README.

Record the projected serving footprint in every `meta.json`, and treat it as a filter applied before the recall ranking, not after.
