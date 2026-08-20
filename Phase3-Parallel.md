# Phase3-Parallel.md

Phase 3 restructured to run across three machines instead of one. Written 18 August 2026, immediately after Phase 2 closed.

Read `Devices.md` first for what each box is and what it is allowed to publish. This file is the work split, the interfaces, and the merge protocol.

---

## 1. The reframe

The obvious version of this plan is "eight strategies, three people, split them three ways". That is the wrong split, and it would produce merge conflicts and idle GPUs.

The right split is **by the resource each job actually consumes**, because the eight strategies are wildly unequal in what they need:

| Job needs | Strategies | Box |
|---|---|---|
| Bulk GPU embedding of new chunk text | C2, C3, C7 | EMBED (3060 Ti) |
| An LLM, or token-level whole-passage encoding | C4, C8 | LLM (5070 Ti) |
| **Zero new embeddings**, plus CPU lexical work | C5, C6, BM25, fusion, eval | BENCH (12400F) |

C5 and C6 land on the CPU-only box not as a consolation prize but because they genuinely need no GPU. C5 changes the payload and the filter, not the vectors. C6's children can be the C1 chunks that are already embedded and already on disk; its parent layer is a lookup table keyed on `query_id`, not an index. Both are close to free once C1 exists, which is exactly why they belong on the machine that has no GPU.

**The second reframe, and it matters more than the first.** Most of Phase 3 is unattended compute, not human work. Every job below is tagged `[unattended]` or `[attended]`. What the three boxes really buy is not three people working at once; it is Phase 3's builds running overnight on EMBED and LLM **while Phase 4 and Phase 5 proceed on BENCH**. With three days left and `ISSUES.md` I11 recording that the project is already three days behind, that overlap is worth more than the parallelism itself.

---

## 2. Job board

Job IDs are stable. Use them in commit messages: `[P3][J6] proposition shard 3 of 8`.

### 2.1 Blocking prerequisite, owned by EMBED

**J1. Offline GPU embedding backend and its parity gate.** `[attended]`, about 2 hours. Everything else on EMBED and LLM waits on this.

`services/rag_core/retrieval/embedder.py` is hot-path code and `Rules.md` §2.1 bans PyTorch at request time. **Do not touch it.** Add a separate offline-only module, `scripts/_gpu_embedder.py`, exposing the same `encode(texts, kind)` signature.

It must replicate all three of the silent failure modes called out in `embedder.py`'s own docstring, because every one of them degrades recall without raising:

1. The `query: ` and `passage: ` prefixes.
2. Masked mean pooling over `last_hidden_state`, not CLS.
3. L2 normalisation, so `space="ip"` in hnswlib remains cosine.

**The parity gate is not optional and it is the deliverable, not the code.** The index will hold GPU fp16 passage vectors while the served query embedder stays ONNX int8 on CPU. That mixed-precision pairing has to be shown safe, not assumed safe.

The gate has a perfect reference already sitting on disk: the Phase 2 C1 index, built CPU int8, measured at en Recall@10 0.870 / MRR 0.525 / Hit@1 0.362. So rebuild C1 on the GPU over the identical chunk list, evaluate with the identical int8 CPU query embedder, and compare.

```
PASS if  |Recall@10 delta| <= 0.005  and  |Hit@1 delta| <= 0.010, on both en and hi
```

This is the same shape as the int8-versus-fp32 gate in `03_export_onnx.py`, and for the same reason recorded in `Memory.md` Phase 2: raw vector cosine is a meaningless test on this corpus because the rank-10 to rank-11 similarity gap is 0.00137. Compare on retrieval or do not compare.

If the gate fails, the fallback is CPU index builds on all three boxes, which costs roughly 30 minutes per strategy and is survivable. Find that out in hour one, not on the 20th.

### 2.2 EMBED (Ryzen 7 + 3060 Ti)

**J2. C2 sentence-window.** `[attended]` to write, `[unattended]` to build.

Split passages into sentences, embed each sentence, index sentences, and expand to a window of n=2 neighbours at retrieval time. The window expansion lives at retrieval, not at build.

Write the sentence split to `artifacts/sentences.parquet` as a first-class artifact. J3 consumes it.

Indic sentence segmentation is not `text.split(".")`. Devanagari uses the danda (`।`). `indic-nlp-library` is already on the allowlist for exactly this.

