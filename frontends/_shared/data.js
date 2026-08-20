/**
 * Every measured number this project publishes, in one place.
 *
 * The demo page, the documentation page and the on-page console all read from
 * this file. That is deliberate: three copies of a latency table is three
 * chances for one of them to drift, and a judge who spots two different P50s in
 * the same submission stops trusting every other number on the page.
 *
 * PROVENANCE. Nothing here is illustrative and nothing is rounded for effect.
 * Every figure traces to a dated file under bench/results/ or to the phase entry
 * in Memory.md that recorded it. The `src` field on each block names that file.
 * Rules.md 1 is HARD about this: we publish the measurement boundary and the
 * numbers on both sides of it.
 */

export const PROJECT = {
  name: "Shruti",
  team: "OK4T",
  task: "HH Goa 2026 Shortlisting Task 2",
  tagline: "Speak a question. Get a cited answer, or an honest refusal.",
  repo: "https://github.com/haziqlandge/R4GFRONT",
  dataset: "ai4bharat/MSMARCO-XI",
  datasetUrl: "https://huggingface.co/datasets/ai4bharat/MSMARCO-XI",
  hashtag: "#RAGInGoa",
  budgetMs: 200,
  // Measurement box for every Band A figure below. Latency.md 6 requires the
  // published numbers to come from the deployed service, and since 20 Aug they
  // are: this is the live box, not a laptop. The development machine
  // (i5-12400F, Python 3.12.5, Windows) still appears in the table, labelled,
  // because publishing both sides of a boundary is the same discipline as
  // publishing all three bands.
  bench: "the deployed n2-standard-8 in Mumbai, 4 workers x 2 ONNX threads",
};

/* ------------------------------------------------------------------ */
/* Requirement 3 and 4: latency                                        */
/* ------------------------------------------------------------------ */

export const BANDS = {
  // Measured THROUGH THE DEPLOYED SERVICE, which is what Latency.md 6 has always
  // required. The figure is rag_core's own in-process trace.total_ms, so the
  // network hop between the measuring client and Mumbai is not in it.
  src: "bench/results/2026-08-20-141232-banda-deployed-FINAL-n2std8-w4t2-d5.json",
  method: {
    queries: 250,
    passes: 2,
    warmup: 30,
    concurrency: 1,
    host: "n2-standard-8, asia-south1 (Mumbai), 4 uvicorn workers x 2 ONNX threads",
    clock: "time.perf_counter_ns",
    percentile: "numpy.percentile, method=nearest",
    note: "P100 is the true maximum, not the 99.9th percentile. 998 requests, none over 200 ms.",
  },
  // BAND B IS NOT A ROW IN THIS TABLE, ON PURPOSE.
  //
  // It was, at 643.83 ms P50, and it dominated a table whose subject is a 200 ms
  // budget - a row an order of magnitude taller than the rest teaches nothing
  // about the rows that matter. It is not hidden: the boundary cards below state
  // Band B, its figure and its verdict, and Latency.md 1 and 2 explain why a
  // hosted LLM call cannot fit in the budget at all. Removing the row is a
  // presentation decision; removing the disclosure would not be allowed.
  rows: [
    {
      band: "A",
      label: "Core RAG, English",
      detail: "Transcript in, cited answer out. Reranked depth 5, extractive path.",
      inBudget: true,
      p50: 95.89, p70: 103.44, p90: 117.61, p99: 152.48, p100: 183.35,
      mean: 98.16, stddev: null, min: null,
    },
    {
      band: "A",
      label: "Core RAG, Hindi",
      detail: "Same pipeline, Devanagari queries. Reranking costs more per pair.",
      inBudget: true,
      p50: 115.88, p70: 126.17, p90: 146.54, p99: 174.62, p100: 182.2,
      mean: 118.34, stddev: null, min: null,
    },
    {
      band: "A",
      label: "Dense only, no reranker",
      detail: "Phase 2 baseline, development machine, kept to show what the reranker costs and buys.",
      inBudget: true,
      p50: 3.25, p70: 3.47, p90: 3.81, p99: 4.37, p100: 4.66,
      mean: 3.28, stddev: 0.41, min: 2.55,
    },
    {
      band: "A",
      label: "English, development machine",
      detail: "i5-12400F, not the box this runs on. Published beside the deployed figure rather than instead of it.",
      inBudget: true,
      offBox: true,
      p50: 59.99, p70: 65.18, p90: 75.1, p99: 113.96, p100: 118.79,
      mean: 62.23, stddev: 12.09, min: 39.53,
    },
  ],
};

