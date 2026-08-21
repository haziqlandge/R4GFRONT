# DONT-FORGET.md

Things that are true, easy to get wrong, and expensive to rediscover. Written
while reading the repo end to end on **20 August 2026**. Each entry names the
file that proves it, so nothing here has to be taken on trust.

This is not a summary of the project — `Memory.md` is that. This is the set of
facts that a session, a teammate or a judge is most likely to get *wrong*.

---

## 1. Do not start the services. They are already running.

`rag_core` on :8000, `stt_gateway` on :8001, static site on :3000, started by
`run-dev.bat` or `frontends\serve.bat` in their own windows.

Starting a second copy fails in two confusing ways and neither says "already
running":

- `stt_gateway` cannot bind a port that is taken.
- `rag_core` dies with a bare **`MemoryError`** inside `pyarrow`'s `to_pylist()`
  at `retrieval/dense.py:41`, because the 655 MB index is already resident and a
  second copy does not fit.

Check liveness instead: `http://127.0.0.1:8000/health` and `:8001/health`.
A healthy core reports `status`, `chunks`, `strategy`, `reranker`,
`passage_store`, `generative` and `startup_seconds` — read it before trusting
any answer, because a dense-only process and a fully-reranked one are **both
"ok"** and answer differently.

Verified 20 Aug: `chunks 379240, strategy c1, reranker multi, passage_store
exact, generative true, startup 6.6 s`.

---

## 2. C3 IS built now. C4 is not, and will not be.

**Changed 21 August 2026.** This section used to read "C3 was never built.
Neither was C4." Half of that is now wrong, and the correction matters because
the old sentence was itself a warning against "fixing" the site to claim C3 had
been tested. It has been tested. **Seven strategies were built, indexed and
measured: C1, C2, C3, C5, C6, C7, C8.**

C3 was time-boxed out of Phase 3 with three days to freeze — a schedule
casualty, not a costed impossibility. It cost **61 minutes** to build once
someone had an hour: ~927,000 sentences embedded in 29.9 min, 346,383 chunks
embedded in 30.2 min, HNSW in 1.2 min. `services/rag_core/chunking/c3_semantic.py`.

**C4 remains unbuilt and killed on its own cost model** — 23.7 M output tokens,
7 to 18 days on the hardware available (`ISSUES.md` I22). Its reversal condition
is a working CUDA box with continuous batching, which would make it a few hours
rather than days. That is a different decision from C3's and must not be
collapsed into it: C3 needed an hour of CPU, C4 needs an LLM.

**And C3 lost.** Paired against C1 on the same 500 dev queries, 4000 bootstrap
resamples (`bench/results/2026-08-20-190027-comparison-j15.json`):

| | C1 | C3 | delta | 95% CI | significant |
|---|---|---|---|---|---|
| en Recall@10 | 0.878 | 0.848 | **-0.030** | [-0.054, -0.006] | **YES** |
| hi Recall@10 | 0.714 | 0.660 | **-0.054** | [-0.078, -0.030] | **YES** |
| en Hit@1 | 0.356 | 0.362 | +0.006 | [-0.026, +0.040] | no |
| hi Hit@1 | 0.226 | 0.236 | +0.010 | [-0.014, +0.034] | no |

**Do not quote the Hit@1 numbers as a C3 win.** They point the other way from
recall and neither is significant; the confidence intervals span zero
comfortably. The only significant differences are losses.

**Why it lost, which is the interesting part.** C3 cuts at sentence boundaries
and its chunks do not overlap. C1 is a 96-token window with 24 tokens of
overlap, so text near a boundary appears in TWO C1 chunks and only one C3 chunk
— C1 gets more chances to match. C3 has 346,383 chunks against C1's 379,240,
8.7% fewer, and 56 MB less index.

So on this corpus **the overlap is doing the work, not the boundary placement.**
Semantic coherence is the entire thesis of the strategy and it bought nothing
measurable. That is a real finding about short passages: at p50 48 words and
3.14 sentences, there is not enough document for "follow the meaning" to have
anything to follow.

`registry.implemented()` returns seven. `registry.pending()` returns C4 alone.

---

## 3. C5 and C6 WERE built and run. They are also not independent evidence.

Both facts are true at once and dropping either one is a misreport.

- Built and run: their own dated result files
  (`bench/results/2026-08-18-200054-retrieval-c5.json`, `-200059-retrieval-c6.json`),
  their own index directories, their own rows in the J15 paired comparison
  (`2026-08-19-000658-comparison-j15.json` contains **six** strategies), and 20
  tests in `tests/test_derived_chunkers.py`.
- Not independent: `registry.REUSES_C1_VECTORS = {"c5", "c6"}`. Their meta
  records `derived_from: c1, reused_vectors: 379240,
  chunk_ids_verified_identical: true`. They change the payload and the parent
  lookup, never the vectors, so equal scores are a **design property** and the
  exact zero delta is the evidence that they did what they claim.

The site now shows all six rows with C5 and C6 marked `reuses C1` and dimmed.
Dropping the rows hides work that was done; showing them unmarked pads C1's
column with its own reflection. See `ISSUES.md` I21 and `Memory.md` Phase 3.

---

## 4. C7 had a leaky variant that would have won the whole project. (I20, P0)

The single best story in the repo, and **it is not on the site yet.**

`Phase3-Parallel.md` J4 sizes C7 at "roughly 30,000 extra vectors" — every query
in the slice. But `bench/queries_250.jsonl` **is** the `test` split, ids match
exactly. Indexing a test query's text against its own gold passage puts the
answer key in the index: searching that query then matches a vector that *is*
the query, pointing at the passage being scored.