**J3. C3 semantic breakpoint.** `[attended]` to write, `[unattended]` to build. Depends on J2. **DONE 21 Aug 2026**, months after the box assignment stopped mattering - built in 61 minutes on BENCH, not EMBED, and it does NOT reuse J2's sentence embeddings because `artifacts/sentences.parquet` was never written. It embeds its own. Result: significantly WORSE than C1 on Recall@10 in both languages. See the reopened Phase 3 entry in `Memory.md` and `DONT-FORGET.md` 2.

Split where consecutive-sentence cosine distance exceeds the 92nd percentile. **It reuses J2's sentence embeddings, which is the entire reason C2 and C3 are on the same box.** Recomputing them would double the cost of both jobs for nothing.

Compute the 92nd percentile over the whole corpus once and record it in `meta.json`, rather than per-passage. Passages here are p50 48 words, so a per-passage percentile over two or three sentence gaps is noise.

**J4. C7 doc2query, query-aligned.** `[attended]`, cheap.

`Architecture.md` §4 flags this as the highest-leverage strategy on this corpus and `Memory.md` A5 predicts it wins. It is also nearly free: the paired MS MARCO query text already exists in `queries.parquet`. Index each query as an additional vector pointing at its passage, on top of the C1 chunk vectors.

> ### 🛑 CORRECTED 19 Aug — the "~30,000 extra vectors" below was a leak
>
> This job originally read *"roughly 30,000 extra vectors on a 379,242 base"* —
> 15,000 queries × 2 languages, i.e. **every** query. **Do not build that.**
> `bench/queries_250.jsonl` **is** the `test` split. Indexing a test query's text
> against its own gold passage puts the answer key into the index: searching that
> query then matches a vector that *is* the query, pointing at the passage it is
> scored on.
>
> Measured, both ways, on the frozen 250 — the leak is worth **+0.47 Hit@1 in
> English and +0.54 in Hindi**, and it also appears to close the I5 multilingual
> gap. A5 predicts C7 wins, so this would have read as confirmation.
>
> **Build only `corpus_only` queries — 24,000 vectors.** `c7_doc2query.py`
> defaults to this (`SAFE_SPLITS`); any opt-in stamps `leaky: true` into
> `meta.json` and `--leaky` writes to `c7-leaky/` so it can never overwrite the
> canonical index.
>
> C7 is already **built and evaluated on BENCH**. Do not rebuild it on EMBED.
> Full reasoning and numbers in `ISSUES.md` **I20**, which also records why the
> split filter cannot rescue the strategy: with one real query per passage group,
> an evaluated passage is either leaked or unaugmented, so **A5 is untestable on
> this corpus** without an LLM generating synthetic queries.

**Verify rather than assume.** A5 is an open assumption, and a prediction that turns out right is only worth something if it could have turned out wrong.

One thing to get right: the query vector must carry the `passage: ` prefix, not `query: `, because it is being indexed as a *representation of a passage*, not used as a search query. Getting this backwards costs recall and raises nothing.

### 2.3 LLM (Core Ultra 9 + 5070 Ti)

**J5. CUDA stack and local LLM server.** `[attended]`, 45-minute timebox, fallback in `Devices.md` §4.3.

**J6. C4 proposition generation.** `[unattended]`, overnight. This is the long pole of Phase 3 and it starts first.

Decompose each passage into standalone atomic factual assertions. Rough sizing: 295,890 passages, about 80 output tokens each, so roughly 24 million output tokens. On a 16 GB card running a 3B to 7B instruct model at 4-bit with continuous batching, that is a few hours, not a few days. It is emphatically not a Groq job (`Devices.md` §5).

Practical requirements, all of which exist because this job runs while nobody is watching:

- **Shard the corpus and checkpoint per shard.** Write `artifacts/propositions/shard_NN.parquet` as each completes. A crash at 90% must not cost the run.
- **Cap output length per passage.** The Hindi repetition-loop pathology from Phase 1 and `ISSUES.md` I1 is in the corpus; feeding a 4,093-word degenerate passage to an LLM invites it to generate a matching degenerate output.
- **Constrain the format.** One proposition per line, no preamble, no numbering. Then validate the parse and count rejects. A generation job with no validity metric is a generation job with an unknown error rate.
- **Log the reject count into `meta.json`.** If 8% of passages produced unparseable output, that number belongs in the comparison table, not in someone's terminal scrollback.