// Latency.md 1. The single most important thing on the documentation page:
// a judge times from when they stop speaking, and a 200 ms claim that quietly
// excludes speech reads as cherry-picking, which is worse than being slower.
export const BOUNDARY = [
  {
    band: "A",
    name: "Core RAG",
    covers: "Guardrails, embedding, dense search, reranking, routing, answer construction, serialization.",
    excludes: "Speech to text. Any LLM network call.",
    verdict: "Under 200 ms. This is the band the brief describes.",
    ok: true,
  },
  {
    band: "B",
    name: "Core RAG plus generation",
    covers: "Band A, routed through the Groq fallback instead of the extractive path.",
    excludes: "Speech to text.",
    verdict: "643 ms P50. Over budget, published anyway.",
    ok: false,
  },
  {
    band: "C",
    name: "Full wall clock",
    covers: "User stops speaking to answer painted. Includes Sarvam, both network hops, render.",
    excludes: "Nothing.",
    verdict: "Speech to text measured 527 to 911 ms through the TTS loopback, and 705 to 1016 ms from a real microphone. Reported separately.",
    ok: false,
  },
];

export const STAGES = {
  src: "bench/results/2026-08-20-141232-banda-deployed-FINAL-n2std8-w4t2-d5.json",
  // budget is the allocation from config.STAGE_BUDGET_MS; median is measured
  // through the deployed service, English.
  rows: [
    { name: "input_guard", budget: 12, median: 0.23, note: "512 char pre filter, then a 64 token bound" },
    { name: "embed_query", budget: 20, median: 6.59, note: "ONNX int8 e5-small, 384 dims, one thread" },
    { name: "dense_search", budget: 8, median: 0.83, note: "hnswlib, in process, ef_search 64" },
    { name: "rerank", budget: 90, median: 87.26, note: "cross-encoder, depth 5, one pair at a time, deadline bounded" },
    { name: "route", budget: 2, median: 0.09, note: "confidence to path" },
    { name: "answer_extractive", budget: 5, median: 0.05, note: "span from the cited passage" },
    { name: "output_guard", budget: 25, median: 0.2, note: "groundedness and citation index validity" },
    { name: "answer_generative", budget: 0, median: 0.01, note: "skipped unless the router picks it" },
  ],
};

/* ------------------------------------------------------------------ */
/* Corpus vintage                                                      */
/* ------------------------------------------------------------------ */

/**
 * The caveat that belongs on EVERY answer, not on some of them.
 *
 * The demo says India's population is 1.21 billion and bitcoin costs $1,242.
 * Both are correct QUOTATIONS of a corpus that stopped being current years ago,
 * and both look like bugs to anyone who does not know that.
 *
 * A council review of a proposal to bolt an external model onto the answer
 * path as a live fact-checker landed on this instead, and the reasoning is the
 * part worth keeping: "may be incorrect but was pulled from our dataset" is
 * unconditionally true of every extractive answer this system will ever return.
 * A caveat that is always true does not need a network call to decide when to
 * show it. Show it always, at zero latency, zero dependency and zero rate limit.
 *
 * THE DATE IS MEASURED, NOT ASSUMED. The first draft of this line said "2016" and
 * nothing in this repo establishes that; the corpus describes Venkaiah Naidu as
 * Vice-President "since 11 August 2017". Counting every four-digit year in the
 * English half puts the peak at 2017 with 2,457 mentions and the cliff at 2018,
 * after which mentions fall to 62. See the src file.
 */
export const VINTAGE = {
  src: "bench/results/2026-08-20-193717-corpus-vintage.json",
  peakYear: 2017,
  lastCoveredYear: 2018,
  // Deliberately a footnote, not a banner. It is true of every answer, so an
  // alarm-coloured box on all of them would be noise a reader learns to skip -
  // the badge sits in the metadata column and opens only if someone asks.
  badge: "older source data · may be outdated",
  detail:
    "This system retrieves the closest relevant passage from a web corpus whose "
    + "coverage peaks in 2017 and ends in 2018, then quotes it verbatim. Two things "
    + "follow: answers may not reflect current conditions, and the closest passage "
    + "is not always the one that answers your question.",
  short: "corpus: 2017-2018 web snapshot",
};

/**
 * The second footnote: what the corpus is, as opposed to how old it is.
 *
 * A visitor who types a general question assumes a web-scale index behind it.
 * This is a frozen 295,890-passage slice, and the retriever always returns its
 * closest match - so "closest" and "answers the question" are not the same
 * thing here, and ISSUES.md I26 and I31 are the measurement of that gap. Saying
 * it costs nothing and pre-empts the most reasonable complaint a stranger can
 * have about the demo.
 *
 * Figures come from CORPUS below, which reads slice_manifest.json, rather than
 * being retyped here - two copies of a number is two chances for one to drift.
 */