Both variants were built and measured on the frozen 250:

| | en R@10 | en Hit@1 | hi R@10 | hi Hit@1 |
|---|---|---|---|---|
| c1 baseline | 0.896 | 0.340 | 0.696 | 0.252 |
| c7 honest | 0.872 | 0.336 | 0.656 | 0.228 |
| **c7 leaky** | **0.972** | **0.808** | **0.936** | **0.792** |

The leak is worth **+0.47 Hit@1 English, +0.54 Hindi**. It would also have
appeared to close the Hindi gap (I5) — Hindi Hit@1 more than triples — so it
would have read as the best result in the project rather than as contamination.
`Memory.md` A5 *predicted* C7 would win. It would have "confirmed" A5.

Deeper finding: the split filter does not rescue the strategy. Real doc2query
indexes *synthetic* queries. This corpus gives each passage group exactly one
real query, and for an evaluated passage that query IS the evaluation query. So
either the query is indexed (leakage) or the evaluated passage is unaugmented
(no effect). **A5 is untestable on this dataset** — not confirmed, not refuted.

Guards in place: `c7_doc2query.py` defaults to `SAFE_SPLITS = {corpus_only}`;
any opt-in stamps `leaky: true` into `params()` and `meta.json`; `--leaky`
writes to `artifacts/indexes/c7-leaky/` and never over the canonical `c7/`.

`Rules.md` 1 says this goes in the published write-up as a finding. It is now on
the documentation page, in the chunking section under the comparison table.

---

## 5. Port 3000 is load bearing, and the failure looks like a broken microphone.

`services/stt_gateway/config.py` allows CORS from `localhost:3000` and
`127.0.0.1:3000` only, because that process holds the Sarvam key and a wildcard
origin on a credential-holding service is not acceptable (`Rules.md` 4, HARD).
`rag_core` holds no key and is permissive.

So on any other port **typing works and speaking fails**, with a CORS rejection
that reads exactly like a dead mic. Same class of trap: the microphone needs a
secure origin, `localhost` counts and `192.168.x.x` does not, so on a LAN
address the mic silently never prompts. On the deployed box this means HTTPS is
mandatory.

Whatever origin Phase 7 deploys to must be added to that allow list.

---

## 6. The 200 ms claim is Band A only, and the boundary must stay on screen.

- **Band A** — transcript in to response serialized. **95.89 ms P50 en,
  115.88 ms hi**, measured through the DEPLOYED service on 20 Aug. P100 183.35 /
  182.20, and **0 of 998 requests over 200 ms**. Inside budget.
  The 59.99 / 73.77 figures that used to sit here are the development machine and
  must not be quoted as the product's latency (section 12A).
- **Band B** — Band A routed through Groq. **643.83 ms P50.** Over budget, and
  published anyway.
- **Band C** — full wall clock. Speech to text measured **527 to 911 ms**
  through the TTS loopback and **705 to 1016 ms** from a real microphone
  (section 8). Quote both, not the friendlier one.

250 frozen queries x 2 passes per language, 30 warmup runs discarded,
`time.perf_counter_ns` inside the process, `numpy.percentile method=nearest`,
P100 is the true maximum. Measured on the deployed `n2-standard-8` in Mumbai at
4 uvicorn workers x 2 ONNX threads, which is what `Latency.md` 6 requires and
what `ISSUES.md` I8 closed against us until it was fixed.

A 200 ms claim that quietly excludes speech-to-text reads as cherry-picking,
which is worse than being slower. The site keeps `pipeline` and `speech` as two
separate readouts for exactly this reason. Never merge them.

---

## 7. The abstention floor is an out-of-domain detector, NOT a grounding
detector. (I26, P0)

`tau_low = -1.103` catches **100%** of off-topic and gibberish input. It also
lets **92.5% of wrong top-1 answers through**, and **62.1%** of what the system
answers is wrong under strict labelling.

This is the single most over-claimable number in the project. The correct
sentence is "it knows when the question is outside the corpus", never "it knows
when it is wrong". The site's guardrail section says all of this with the numbers
attached — keep it that way.

**And the 62.1% is itself misread in the other direction.** It measures the exact
`is_selected` label. Measured 21 Aug (`ISSUES.md` I33): **75% of the answers it
counts as wrong retrieve a passage from the same query candidate set** — right
topic, wrong labelled position — and under a topical target 86% are right against
43% under strict gold. That does not license "the system is 86% correct", and
I33 is careful about why. It does mean **"62% of answers are useless" is wrong**,
and it is the misreading a reader is most likely to arrive at unaided.

**Updated 20 Aug: the output guard now exists** (`guardrails/output_guard.py`,
live in the pipeline) and it is the layer that reads the answer rather than the
scores. **It does not close the 62.1%, and do not say that it does.** It scores
lexical overlap against the cited passages, and measured on a worked example a
FALSE sentence reassembled out of the passage's own words scores **0.833** while
a TRUE paraphrase scores **0.639** — the false one is higher. It reliably catches
an answer that is about something else (0.062) and it does not adjudicate claims.
The floor is set at 0.35 for exactly that reason and
`tests/test_output_guard.py` pins the inversion so the character of the measure
cannot drift without a test going red.

---

## 8. The microphone path IS verified now. (20 Aug 2026)

This was the oldest open gap in the project — Phase 4 and Phase 8 both closed
with `getUserMedia` -> AudioWorklet -> resampler unexercised, because the build
box has no microphone and the gateway had only ever been proven by feeding
Sarvam TTS back through STT.

Run for real, in a browser, by a person speaking:

| spoken | speech ms | pipeline ms | path | confidence |
|---|---|---|---|---|
| "What is the capital of Russia?" | 1016 | 65.2 | EXTRACTIVE | 5.01 |
| "Who is Donald Trump?" | 705 | 68.2 | EXTRACTIVE | 10.94 |
| "कतर की राजधानी क्या है?" (hi, **deployed box**) | 366 | 86.0 | EXTRACTIVE | 10.53 |
| "प्रकाश संश्लेषण क्या है?" (hi, **deployed box**) | 798 | 130.8 | EXTRACTIVE | 8.33 |

The last two were spoken into the deployed site on 20 Aug, in Hindi, and both
returned three Hindi citations. They widen the observed speech range downward to
**366 ms** — the earlier "705 to 1016 ms" was two samples and is not a floor.
Four samples is still not a distribution, and Band C still has no percentiles.

Both transcribed exactly, both answered with three citations. The whole capture
chain works, **including the windowed-sinc low pass** — the riskiest file in the
frontend, and the one that could have shipped silently degrading Hindi without
sounding broken.

Two things to carry forward:

- **Real microphone audio costs more than the loopback**: 705 to 1016 ms against
  the loopback's 527 to 911 ms. Band C should quote both ranges, not the
  friendlier one.
- **Cross-lingual retrieval fired on live spoken input.** "Who is Donald Trump?",
  spoken in English, returned a Hindi passage at rank 2 (`1002273:1:hi`, 10.37)
  beside its English twin at rank 1 (10.94). `README.md` has called cross-lingual
  retrieval "a checkable event rather than a demo anecdote" since Phase 1; this
  is the check.

Two samples is a sighting, not a distribution, and the documentation page says so
where it prints them.

---

## 8A. Other traps already paid for, worth not re-paying

- **A2 is false.** Reranking 20 candidates does not fit in 45 ms — depth 20
  measures **249.1 ms P50**, 5.5x over, on a faster CPU than the deploy target.
  Shipped depth is **5**. It was briefly cut to 3 on 20 Aug and put back the same
  day: the cost that forced the cut was `ISSUES.md` I28, not the depth.
- **The English-only reranker is actively harmful in Hindi.**
  `ms-marco-MiniLM-L-6-v2` scores en 0.447 / hi 0.120; no reranking at all
  scores hi 0.233. Shipped `mmarco-mMiniLMv2-L12-H384-v1` at en 0.393 /
  hi 0.307. Our own `Rules.md` named the English-only model as the default.
- **Stage timeouts do not fire for synchronous stages** (I25, P0). A timeout
  cannot interrupt synchronous ONNX work: a 50 ms limit ran 123.7 ms and still
  reported success. This is still true and is why the input guard has to bound
  the input rather than the stage. **Closed for I1 on 20 Aug:** the guard rejects
  the 7,168-character query, and verified against the real tokenizer it accepts
  **499 of 500** frozen benchmark queries and rejects exactly one, `query_id
  156297`, at 2,390 raw tokens. Note that I1 records that query as 512 tokens,
  which is the count *after* the embedder truncates; the raw count is 2,390.
- **int8 cross-encoder scores shift with batch composition** (I24). Scores are
  not stable across differently-composed batches, which matters because a
  threshold is fitted on them.
- **Two degenerate `-` passages act as attractors** (I10) and were dropped;
  `degenerate_dropped: 2` in every index meta is that, not a bug.
- **Non-ASCII through `curl` on Windows silently mangles** (I12). Test Hindi
  through the browser or a Python client, never a Windows shell.
- **Google has TWO Gemini products and only one of them is free.** They are not
  interchangeable and the difference is your remaining credit:

  | | Google AI Studio (`ai.google.dev`) | Vertex AI (`aiplatform.googleapis.com`) |
  |---|---|---|
  | billing account | **not required** | required |
  | the GCP trial credits keeping this site alive | **untouched** | **consumed** |
  | how you get in | a key from `aistudio.google.com/apikey` | enable the API on the project |

  Google's docs: "New accounts begin on the Free Tier... up to the models' free
  tier rate limits", and "AI Studio usage remains free of charge unless users
  link a paid API key." **Get an AI Studio key. Do not enable
  `aiplatform.googleapis.com`** - that bills the same credits that pay for the
  `n2-standard-8`, and `Memory.md` A12 already re-priced that runway once.

  **And there is a THIRD state, which is what the project's key turned out to be
  in on 21 Aug.** A key can be neither free-tier nor billing-enabled but *prepay
  with an empty balance*, which fails differently from both and reads like
  neither: `GET /v1beta/models` returns **200** with all 50 models listed, and
  every generation call returns **429 "Your prepayment credits are depleted."**
  So the key authenticates, the model exists, and nothing generates. Do not debug
  it as an auth problem and do not wait for a rate-limit window to reset - it
  does not clear on its own. The fix is credit in **AI Studio**; adding it
  through Vertex on the GCP project is the trap above, not the fix.

  This is kept even though **there is no Gemini in this project any more**
  (section 14) — it cost a diagnostic cycle once and the next person to try a
  Google key will hit the same three-way fork.
- **Sarvam has TWO speech-to-text sockets and only one of them emits partials.**
  `wss://api.sarvam.ai/speech-to-text/ws` is the legacy streaming endpoint and
  Sarvam's own comparison table gives its interim results as "None; only a final
  transcript per utterance". The realtime one is
  `wss://api.sarvam.ai/speech-to-text-realtime/ws`. `config.py` pointed at the
  legacy host until 20 Aug, which would have produced a live transcript that
  never updated until the speaker stopped - with correct-looking code. The
  realtime endpoint also renames the parameters: `encoding=linear16` rather than
  `input_audio_codec=pcm_s16le`, `stream_type` takes fast/balanced/simulated
  (not `vad`), and segmentation is a separate `endpointing` parameter.