C4 is a genuine risk of producing a *worse* index than C1 while costing far more, because an LLM restating a machine-translated Hindi passage is a lossy pass over an already-lossy text. That is a legitimate and interesting finding if it happens. Do not bury it.

**J7. C4 index build.** `[unattended]`, follows J6. GPU embed of the proposition set. Expect roughly two to three times C1's chunk count, so check the projected serving footprint against `Devices.md` §6 before assuming C4 is even eligible to win.

**J8. C8 late chunking.** `[attended]` to write, `[unattended]` to build.

Encode the full passage once, keeping token-level `last_hidden_state`, then mean-pool per chunk span so each chunk vector carries whole-passage context. Spans should be C1's spans, so C8 versus C1 is a clean single-variable comparison: same spans, different context.

This needs the encoder's pre-pooling output, which the ONNX graph already emits (the existing `Embedder` does the masked mean itself in Python). Apply the 99.5th-percentile length cap from `Architecture.md` §4.1, which is specified and still not implemented.

### 2.4 BENCH (i5-12400F, CPU only)

**J9. Registry and shared-file freeze.** `[attended]`, first 30 minutes, and it blocks nothing but prevents everything.

Land `services/rag_core/chunking/registry.py` mapping all eight strategy names to their classes, with the seven unwritten ones as stubs raising `NotImplementedError`. Extend `scripts/02_build_indexes.py`'s dispatch to read the registry and to accept `--backend {onnx-cpu,cuda-fp16}` and `--device-tag`.

**From that commit onward, `02_build_indexes.py`, `05_eval_retrieval.py` and `registry.py` are owned by BENCH and nobody else edits them.** Everyone else adds exactly one file, `chunking/cN_*.py`. Three people editing the same dispatch function on three branches is the merge conflict that eats an evening you do not have.

**J10. Degenerate passage filter.** `[attended]`, small, `ISSUES.md` I10.

Two passages of 295,890 have text `-` and act as attractors for meaningless queries. Filter empty and near-empty passages at index build time, in the indexer. The frozen slice itself does not change (`Rules.md` §5); the filtered count goes into `meta.json`.

**J11. BM25 lexical index.** `[attended]`, genuinely CPU-bound, no GPU would help.

`bm25s` with Indic-aware tokenisation via `indic-nlp-library`. This is also the most plausible lever on `ISSUES.md` I5, the 0.19 Recall@10 gap between English and Hindi. Watch for the opposite outcome: if BM25 helps English more than Hindi it *widens* the gap, and the multilingual framing in `Project.md` §3 has to be restated honestly rather than glossed.

**J12. RRF fusion, k=60.** `[attended]`, small.

**J13. C5 metadata-aware.** `[attended]`, no new embeddings.

Filters on `language`, `script`, `query_type`, `is_selected_any` and passage `position`, written into the payload for pre-filtered search. Reuses the C1 vectors as-is. Per `Memory.md` R1, `query_type` replaced the non-existent `url` field, and the distribution is usefully spread at 51% DESCRIPTION / 24% NUMERIC.

**J14. C6 hierarchical parent-child.** `[attended]`, no new embeddings.

Children are the C1 chunks already on disk. The parent is the `query_id` passage group, roughly ten passages, returned as generation context. This is a retrieval-time expansion plus a parent lookup table, not a second index.

**J15. Eval harness extension.** `[attended]`. The gate for the whole phase.

Extend `05_eval_retrieval.py` to nDCG@10 alongside Recall@10, MRR@10 and Hit@1, to iterate every registered strategy, and to emit one comparison table with the machine-invariant cost columns from `Devices.md` §3. Ground truth is free from `gold_en_ids` / `gold_hi_ids`.

**J16. Integration, decision, `Memory.md` entry.** `[attended]`. BENCH owns the merge and the write-up. `Rules.md` §7: the phase is not done until the entry exists.

---

## 3. Dependency order

```
J9 registry ─────────────────────────────► everyone unblocked
J1 GPU parity gate ──────► J2 ─► J3
                      └──► J4
                      └──► J7, J8
J5 CUDA up ─────────► J6 (overnight) ─► J7
J10 filter ─► all builds
J11 BM25 ─► J12 fusion
C1 on disk ─► J13, J14
all indexes ─► J15 ─► J16
```