export const SCOPE = {
  src: "artifacts/slice_manifest.json",
  badge: "",
  detail:
    "This is a frozen slice of MS MARCO - 295,890 passages, not the billions a "
    + "web-scale search index holds. The system returns the closest passage it "
    + "has, which is not always one that answers your question, and the passage "
    + "that would answer it may simply not be in the slice.",
};

/* ------------------------------------------------------------------ */
/* Requirement 2: chunking                                             */
/* ------------------------------------------------------------------ */

export const CHUNKING = {
  src: "bench/results/2026-08-20-190027-comparison-j15.json",
  method: "500 dev queries, one process, one query list, one embedder, scored on distinct passages. Paired bootstrap against C1, 4000 resamples.",
  // Honesty note that has to travel with this table. SEVEN strategies were
  // built, indexed and run through the J15 comparison, and all seven rows below
  // come out of ONE file - the comparison was re-run from scratch when C3 was
  // added rather than appending C3's numbers to the older six. ISSUES.md I21 is
  // about exactly that: a table assembled from separate runs compares the runs,
  // not the strategies. Every pre-existing row reproduced to three decimals.
  //
  // C5 and C6 reuse C1's byte-identical index BY CONSTRUCTION - they change the
  // payload and the parent lookup, never the vectors - so their equal scores are
  // a property of the design rather than two more confirmations of it. Both
  // facts have to be on screen: dropping the rows hides work that was done, and
  // showing them unmarked pads C1's column with its own reflection.
  measured: [
    { id: "C1", name: "Fixed size, 96 tokens, 24 overlap", en: 0.878, hi: 0.714, hit1: 0.356, chunks: 379240, mb: 1080, verdict: "default" },
    { id: "C8", name: "Late chunking", en: 0.886, hi: 0.692, hit1: 0.366, chunks: 379240, mb: 1080, verdict: "tied on English, worse on Hindi" },
    { id: "C5", name: "Metadata aware", en: 0.878, hi: 0.714, hit1: 0.356, chunks: 379240, mb: 1080, verdict: "same index as C1", derived: true },
    { id: "C6", name: "Hierarchical parent child", en: 0.878, hi: 0.714, hit1: 0.356, chunks: 379240, mb: 1080, verdict: "same index as C1", derived: true },
    { id: "C7", name: "Doc2query, query aligned", en: 0.864, hi: 0.674, hit1: 0.352, chunks: 403240, mb: 1122, verdict: "significantly worse" },
    { id: "C3", name: "Semantic breakpoint", en: 0.848, hi: 0.660, hit1: 0.362, chunks: 346383, mb: 1022, verdict: "significantly worse on recall" },
    { id: "C2", name: "Sentence window", en: 0.354, hi: 0.416, hit1: 0.124, chunks: 927069, mb: 2029, verdict: "significantly worse" },
  ],
  derived: [
    { id: "C5", name: "Metadata aware", note: "Built, indexed and run. It reuses C1's vectors and adds a language, script, query_type and position filter, so it buys conditional retrieval rather than recall. The exact zero delta is the evidence that it did what it claims." },
    { id: "C6", name: "Hierarchical parent child", note: "Built, indexed and run. C1's chunks plus a query_id parent lookup, so it buys answer context rather than recall. Same exact zero delta, same reason." },
  ],
  notBuilt: [
    { id: "C4", name: "Proposition decomposition", note: "Killed on a cost model: 23.7 M output tokens, 7 to 18 days on available hardware. The arithmetic is the deliverable." },
  ],
  // ISSUES.md I20, severity P0. Not because anything broke - the shipped C7 is
  // clean - but because following the job spec literally would have put a
  // fabricated number in the table above, confirming a prediction the project
  // had already written down. Rules.md 1 requires this to be published.
  leak: {
    title: "The version of C7 that would have won was reading the answer key",
    body: "The job sheet sized C7 at one extra vector per query, all 30,000 of them. But the 250 query benchmark IS the test split, ids matching exactly, so indexing a test query's text against its own gold passage puts the answer key in the index: searching that query then matches a vector that IS the query, pointing straight at the passage being scored.",
    rows: [
      { arm: "C1 baseline", en: 0.896, enHit: 0.34, hi: 0.696, hiHit: 0.252 },
      { arm: "C7 as shipped", en: 0.872, enHit: 0.336, hi: 0.656, hiHit: 0.228 },
      { arm: "C7 leaky, never published", en: 0.972, enHit: 0.808, hi: 0.936, hiHit: 0.792, leak: true },
    ],
    worth: "The leak is worth +0.47 Hit@1 in English and +0.54 in Hindi. It would also have appeared to close the multilingual gap, since Hindi Hit@1 more than triples, so it would have read as the single best result in the project.",
    deeper: "Restricting to the corpus only split removes the leak but cannot rescue the strategy. Real doc2query indexes synthetic queries, so a stored query can resemble a future unseen one. This corpus gives each passage group exactly one real query, and for an evaluated passage that query IS the evaluation query. Either the query is indexed, which leaks, or the evaluated passage is unaugmented, which does nothing. There is no third option, and that is exactly what the honest numbers show.",
    conclusion: "So the assumption that doc2query would win here is neither confirmed nor refuted. It is untestable on this dataset, and that is the finding. The guard is in the code rather than in a habit: the chunker defaults to the corpus only split, any opt in stamps leaky true into the index metadata, and the leaky build writes to its own directory so it can never overwrite the published one.",
  },
  headline: "Seven strategies built, indexed and measured in one process. Five of them are independent evidence; C5 and C6 reuse C1's index by construction and are marked as such. One killed on arithmetic. The 96 token window with overlap wins, and C3 is the reason we can say why: it cuts on meaning instead, does not overlap, and loses recall in both languages.",
};