- **`PYTHONIOENCODING=utf-8` is not optional** on Windows: printing a Hindi
  transcript to a cp1252 console raises `UnicodeEncodeError` and kills the
  request rather than garbling one log line.

---

## 9. Two published numbers did not appear in the file the site cited for them

**Fixed 20 Aug** — both blocks now name every file they draw from, and the
reasoning is on the documentation page rather than only in `config.py`. Keep the
underlying facts in mind, because the same trap is easy to walk back into.

Both values were always **correct**. Both were cited to a file that does not
contain them, which is worse than it sounds on a page whose whole pitch is
"every figure names its source": a judge who opens the cited JSON finds a
different number and concludes it was massaged.

### `tau_high` = 1.877 is a deliberate override, not the calibration output

`bench/results/2026-08-19-064809-routing-calibration.json` says
`tau_high: 9.242` and a path distribution of **25% extractive / 70% generative /
5% abstain**. The site publishes **1.877** and **85 / 10 / 5**.

`services/rag_core/config.py` explains why, at length, and the reasoning is
good: top-1 precision never reaches the 0.75 the calibration targeted. It peaks
at 0.508 at 37.4% coverage and falls after.

```
cut     precision   coverage
1.88        0.400      85.0%   <- shipped
4.99        0.433      65.6%
8.09        0.508      37.4%   (peak)
9.65        0.485      20.6%
```

Precision was not bought with coverage, for two reasons: Groq's free tier is
~12 calls per window (I7), so routing 58-70% of traffic there is *inoperable*,
not merely slow; and the extractive path returns a cited passage rather than an
assertion, so Hit@1 undercounts a topically-correct but unlabelled passage that
is still useful to a reader.

**`tau_low = -1.103` IS the calibration output** and matches the file exactly.
Only `tau_high` was overridden.

### The reranker table mixes two runs, and cites the one with fewer of them

`data.js` cites `2026-08-19-012924-rerank-phase5.json`. That file contains only
`dense`, `multi d5` and `multi d10`. The English-only arm (en 0.447 / hi 0.120)
and the depth 20 and 50 rows come from
**`2026-08-19-012200-rerank-phase5.json`**, a different 300-query run — which
also reports `multi d10` at en 0.417 / hi 0.307 against the cited file's
en 0.397 / hi 0.313.

Both runs are legitimate and the shipped conclusion does not change. But one
table assembled from two runs is exactly the failure `ISSUES.md` I21 was raised
about, and the fix is a sentence, not a re-run: name both files and say which
rows come from which.

---

## 10. Findings that existed in the repo and not on the site

- ~~**I20, the C7 answer-key leak**~~ **published 20 Aug**, in the chunking
  section under the comparison table, with the leaky row drawn in the refusal
  colour so it cannot be misread as a result. `Rules.md` 1 asked for it.
- **Retrieval, not ranking, is the remaining ceiling.** On Band B, `gpt-oss-20b`
  handed the top-3 passages returns `INSUFFICIENT_CONTEXT` on **50%** of
  queries: our own LLM, reading the retrieved context, agrees it usually does
  not contain the answer. `Memory.md` Phase 5 says this "should be stated that
  way rather than smoothed over", and it is the opposite of what Phase 3
  concluded.
- **The Groq model in `Rules.md` 3.3 no longer exists.** `llama-3.3-70b-versatile`
  and `llama-3.1-8b-instant` both 404 with `model_not_found` — Groq retired the
  Llama chat lineup for this account between 14 and 19 Aug, which makes the
  14 Aug "verified available" note stale. Shipped model is `openai/gpt-oss-20b`,
  chosen over `gpt-oss-120b` because the 120b emits fullwidth citation brackets
  (U+3010) that would need normalising. `qwen3.6-27b` is unusable: it opens with
  a `<think>` block that eats the whole 160-token cap, and while reasoning it
  quotes the abstention sentinel out of the system prompt, which used to trip a
  false abstention. **Re-check a provider's model list at the start of any phase
  that depends on it rather than trusting a note.**

---

## 11. Phase 6 is PARTIAL, and what that means precisely (20 Aug)

Layers 1 and 4 are built, tested and live. Layer 2 was built as a measurement and
**deliberately not shipped**. Do not "finish" it without reading I27 first.

| layer | state |
|---|---|
| 1, input guard | **live.** empty check, 512-char pre-filter, 64-token bound, injection patterns, unsafe-intent patterns |
| 2, retrieval guard | **the floor only**, in `answering/router.py`. The score-gap and language-mismatch checks are rejected, see I27 |
| 3, generation guard | partial. The grounding system prompt and the abstention sentinel exist in `generative.py`; the schema-repair retry does not |
| 4, output guard | **live.** groundedness plus citation-index validity |

**Measured, 76 adversarial cases, same set before and after a restart:**

| | recall | precision | F1 |
|---|---|---|---|
| before | 0.717 | 0.956 | 0.819 |
| after | 0.750 | 0.957 | 0.841 |

**Recall moved +0.033 and that is not the result.** Two other things did:

- **Refusals are now for the right reason.** Before, all 45 refusals came back
  `LOW_CONFIDENCE`, including every injection and unsafe case. Those were caught
  by accident: a bomb-making question retrieves badly, so the Phase 5 floor
  happened to fire. After: 24 `LOW_CONFIDENCE`, 23 `UNSAFE_INPUT`. Requirement 6
  asks the system to show it knows when not to answer, and "the retrieval score
  was low" is not knowing.