Critical path is **J5 → J6 → J7**. Start it tonight. Everything else has slack; the proposition pass does not.

---

## 4. Merge protocol

Three branches off `main`: `p3-embed`, `p3-llm`, `p3-bench`. Merge into `p3-integration`, then to `main` when the phase exit criterion is met, per `Rules.md` §7.

**Committed:** code, `meta.json`, eval result JSON, `Memory.md` entries.
**Never committed:** anything else under `artifacts/`. `.gitignore` already handles this with `artifacts/*`, and that pattern is deliberate (git cannot un-ignore a file inside an ignored directory, which is how `slice_manifest.json` stays committed).

**Nothing large moves between machines.** Each box regenerates the slice locally in about 10 minutes. Only two files ever travel: a strategy's `meta.json` and its eval JSON, both small, both via git.

The only large transfer in the whole project is the **winning** index going to GCP once, at Phase 7, roughly 655 MB to 1.5 GB depending on which strategy wins. Push it to a GCS bucket in `asia-south1` and pull it onto the VM; do not scp it from a home connection and do not rebuild it on a 2-vCPU box.

Commit format stays `[P3][J6] ...`, and per `HANDOFF.md` §7, no AI attribution in commits.

---

## 5. Exit criterion

Unchanged in substance from `Phases.md`, with two additions forced by the split:

> Eight indexes built, one results table comparing all eight on Recall@10, MRR@10, nDCG@10 and the machine-invariant cost columns, and a documented default with the reasoning in `Memory.md`.

Additions:

1. **The J1 parity gate passed and its numbers are recorded.** Without it, every GPU-built index is unvalidated and the whole split is unsound.
2. **The chosen default fits the 8 GB serving box** alongside the embedder, reranker, BM25 and passage store (`Devices.md` §6). A strategy that wins on recall and does not fit is a README finding, not the default.

---

## 6. Revised schedule

Today is 18 August. Code freeze is 21 August 11:59 PM. That is three and a half days for Phases 3 through 8, against a plan that allocated six. `ISSUES.md` I11 already records the slip; this is what the recovery looks like.

**The recovery is not "work faster". It is overlap.** Phase 3's expensive parts are unattended, so Phases 4 and 5 start before Phase 3 finishes. `HANDOFF.md` §8 already notes those two touch disjoint code: voice is `stt_gateway` and `apps/web`, reranking is `rag_core`.

| When | BENCH | EMBED | LLM |
|---|---|---|---|
| **18 Aug, tonight** | J9 registry, J10 filter | J1 parity gate | J5 CUDA up, **J6 kicked off** |
| **19 Aug, day** | J11 BM25, J12 fusion, J13 C5, J14 C6 | J2, J3, J4 | J6 running, then J7 |
| **19 Aug, eve** | J15 eval harness | builds finishing | J8 C8 |
| **20 Aug, AM** | **J16, Phase 3 closes** | Phase 4 voice | Phase 5 rerank |
| **20 Aug, PM** | Phase 6 guardrails | Phase 4 voice | Phase 7 GCP deploy |
| **21 Aug** | Phase 8 polish, re-bench on GCP. **Freeze 11:59 PM.** | | |
| **22 Aug** | Phase 9: videos, posting, submission | | |

**Cut trigger.** If Phase 3 has not exited by 20 August 12:00, apply the `Phases.md` cut order immediately rather than debating it: C8 and C6 go first. They sit on different boxes (LLM and BENCH), so cutting both frees capacity in two places at once, and six strategies is still comfortably "vast" against requirement 2.

Never cut, per `Phases.md`: the guardrail eval set, the latency benchmark, the deployment, the videos, the posting. Those are scored. Everything else is depth.

---

## 7. Two things to do while this runs

**Record it.** `HANDOFF.md` H5 is the human task most likely to be forgotten, and `Submission.md` §2 wants a genuine failure moment on camera. Three machines building indexes simultaneously, a screen each, is a far better Video 1 shot than a terminal scrolling on one laptop, and it is a *true* shot of how the team actually worked. Film the J1 parity gate specifically. It either passes or it fails, on camera, and both outcomes make good footage.

**Watch the Groq quota.** Every token spent offline is a token unavailable to the Phase 5 fallback path and the Band B benchmark. The whole point of putting C4 on the 5070 Ti is that the free tier stays intact for the part that is actually scored.