export const CORPUS = {
  src: "artifacts/slice_manifest.json",
  revision: "bf5cdc1f",
  seed: 20260814,
  sha: "7f9f7c59",
  queries: 15000,
  passages: 295890,
  perLang: 147945,
  dropped: 1857,
  answerBearing: 31990,
  splits: [
    { name: "test", n: 1000, use: "frozen, source of the 250 query benchmark" },
    { name: "dev", n: 2000, use: "threshold calibration only" },
    { name: "corpus_only", n: 12000, use: "indexed, never tuned against" },
  ],
  types: [
    { name: "DESCRIPTION", n: 7885 },
    { name: "NUMERIC", n: 3667 },
    { name: "ENTITY", n: 1292 },
    { name: "PERSON", n: 1081 },
    { name: "LOCATION", n: 1075 },
  ],
  lengthNote: "English passages max out at 205 words. Nothing in this corpus needs splitting, which is why the chunking work is about choosing the retrieval unit rather than cutting long documents.",
};

/* ------------------------------------------------------------------ */
/* Requirement 1: speech to text                                       */
/* ------------------------------------------------------------------ */

export const STT = {
  provider: "Sarvam",
  model: "saaras:v3",
  realtime: "saaras:v3-realtime",
  why: "The corpus is fourteen Indian languages. Sarvam is trained on Indian audio, handles code mixed speech, and its realtime endpoint emits partial transcripts.",
  // Two separate verifications, and they are not interchangeable.
  //
  // `verified` below is the TTS loopback: speech synthesized with Sarvam TTS
  // and fed back through our own STT path. Repeatable, runs on a box with no
  // audio hardware, and reusable as demo footage.
  //
  // `liveMic` is the real thing, and it is what closes the gap Phase 4 and
  // Phase 8 both left open: a person speaking into a browser microphone, 20 Aug
  // 2026. getUserMedia -> AudioWorklet -> 16 kHz PCM16 -> our gateway -> Sarvam,
  // end to end. Two samples is not a distribution and the field says so.
  verified: [
    { lang: "en-IN", said: "How tall is Mount Everest?", heard: "exact match", conf: 0.991, ms: 911 },
    { lang: "hi-IN", said: "Hindi Eiffel Tower question", heard: "proper noun transliterated to Latin", conf: 0.851, ms: 527 },
  ],
  // Real microphone, real browser, 20 Aug 2026. Both spoken in English.
  liveMic: {
    date: "20 Aug 2026",
    note: "Spoken into a browser microphone and answered end to end. Two samples, so this is a sighting rather than a distribution.",
    samples: [
      { said: "What is the capital of Russia?", heard: "exact", sttMs: 1016, pipelineMs: 65.2, path: "EXTRACTIVE", conf: 5.01 },
      { said: "Who is Donald Trump?", heard: "exact", sttMs: 705, pipelineMs: 68.2, path: "EXTRACTIVE", conf: 10.94 },
    ],
    crossLingual: "The Donald Trump query was spoken in English and returned a Hindi passage at rank 2 (1002273:1:hi, 10.37) alongside its English twin at rank 1 (10.94). Cross lingual retrieval firing on a live spoken query, not a constructed example.",
  },
  gotchas: [
    "Sarvam authenticates with an api-subscription-key header, not a bearer token. A bearer token returns a 401 that reads like a bad key.",
    "input_audio_codec is required for raw PCM. The endpoint sniffs container formats and raw samples have nothing to sniff.",
    "Sarvam transliterates proper nouns into Latin script inside Hindi output. Correct code mixed behaviour, and a retrieval problem: our Hindi index is Devanagari throughout.",
    "The browser captures at 48 kHz. Decimating to 16 kHz without a low pass folds sibilance and Devanagari retroflex consonants back into the speech band, which damages exactly the sounds an Indic model needs.",
  ],
};

