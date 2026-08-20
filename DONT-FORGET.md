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

## 2. C3 was never built. Neither was C4. This is not a documentation lag.

The site says so and the site is right. Three independent proofs:

| evidence | file |
|---|---|
| The class raises on construction | `services/rag_core/chunking/c3_semantic.py` — `SemanticChunker.__init__` raises `NotImplementedError`, every method raises |
| The registry marks it pending | `chunking/registry.py` — `"c3": _Pending("c3", "J3", "EMBED", ...)`, same for `"c4"` |
| No artefact exists | `artifacts/indexes/` holds `c1 c2 c5 c6 c7 c7-leaky c8` and nothing else; no `bench/results/*` file mentions c3 or c4 |

`registry.implemented()` returns the six that are real. **Six strategies were
built, indexed and measured: C1, C2, C5, C6, C7, C8.** C3 was time-boxed out
with three days to freeze; C4 was killed on a cost model (23.7 M output tokens,
7 to 18 days on available hardware). Both are reported as reasoned rather than
measured, which is the correct and defensible claim.

**Do not "fix" the site to say C3 was tested.** It was not.

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

- **Band A** — transcript in to response serialized. **59.99 ms P50 en,
  73.77 ms hi.** P100 118.79 / 155.92. Inside budget.
- **Band B** — Band A routed through Groq. **643.83 ms P50.** Over budget, and
  published anyway.
- **Band C** — full wall clock. Speech to text measured **527 to 911 ms**
  through the TTS loopback and **705 to 1016 ms** from a real microphone
  (section 8). Quote both, not the friendlier one.

250 frozen queries, 30 warmup runs discarded, `time.perf_counter_ns`,
`numpy.percentile method=nearest`, P100 is the true maximum. Measured on an
i5-12400F at 2 serving threads — **not** the deploy target (`ISSUES.md` I8).

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
  Shipped depth is **5**.
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

## 12A. IT IS DEPLOYED, and the 200 ms claim does not hold there (20 Aug)

**https://shrutirag.duckdns.org** is live. Valid Let's Encrypt certificate,
`rag_core` and `stt_gateway` under systemd bound to loopback, Caddy on 443
serving the site and proxying `/api/core/*` and `/api/stt/*`. Configs in
`deploy/etc/`.

**Measured on the deployed box, 250 frozen queries, 30 warmup discarded:**

| | P50 | P70 | P90 | P100 |
|---|---|---|---|---|
| en, i5-12400F | 59.99 | 65.18 | 75.10 | 118.79 |
| **en, n2-standard-2** | **190.47** | **198.31** | 216.12 | 250.90 |
| hi, i5-12400F | 73.77 | 80.85 | 95.61 | 155.92 |
| **hi, n2-standard-2** | **200.87** | 208.98 | 221.72 | 256.57 |

**Do not quote the 59.99 ms figure as the product's latency.** It is a real
measurement on a machine the product does not run on, and `Latency.md` 6 has
always required published numbers to come from the deployed service. I8 is
closed by this and it closed against us.

The box is a 2.80 GHz Xeon with 2 vCPU, meaning one physical core plus a
hyperthread, against a six-core i5 boosting to ~4.4 GHz. The reranker is 94% of
the budget and scales with both. `avx512_vnni` **is** present, so this is not an
int8 fallback.

**Levers, in this order, re-measuring after each rather than stacking them:**

1. Resize to `n2-standard-4`, and change `ONNX_THREADS_SERVING` from 2 to 4 with
   it. Costs money, costs no quality. `N2_CPUS` quota is 200 with 2 in use.
2. Rerank depth 5 to 3. Free, costs quality.
3. `ef_search` 64 to 48. Smallest effect, costs recall.

**Voice does not work on the deployed box yet.** `.env` is gitignored so the VM
has no `SARVAM_API_KEY` or `GROQ_API_KEY`. Text answering works,
`/api/stt/health` returns 503 `no_api_key`, and the generative path is off.

---

## 13. Still open

- **Every published figure is still the i5 number.** `data.js`, the
  documentation page, `README.md` and `Latency.md` results all predate the
  deployment. They must be republished from the deployed box once a lever has
  been pulled and the number settles. See 12A.
- **`.env` on the VM**, without which there is no voice and no generative path.
- **Band C has two samples, not a distribution.** The mic path works; how long
  it takes across many utterances is unmeasured.
- The realtime STT socket (`/v1/stt/live`) is unwired, so partials and the
  `Latency.md` 5 speculative prefetch remain hypothetical and must not be
  claimed.
- Layer 3's schema-repair retry is not built.
- The adversarial eval is 76 cases. The ambiguous and answerable groups are 12
  and 16, which is enough to decide a direction and not enough to price one.