- **Refusing got cheaper.** Median refused-request latency **75.96 ms to
  45.01 ms**, because a blocked input exits in 0.1 to 0.3 ms rather than paying
  for an embedding and a rerank.

**Ambiguity is 25% and stays 25%.** The specified fix costs 4 real questions to
catch 5 ambiguous ones (I27). It is reported per category rather than averaged
into the headline, and it is the honest open weakness of requirement 6.

**Two things about the guards that are easy to get wrong:**

- **The I1 pathological query never reaches the input guard over HTTP.**
  `AnswerRequest.query` carries `max_length=2000` from Phase 2 and that query is
  7,168 characters, so pydantic returns a **422** first. It is bounded, but as a
  transport error rather than a typed refusal. The guard covers 512 to 2,000
  characters plus the token bound inside that range. See decision D-A below.
- **There is no `guardrails/policies.yaml`** and that is deliberate, not missing.
  `Phases.md` asks for one; the thresholds live in `config.py` with the
  measurement that set each one written above it. Section 9 of this file records
  what happened last time this project had two sources for one number. Recorded
  as a Rules.md 9 deviation in the `Memory.md` Phase 6 entry.

---

## 12. Decisions waiting on a human

Written down rather than taken, because each one is a judgement about the
submission rather than about the code. A later session can act on any of them
without re-deriving the context.

### D-A. Should a query over 2,000 characters 422, or abstain?

**State.** `AnswerRequest.query` has `max_length=2000`, frozen in Phase 2 so the
contract would not move under the frontend. Anything longer gets a 422 before
the pipeline runs. The input guard never sees it.

**For leaving it.** Two bounds at different layers is ordinary defence in depth,
and you do not want to accept an arbitrarily large request body. The contract
was frozen for a reason and the frontend is written against it.

**For changing it.** A judge who pastes something enormous sees an error rather
than the abstention panel, which is the one screen requirement 6 is demonstrated
on. Raising `max_length` and letting the guard return `UNSAFE_INPUT` turns an
error into a demonstration.

**Cost of changing it:** one field in `answering/schemas.py`, and the frontend
already renders `UNSAFE_INPUT`, so probably nothing else.

### D-B. Show `groundedness` in the interface, or spend the time on Phase 7?

**State.** The field is populated and reaches the API. `Confidence.groundedness`
has been in the contract since Phase 2 and the frontend does not render it. An
extractive answer reports **1.0**, which is the extractive path's structural
guarantee shown as a number instead of asserted.

**For showing it.** It is the visible half of the phase that was just built, it
costs one row in the answer side panel, and "1.00 grounded" beside a quoted
answer is the strongest single frame in a demo video.

**For Phase 7 instead.** Every published latency number still comes from an
i5-12400F rather than the 2 vCPU `n2-standard-2` target, where they will be
worse (`ISSUES.md` I8). That is a larger honesty gap than a missing readout, and
deployment problems become deadline problems when they start late.

### D-C. Is the ambiguity gap worth one more attempt?

**State.** 25% caught, unchanged by Phase 6, and the specified fix is rejected
on measurement (I27).

**If someone wants to try:** the thing that might work is not a threshold on the
scores but a check on the QUERY — single content word, no verb, no question
word. That is a different signal from the score gap and it was not tested. It
would also refuse a legitimate one-word lookup, so it needs the same control
group treatment the unsafe patterns got.

**The default is to leave it and report it**, which is what the site does now.

---

## 12A. IT IS DEPLOYED, and the 200 ms claim HOLDS there (20 Aug, second pass)

**https://shrutirag.duckdns.org** is live. Valid Let's Encrypt certificate,
`rag_core` and `stt_gateway` under systemd bound to loopback, Caddy on 443
serving the site and proxying `/api/core/*` and `/api/stt/*`. Configs in
`deploy/etc/`.

**Published Band A, measured through the deployed service**, 250 frozen queries
x 2 passes per language, 30 warmup discarded, `n2-standard-8`, 4 uvicorn workers
x 2 ONNX threads, rerank depth 5
(`bench/results/2026-08-20-141232-banda-deployed-FINAL-n2std8-w4t2-d5.json`):

| | P50 | P70 | P90 | P99 | P100 | over 200 ms |
|---|---|---|---|---|---|---|
| en | **95.89** | **103.44** | 117.61 | 152.48 | **183.35** | **0 of 500** |
| hi | **115.88** | **126.17** | 146.54 | 174.62 | **182.20** | **0 of 498** |

**Still do not quote the 59.99 ms development-machine figure as the product's
latency.** The rule that put it here has not changed; what changed is that the
deployed figure is now a good number, so there is no temptation.

### What this section said this morning, and why it was wrong

It said the claim did not hold: `n2-standard-2` measured en P50 190.47 and hi
P50 200.87, and it listed three levers — a bigger instance, rerank depth 5 to 3,
`ef_search` 64 to 48. Two were pulled and the third was not needed. **None of
them was the fix.**

The fix was `ISSUES.md` **I28**: `rag_core` holds two ONNX sessions and gave each
one four intra-op threads on a four-vCPU box. ORT's thread pool spins rather than
sleeping when it finishes, so the embedder was burning cores while the
cross-encoder ran. Giving the embedder **one** thread halved the rerank stage and
made the embedder faster at the same time — en P50 132.59 to 64.48 at depth 3,
from one line.

Three things follow, and each is the kind of mistake worth not repeating:

- **Explain a number before pulling a lever against it.** The cross-encoder cost
  ~18 ms per pair standalone on that box, so depth 3 should have cost ~55 ms and
  the service reported 118. Nobody computed that ratio for six phases. It is now
  lever 0 in `Latency.md` 8.