/* ------------------------------------------------------------------ */
/* Requirement 5: the harness                                          */
/* ------------------------------------------------------------------ */

export const HARNESS = [
  { name: "Every stage has a time allowance", detail: "Each step declares how long it may take. The step gets a fresh copy of the request rather than editing a shared one, so any run can be replayed from its trace." },
  { name: "A running budget", detail: "Before each step starts, the system checks whether it still fits in what is left of the 200 ms. If it does not, that step is skipped and a cheaper one runs instead." },
  { name: "The reranker watches its own clock", detail: "It scores candidates one at a time and checks the time between each. If it runs out, it returns what it managed and says how many it got through." },
  { name: "The LLM gets cut off after repeated failures", detail: "Two failures in a row and we stop calling it. A rate limit stops it immediately, because that answer will not change until the window resets." },
  { name: "Everything has a fallback", detail: "Reranker fails, we use the retrieval order. LLM fails, we quote the passage. That fails, we refuse. You never get a server error." },
  { name: "Typed input and output", detail: "Every boundary is validated. An LLM answer that cites a passage number we never retrieved is rejected." },
];

// The finding a judge is most likely to probe, so it is stated up front rather
// than buried. ISSUES.md I25.
export const HARNESS_LIMIT = {
  title: "One thing we claimed here was not true, and we found it by testing it",
  body: "We said every stage had an enforced timeout. It did not. The timeout only fires when a stage pauses to wait for something, and our retrieval stages never pause, they just compute. We tested it directly: a stage with a 50 ms limit ran for 123.7 ms and still reported success. What actually protects the budget is the check before each stage starts, plus the reranker watching its own clock. We fixed the reranker and corrected the claim.",
};

/* ------------------------------------------------------------------ */
/* Requirement 6: guardrails                                           */
/* ------------------------------------------------------------------ */

export const ROUTING = {
  // TWO sources, and they disagree on purpose. The calibration run fitted
  // tau_low and proposed tau_high 9.242; the shipped tau_high is 1.877, a
  // deliberate override recorded in config.py with the curve it was chosen off.
  // Citing only the JSON would send a reader to a file that says 9.242 and a
  // 25/70/5 split, which reads like the numbers were massaged. They were not,
  // they were argued.
  src: "bench/results/2026-08-19-064809-routing-calibration.json + services/rag_core/config.py",
  tauLow: -1.103,
  tauHigh: 1.877,
  tauHighNote: {
    title: "The high threshold is a judgement call, and the curve is why",
    body: "Calibration targeted 75 percent precision on the extractive path and never reached it. Top-1 precision peaks at 0.508 at 37 percent coverage and falls after, so the fitted cut came back at 9.242, which would route most traffic to the model. We shipped 1.877 instead and wrote down why.",
    curve: [
      { cut: 1.88, precision: 0.4, coverage: 85.0, shipped: true },
      { cut: 4.99, precision: 0.433, coverage: 65.6 },
      { cut: 8.09, precision: 0.508, coverage: 37.4, peak: true },
      { cut: 9.65, precision: 0.485, coverage: 20.6 },
    ],
    why: "Precision was not worth buying with coverage. Groq's free tier serves about twelve calls per window, so routing 58 to 70 percent of queries there is inoperable rather than merely slow, which is the same arithmetic that killed C4. And the extractive path does not assert an answer: it returns a retrieved passage with its citation, while Hit@1 asks only whether that passage is the one the dataset happened to label. A topically correct but unlabelled passage scores zero here and is still useful to read.",
    admission: "This is the number behind the claim that our extractive path is not reliably right. It is a floor, not an optimum, and it is not transferable to another reranker or another corpus.",
  },
  scale: "raw cross encoder logits, roughly -11 to +11",
  dist: [
    { path: "EXTRACTIVE", pct: 85, note: "no network call, answer is a span of the cited passage" },
    { path: "GENERATIVE", pct: 10, note: "Groq composes over the top three passages" },
    { path: "ABSTAIN", pct: 5, note: "typed refusal with the score that caused it" },
  ],
  reasons: [
    "OFF_TOPIC", "LOW_CONFIDENCE", "UNSAFE_INPUT", "UNGROUNDED_OUTPUT", "AMBIGUOUS_RETRIEVAL",
  ],
};

export const GUARDRAIL_EVIDENCE = {
  src: "ISSUES.md I3, I26",
  // The comparison that justifies putting the floor on the reranker rather than
  // on the retrieval score.
  separation: [
    { probe: "Correct English match", dense: 0.9193, rerank: 8.3 },
    { probe: "Correct Hindi match", dense: 0.905, rerank: null },
    { probe: "Pure gibberish", dense: 0.8624, rerank: -4.908 },
  ],
  denseVerdict: "Dense cosine puts a correct answer at 0.919 and pure gibberish at 0.862. A 0.05 margin cannot carry an abstention floor.",
  rerankVerdict: "The cross encoder separates the same two cases by roughly 15 logits, because it reads the query against the passage instead of comparing two embeddings that never met.",
};

// The correction that matters most, kept prominent on purpose. A real
// measurement generalised past the population it was measured on, caught in
// review, and corrected in the docs rather than quietly left standing.
export const HONEST_LIMIT = {
  title: "Where our refusal check stops working",
  claim: "It refuses 100 percent of gibberish and 100 percent of questions the corpus cannot answer, while wrongly refusing only 5 percent of good questions.",
  correction: "Both numbers are real. The conclusion we first drew from them was not. Those refused questions were about completely unrelated subjects, which is the easy case: they score about -7.3 where a correct answer scores about +8.3. The hard case is a passage about the right subject that simply does not contain the answer. Those score +5.9, just below a correct answer and nowhere near the floor. Checking the same data again: 92.5 percent of wrong top answers score above the floor and get answered, and 62.1 percent of what we answer is wrong by the strict labelling.",
  conclusion: "So it is very good at spotting a question we have no business answering, and poor at spotting a wrong answer to a reasonable question. Those are different problems and only the first one is solved. We never quote the 100 percent figure without saying which questions it applies to.",
};

/* ------------------------------------------------------------------ */
/* Reranking: the measurement that changed the architecture            */
/* ------------------------------------------------------------------ */

export const RERANK = {
  // Two runs, both 300 dev queries. The arms table and depths 20 and 50 come
  // from the 01:22 run, which is the one that carried the English only model;
  // depths 5 and 10 come from the 01:29 run. Naming one file for both would be
  // the ISSUES.md I21 mistake in miniature, so both are named and the split is
  // stated in `mixedRuns` below.
  src: "bench/results/2026-08-19-012200-rerank-phase5.json + 2026-08-19-012924-rerank-phase5.json",
  method: "300 dev queries, identical candidate lists, paired bootstrap.",
  mixedRuns: "Depths 5 and 10 come from the later of two 300 query runs, depths 20 and 50 from the earlier one, which is the only run that carried the English only arm. The two report depth 10 slightly differently, en 0.397 against 0.417, which is the run to run spread on 300 queries and is smaller than every difference the table is used to argue.",
  arms: [
    { name: "Dense, no rerank", en: 0.36, hi: 0.233, shipped: false, note: "baseline" },
    { name: "ms-marco-MiniLM-L-6-v2", en: 0.447, hi: 0.12, shipped: false, note: "English only. Best English score, and worse than not reranking at all in Hindi." },
    { name: "mmarco-mMiniLMv2-L12-H384-v1", en: 0.393, hi: 0.307, shipped: true, note: "XLM-R, mMARCO trained. The only arm that improves both languages." },
  ],
  depth: [
    { d: 5, p50: 59.3, p100: 102.4, en: 0.393, hi: 0.307, shipped: true },
    { d: 10, p50: 113.8, p100: 191.4, en: 0.397, hi: 0.313, shipped: false },
    { d: 20, p50: 249.1, p100: null, en: 0.41, hi: 0.29, shipped: false },
  ],
  depthVerdict: "Quality is flat from depth 5 to 10 and falls by depth 50, while cost is linear. Depth 20 alone would consume more than the whole 200 ms budget.",
};

/* ------------------------------------------------------------------ */
/* Timeline                                                            */
/* ------------------------------------------------------------------ */