- **Rerank depth was cut to 3 and put back to 5 the same day.** The cut was a
  correct decision on a measurement of a defect. Depth 5 is better on Hindi
  Hit@1, MRR and nDCG, and it fits.
- **`ONNX_THREADS_SERVING` went 2 → 4 → 2.** The raise tracked the resize and
  made the contention worse; the cross-encoder does not scale past two threads at
  all (17.78 ms per pair at 2, 17.88 at 4), so it bought nothing either way.

### The tail is now a ceiling, not a distribution

The rerank deadline used to ask "have I already overrun?", which cannot bound a
stage that nothing can interrupt: a check passing with 5 ms left still spent a
whole pair. It now refuses to *start* a pair that will not fit. At depth 5 that
took 8 of 998 requests over budget to 0, and the three worst rerank runs in a
250-query pass land at 175.50, 175.66 and 175.51 ms.

It truncates 0.8% of English and 3.2% of Hindi requests to depth 4, recorded in
the trace as `deadline: scored 4 of 5`. Quote that alongside the P100 — a
guarantee held by degrading is a different claim from a guarantee held by being
fast, and the second one is not true.

### Editing the frontend on the box changes nothing until you sync /var/www

The repo lives at `/home/haziqlandge/app` on the VM, but Caddy's root is
`/var/www/shruti` — a home directory is 0750 and the `caddy` user cannot
traverse into it, so serving from `~` returns 403 with nothing useful in the
log. The Caddyfile says `deploy.sh syncs it` and **there is no `deploy.sh` on
the box**; the sync has been done by hand.

The failure is silent and convincing: `scp` succeeds, `grep` on
`~/app/frontends/_shared/data.js` finds the new value, and the site keeps serving
the old one. Verify by fetching the asset over HTTPS and grepping the response,
not by looking at the file you copied. The sync is:

```
sudo rsync -a --delete /home/haziqlandge/app/frontends/ /var/www/shruti/
sudo chown -R caddy:caddy /var/www/shruti
```

Backend files are different: `services/` is run from `~/app` directly by the
systemd units, so `scp` plus `sudo systemctl restart shruti-core` is the whole
deploy there.

### The box is 8 vCPU, and it bought concurrency rather than speed

`n2-standard-2` → `n2-standard-4` → `n2-standard-8`, all on 20 Aug. The last
resize improved single-request P50 only from 102.48 to 95.89, because threads
stopped buying latency at two. What it bought is **workers**: every Band A stage
is synchronous and never yields, so one uvicorn process serves one request at a
time no matter how many cores it has (`ISSUES.md` I29). At four concurrent
clients that was a client-side wall clock of P50 418 ms and P100 698 ms with
Band A completely unchanged — a queue, not a slowdown, and the source of the
"~700 ms" people were seeing. Four workers x two threads takes wall P100 at
four concurrent clients to 416 ms.

**Cost.** `n2-standard-8` is roughly 4x `n2-standard-2`. That is a deliberate
choice for a short judging window, not a permanent shape — `Memory.md` R3 sizes
the runway, and this shortens it. Resize down with
`gcloud compute instances stop rag-core --zone=asia-south1-a`, then
`set-machine-type`, then `start`; the static IP survives, and
`ONNX_THREADS_SERVING` stays at 2 while `--workers` in
`deploy/etc/shruti-core.service` becomes vCPUs/2.

**Voice works on the deployed box now.** `.env` is on the VM (gitignored, copied
by hand). `/api/stt/health` returns `ok`, core `/health` reports
`generative: true`. The section that said otherwise was written before the keys
were copied.

---

## 13. Still open

- ~~**Every published figure is still the i5 number.**~~ **Republished 20 Aug**
  from the deployed box: `data.js` `BANDS` and `STAGES`, `README.md`,
  `Latency.md` 7 and section 6 above. The i5 figures are kept beside the deployed
  ones rather than deleted, labelled as the development machine.
  Two Band A numbers on the page that are still NOT from the deployed box: the
  dense-only baseline (3.25 ms P50, Phase 2, development machine) and Band B
  (643.83 ms P50). Both are labelled; neither has been re-run on the box.
- ~~**`.env` on the VM**~~ **done**, by hand. `/api/stt/health` is `ok` and core
  `/health` reports `generative: true`, so voice and the generative path both
  work on the deployed box.
- **Band B and the path distribution have never been measured on the deployed
  box.** Band B is a Groq network call, so it will be dominated by that rather
  than by the box, but it is still an unmeasured claim on the deploy target.
- **The rerank deadline's truncation rate is measured only at concurrency 1.**
  It fires on 0.8% en / 3.2% hi with one client. Under load the remaining budget
  is smaller, so it will fire more often, and nobody has measured how much more.
- **Band C has four samples, not a distribution.** The mic path works in both
  languages, on the deployed box, verified by a person; how long it takes across
  many utterances is still unmeasured. Observed speech: 366, 705, 798, 1016 ms.
- **There is no correctness signal, and three candidates have now failed.**
  Absolute rerank score AUC 0.606, margin over second 0.586, an LLM
  context-sufficiency judge 0.542, where 0.500 is a coin flip (`ISSUES.md` I31,
  I33). Do not build a fourth without reading those two entries. In particular
  the live external-LLM cross-checker is rejected on measurement, not on taste.
- **Hit@1 is measuring labels as much as retrieval.** 75% of the answers scored
  wrong in the I33 study retrieved a passage from the same query candidate set -
  right topic, wrong labelled position. I26's 62.1% is a statement about exact
  `is_selected` labels and must not be quoted as "62% of answers are useless".
  This is the most likely thing for a reader to get wrong about this project.