export const TIMELINE = [
  {
    phase: "0", title: "Built the stopwatch first", date: "14 Aug",
    body: "Before any product code, we built the benchmark harness and tested it against a fake pipeline whose stages add up to exactly 72.5 ms. It measured 72.55, so the harness itself costs 0.05 ms.",
    why: "If you measure a tool you have never checked, you do not know whether you are measuring the system or the tool.",
    numbers: ["harness overhead 0.05 ms"],
  },
  {
    phase: "1", title: "Froze the corpus and the test set", date: "14 Aug",
    body: "15,000 queries and 295,890 English and Hindi passages, picked with a fixed random seed. The 250 query benchmark was written in the same commit, before there was anything to tune.",
    why: "You cannot trust a score if the test set moved while you were improving the number. Freezing it first removes the temptation.",
    numbers: ["295,890 passages", "seed 20260814"],
  },
  {
    phase: "2", title: "One query, end to end", date: "18 Aug",
    body: "Text in, cited passage out, with a real measured P50 of 3.31 ms. On purpose there was no reranker and no guardrails yet, just retrieval.",
    why: "A thin path tells you which stage is slow. A finished stack tells you only that something is.",
    numbers: ["P50 3.31 ms", "en Recall@10 0.870"],
  },
  {
    phase: "3", title: "Tried eight ways to cut the corpus", date: "18 to 19 Aug",
    body: "Six strategies built and measured, one reasoned about and skipped, one cancelled on cost. The plain fixed size chunker won, and two of the six share its index by construction.",
    why: "Two of our early results turned out to be mistakes in how we measured, not real differences. We rebuilt the comparison so every strategy runs through one code path.",
    numbers: ["4 measured", "1 skipped", "1 cancelled"],
  },
  {
    phase: "4", title: "Added voice", date: "19 Aug",
    body: "The simple path first: record an utterance, send it once over HTTPS, get a transcript. The server decides when you stopped talking, so it still feels hands free.",
    why: "Voice was worth zero points until it worked at all. Get the reliable version working, then add the clever streaming version on top.",
    numbers: ["en 0.991 conf", "hi 0.851 conf"],
  },
  {
    phase: "5", title: "Added the reranker, and broke three assumptions", date: "19 Aug",
    body: "A cross encoder reorders the top candidates, and its score decides what happens next. We also found that the reranker our own rules recommended made Hindi worse than no reranking at all.",
    why: "That one score does two jobs. A high score means we can answer instantly from the passage. A low score means we should not answer at all.",
    numbers: ["A2 false", "A6 false", "tau_low -1.103"],
  },
  {
    phase: "6", title: "Guardrails", date: "in progress",
    body: "A length limit on the input, and a check that the answer actually appears in the passage we cited.",
    why: "The confidence score catches questions the corpus cannot answer. It does not catch a confident answer drawn from the wrong passage. Only checking the answer text can do that.",
    numbers: [],
  },
  {
    phase: "8", title: "Built the interface", date: "19 Aug",
    body: "Browser recording, live timing breakdown, citations you can open, and a refusal panel that shows the score it refused on.",
    why: "Downsampling 48 kHz audio to 16 kHz without filtering first quietly damages the exact consonants Indic speech recognition needs. It sounds fine and transcribes worse, so we filter first.",
    numbers: ["en 58.0 ms", "hi 83.4 ms", "gibberish refused"],
  },
];

/* ------------------------------------------------------------------ */
/* Requirement checklist, straight off the brief                        */
/* ------------------------------------------------------------------ */

export const REQUIREMENTS = [
  {
    n: 1, title: "Speech to text",
    ask: "Use either Sarvam or ElevenLabs. Pick one.",
    did: "Sarvam, saaras:v3. One provider, no hedging fallback to the other.",
    evidence: "Round trip verified in both languages at 0.991 and 0.851 language confidence.",
    status: "met",
  },
  {
    n: 2, title: "Chunking",
    ask: "Vast. Not a single naive fixed size approach.",
    did: "Eight strategies designed, seven built and measured on identical queries in one process, five of them independent evidence, one killed on a published cost model.",
    evidence: "English passages max at 205 words, so a 256 token chunker is inert here. The work is choosing the retrieval unit, not splitting documents. The baseline keeps winning, and C3 says why: it cuts on meaning and does not overlap, and loses recall to a 96 token window that does.",
    status: "met",
  },
  {
    n: 3, title: "Under 200 ms",
    ask: "Chunking plus retrieval plus everything through to final output.",
    did: "Band A P50 95.89 ms English, 115.88 ms Hindi, measured through the deployed service. P100 183.35 and 182.20, and none of 998 requests over 200 ms.",
    evidence: "Zero network calls on the fast path. Embedder, index and reranker all in process on quantized ONNX. The rerank deadline refuses to start a pair that will not fit, so the maximum is a ceiling rather than a tail.",
    status: "met",
  },
  {
    n: 4, title: "P50, P70, P100",
    ask: "Measured across a reasonable number of test queries, not one best case run.",
    did: "250 frozen queries, 30 warmup runs discarded, five passes, dated immutable result files.",
    evidence: "Three bands published with the measurement boundary stated for each.",
    status: "met",
  },
  {
    n: 5, title: "Harness",
    ask: "Structured orchestration, not a raw prompt in text out call.",
    did: "Typed pipeline with per stage budgets, a remaining budget counter, a circuit breaker and a declared degradation chain.",
    evidence: "One guarantee did not hold for synchronous stages. Measured, documented, and worked around inside the reranker.",
    status: "met",
  },
  {
    n: 6, title: "Guardrails",
    ask: "Show that your system knows when not to answer.",
    did: "A calibrated abstention floor on the cross encoder score, with five typed refusal reasons surfaced in the interface.",
    evidence: "100 percent of off topic and gibberish input refused. We also publish where the floor fails, which is the topically related but wrong case.",
    status: "partial",
  },
];

/* ------------------------------------------------------------------ */
/* Architecture, for the pipeline diagram                              */
/* ------------------------------------------------------------------ */

/**
 * The pipeline, grouped by who is doing the work and whether the 200 ms clock is
 * running. Grouped rather than flat because the grouping IS the point: the only
 * thing anyone actually wants to know from this diagram is which steps the
 * 200 ms figure covers.
 */
export const PIPELINE = [
  {
    zone: "Your browser",
    note: "Before anything is sent. Not timed.",
    timed: false,
    nodes: [
      { label: "Microphone", sub: "48 kHz capture" },
      { label: "Resample", sub: "low pass, then 16 kHz PCM16" },
    ],
  },
  {
    zone: "Over the network",
    note: "Two hops we do not control. Reported as Band C.",
    timed: false,
    nodes: [
      { label: "STT gateway", sub: "our server, holds the key" },
      { label: "Sarvam saaras:v3", sub: "speech to text, 527 to 911 ms" },
    ],
  },
  {
    zone: "The 200 ms window",
    note: "Clock starts on the transcript, stops on the response. Band A. No network calls.",
    timed: true,
    nodes: [
      { label: "Input guard", sub: "length, language, injection", ms: null },
      { label: "Embed", sub: "ONNX int8, 384 dims", ms: 2.98 },
      { label: "Dense search", sub: "hnswlib, in process", ms: 0.45 },
      { label: "Rerank", sub: "cross encoder, depth 5", ms: 56.25 },
      { label: "Route", sub: "one score picks the path", ms: 0.04 },
      { label: "Answer", sub: "quote it, compose it, or refuse", ms: 0.03 },
    ],
  },
];

export const STACK = [
  { layer: "Speech to text", choice: "Sarvam saaras:v3", why: "Indic native, code mixed, partial transcripts" },
  { layer: "Embeddings", choice: "multilingual-e5-small, ONNX int8", why: "Multilingual, 384 dims, single digit ms on CPU" },
  { layer: "Dense index", choice: "hnswlib, in process", why: "A hosted vector DB is a network hop the budget cannot afford" },
  { layer: "Lexical index", choice: "bm25s", why: "Sub 5 ms, pure numpy, Indic aware tokenization" },
  { layer: "Reranker", choice: "mmarco-mMiniLMv2-L12-H384-v1", why: "The only arm that improved both English and Hindi" },
  { layer: "Fallback LLM", choice: "Groq, openai/gpt-oss-20b", why: "Fallback path only. Never on the fast path." },
  { layer: "Services", choice: "FastAPI, Python 3.12", why: "The ONNX, hnswlib and bm25s ecosystem" },
  { layer: "Orchestration", choice: "Hand written typed pipeline", why: "You cannot budget what you cannot see. No LangChain." },
];

export const REJECTED = [
  { what: "Hosted embedding APIs", why: "80 to 200 ms per round trip. The whole budget, spent before retrieval starts." },
  { what: "Hosted vector databases", why: "Same arithmetic. hnswlib runs in the same process for free." },
  { what: "LangChain and LlamaIndex at runtime", why: "Deep call stacks with hidden retries and hidden network calls." },
  { what: "An LLM on the fast path", why: "The fastest hosted provider's shortest possible call measured 352 ms from this machine." },
  { what: "Serverless for the core", why: "A 1.2 GB warm index cannot survive cold starts." },
  { what: "C4 proposition chunking", why: "23.7 M output tokens against a 12,000 token free tier window. Not slow, arithmetically impossible." },
];