- ~~**The aside could be Gemini and probably should be.**~~ **Built and removed
  on 21 Aug.** Do not propose it again without reading section 14 below and
  `ISSUES.md` I35 - the key turned out to be a valid one on an account with no
  credit, and the removal was a decision, not a defect. The aside is Groq only.
- ~~**The `accurate` mode aside calls Groq on every question**, and a judge
  clicking repeatedly can exhaust the shared window for everyone.~~ **Capped
  21 Aug**: five calls per client per minute, sliding window,
  `harness/ratelimit.py`. `ISSUES.md` I35. The aside is still outside Band A and
  outside analytics by construction (I34), and exceeding the cap still shows a
  panel that does not appear rather than an error.
- **The aside still cannot show current facts**, and that is now a permanent
  property rather than a pending task. Groq answers from training-time memory:
  asked the price of bitcoin it replies "I'm not able to provide the current
  price of Bitcoin." Live-web grounding was the one thing Gemini would have
  bought and it is not being bought. The panel is labelled and names its model,
  which is all it claims.
- **The realtime STT relay is built and switched OFF** (`LIVE_TRANSCRIPT` in
  `frontends/_shared/app.js`). Read that constant's comment before turning it on:
  under `language_code=auto` Sarvam streams romanised Hindi partials, and pinning
  the language corrupts the other language's FINAL. `ISSUES.md` I30.
- **The realtime STT socket is BUILT and DELIBERATELY OFF.** `/v1/stt/live`
  exists in `stt_gateway`, the browser client exists in `_shared/core.js`, both
  were tested end to end, and `LIVE_TRANSCRIPT` in `_shared/app.js` is `false`.
  Read that constant's comment before switching it on.
  The reason is Hindi. With `language_code=auto` Sarvam's realtime model streams
  ROMANISED partials and only converts to Devanagari on the final, so a Hindi
  speaker watches "Qatar ki rajdhani kya hai" type out and snap to script at the
  end. Pinning `hi-IN` fixes the partials and **corrupts the English final** to
  `व्हाट इज द कैपिटल ऑफ कतार`, which is the string that would be sent to
  `rag_core`. Every `mode` and both `stream_type` values were tried
  (`scripts/08c_probe_hindi_partials.py`); none of them separates the two.
  **The `Latency.md` 5 speculative prefetch is still NOT built and must not be
  claimed**, and it now has a second problem to solve if anyone tries: under
  auto, the partials it would speculate on are in a different script from the
  final it would be compared against.
- Layer 3's schema-repair retry is not built.
- The adversarial eval is 76 cases. The ambiguous and answerable groups are 12
  and 16, which is enough to decide a direction and not enough to price one.

---

## 14. Gemini was built for the aside and REMOVED. Two other things from that day stayed.

**21 August 2026.** This section replaces one written a few hours earlier that
said the opposite. Read the split carefully, because the surviving half is easy
to credit to the removed half:

| | state |
|---|---|
| `gemini-3.5-flash-lite` as the primary aside, google_search grounded | **built, then removed on the owner's instruction. The key is out of `.env`.** |
| per-client rate limit, 5 aside calls per minute | **shipped and staying** |
| `ASIDE_MAX_TOKENS` 320 -> 240 | **shipped and staying** |
| the panel footer naming its model | **shipped and staying** |

**There is no dormant Gemini path.** `answering/gemini.py`,
`scripts/12_probe_gemini.py` and `tests/test_aside_gemini.py` are deleted;
`GeminiClient`, `Runtime.gemini` and every `GEMINI_*` constant are gone. The aside
is Groq `openai/gpt-oss-20b` and nothing else. If a document elsewhere in this
repo says otherwise, that document is stale and this one is right.

### What the key turned out to be doing, so nobody re-derives it

It was valid and the account had no credit, which looks like neither an auth
failure nor a rate limit:

```
GET  /v1beta/models        -> 200, 50 models, gemini-3.5-flash-lite present
POST /v1beta/interactions  -> 429  "Your prepayment credits are depleted."
```

Both API shapes, both languages, with and without `google_search`. Section 8A
above records this as the **third** Gemini billing state, and it is the one most
likely to be misdiagnosed: a key that lists models authenticates, so "check the
key" wastes the first cycle.

### The one thing that was lost, stated plainly

Search grounding. Groq answers from training-time memory - asked the price of
bitcoin it replies "I'm not able to provide the current price of Bitcoin" - and
Groq cannot ground on search at all. An external source beside a corpus that
peaks in 2017 is worth more if it is current, and it is not current. **That is a
known limit of what ships, not a bug to be fixed at the next opportunity.** The
panel is headed "external source · not from corpus", carries no citation, sits
outside the grounding check, and names its model. It claims nothing more.

### Five per minute, per client - and 20 on the deployed box

`harness/ratelimit.py`, sliding window. This is independent of Gemini and would
have been needed without it: the aside spends the same 12,000-token Groq window
as the real generative fallback (`ISSUES.md` I7), so one visitor clicking
repeatedly takes Band B away from the next one, not just their own panel.

Three things about it that are easy to get wrong:

- **A circuit breaker is not a rate limit.** The breaker reacts to an upstream
  already pushed over; the limiter declines to push it over. The limiter runs
  FIRST, so a capped client never touches the network and therefore never records
  a failure against a breaker protecting other visitors.
- **Per process.** The deployed box runs four uvicorn workers, so the real
  ceiling is **5 x 4 = 20** per client per minute. The number on `/health` is the
  per-worker one.
- **The client is the RIGHTMOST `X-Forwarded-For` hop**, not the leftmost. Caddy
  appends the real peer, so the last entry is the one it wrote. Reading the
  leftmost - which is the usual advice - lets anyone mint a fresh identity per
  request with one header.

Exceeding it is not an error. `{"text": null, "model": null}`, which the page
renders as nothing at all: a throttled visitor sees exactly what a visitor with a
dead upstream sees.

### 320 became 240, and it is the reasoning-token trap for the fourth time

`ISSUES.md` I34 records 160 truncating `gpt-oss-20b` mid-sentence - "Eric Adams
is the" - because it spends the cap thinking before it writes. 240 was
re-measured on that same query and four others, English and Hindi: all five
finish their sentences. **If a Groq aside starts arriving truncated, this is the
first number to look at**, and the trap has now been paid for four times in this
repo (`qwen3.6-27b`, `11_llm_judge.py` at `max_tokens: 8`, the aside at 160, and
this).

### What must not be built on top of this

`ISSUES.md` I33 rejected an external model as a **verifier** on measurement: a
current model disagrees hardest with the answers most FAITHFUL to a 2017 corpus,
so the flag ends up anti-correlated with correctness. **The aside labels; it does
not adjudicate.** Worth noting in the direction this section just moved: live-web
grounding would have made that failure *sharper*, not safer - so "Gemini would be
more accurate" is not an argument for reviving it as a checker.

Before anyone proposes Gemini for the aside again, three things have to be true
and none of them is about code: a funded **AI Studio** project (never Vertex,
section 8A), a second free tier somebody is willing to run out of, and a reason
that survives the paragraph above.

---

## 15. The demo page has TWO views of every number, and `path` cannot tell them apart

**21 August 2026.** The timing panel and the analytics panel each carry a switch
in their title — `timing · model` / `timing · external`, and the same on
analytics. It is not decoration and it is not a toggle somebody added for fun; it
exists because the two numbers were being added together and reported as ours.

### The fact that is most expensive to rediscover

**`AnswerResponse.path` tells you what the user received. It does not tell you
whether the request left the process.** Three outcomes call Groq and then report
a path that is *not* `GENERATIVE`:

| what happened | reported path |
|---|---|
| the model reports `INSUFFICIENT_CONTEXT` | `NONE`, abstained |
| the call fails and extractive is served | `EXTRACTIVE` |
| the output guard rejects the answer | `NONE`, abstained |

Anything keyed on `path` to mean "was this cheap" is wrong on all three, and the
error is ~600 ms. This is the defect I36 records: the browser used
`path === "GENERATIVE" ? "B" : "A"`, so `दुनिया में कितनी भैंसें हैं?` in accurate
mode pinned "Band A P100" above 500 ms for the rest of the session.

**Read the `answer_generative` span's duration instead.** Every version of the
trace carries it. `rag_core` also stamps `called the model` onto that span
(`LLM_CALLED`, one contract shared by `harness/stages.py` and
`_shared/core.js` — do not reword either alone), but that is diagnostics: the
split is arithmetic on a stage duration and works without it.

### Three "stuck percentile" reports that are NOT bugs

This will be reported again. All three are correct behaviour:

- **P100 only ever rises.** It is the session maximum. One routed query sets it
  and nothing brings it down.
- **Nearest-rank percentiles tie at small n.** The index is
  `round(p/100 x (n-1))`, so at n=3 P50 and P70 both land on slot 1 and P90 and
  P100 both land on slot 2 — four cells showing two values. They separate at n=5
  and are all distinct by n=7. Measured.
- **The old external series genuinely did freeze**, and that one *was* a bug: it
  contained only requests that actually routed, so a rotation of the sample
  prompts added nothing to it. Fixed by giving both views the same requests. If
  you see it again, check `n` — if the two views disagree on `n`, the filter is
  back.

### Rules the panel holds to, which look like preferences and are not

- **`external` is disabled in fast mode**, dimmed rather than hidden. Fast calls
  nothing out — the router gates the generative path on `mode`, and the aside is
  requested only in accurate. A control that disappears reads as a bug; one that
  is visibly off reads as a rule.
- **Changing mode clears the session and resets both panels to MODEL, in both
  directions.** Fast and accurate do not produce comparable samples, so carrying
  one into the other builds a distribution out of two different systems.
- **`answer_generative` is listed in the MODEL view at `0.00`, not removed.** A
  missing row looks like an oversight; a zero row is the claim.
- **Both views are graded on the same 200 ms rule.** An earlier pass drew
  external neutral and ungraded — that is backwards for this project, because
  "Band B is over budget and we publish it anyway" is said in words everywhere
  else and the rule is what shows *which* percentiles cross it.

### Two wording rules for the interface

- **Never write "AI" on the page.** It is an **external source**. The word is
  used consistently across the aside panel, the switches and every caption.
- **Each caption has one job and must not restate its neighbours.** The first
  draft had the readout note, the waterfall caption and the analytics caption all
  saying "the same question with the external call counted" in slightly different
  words. They now answer three different questions: what the headline counts,
  which bars are ours, and what the gap between the views means.

### Deploying this

**Frontend-only is sufficient for the correction.** The split reads a stage
duration every trace already carries, verified against a synthetic pre-fix
response (`detail: null` throughout) giving `model 100 ms / external 700 ms`. So
`rsync` to `/var/www/shruti` fixes the panel on the deployed box on its own.

The `rag_core` half — the `LLM_CALLED` stamp, the per-client aside rate limit and
the 240-token cap — needs `scp` plus `sudo systemctl restart shruti-core`, and is
worth doing, but the page is correct either way. Section 12A has both procedures
and the trap that makes the frontend one silent.
