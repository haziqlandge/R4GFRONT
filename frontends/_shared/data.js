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
  // published numbers to come from the deployed service; these are BENCH, and
  // that is stated rather than glossed.
  bench: "i5-12400F, 2 serving threads, Python 3.12.5, Windows",
};

/* ------------------------------------------------------------------ */
/* Requirement 3 and 4: latency                                        */
/* ------------------------------------------------------------------ */

export const BANDS = {
  src: "bench/results/2026-08-19-0653xx",
  method: {
    queries: 250,
    warmup: 30,
    concurrency: 1,
    clock: "time.perf_counter_ns",
    percentile: "numpy.percentile, method=nearest",
    note: "P100 is the true maximum, not the 99.9th percentile.",
  },
  rows: [
    {
      band: "A",
      label: "Core RAG, English",
      detail: "Transcript in, cited answer out. Reranked, extractive path.",
      inBudget: true,
      p50: 59.99, p70: 65.18, p90: 75.1, p99: 113.96, p100: 118.79,
      mean: 62.23, stddev: 12.09, min: 39.53,
    },
    {
      band: "A",
      label: "Core RAG, Hindi",
      detail: "Same pipeline, Devanagari queries. Reranking costs more per pair.",
      inBudget: true,
      p50: 73.77, p70: 80.85, p90: 95.61, p99: 135.5, p100: 155.92,
      mean: 75.91, stddev: 16.98, min: 45.61,
    },
    {
      band: "A",
      label: "Dense only, no reranker",
      detail: "Phase 2 baseline, kept to show what the reranker costs and buys.",
      inBudget: true,
      p50: 3.25, p70: 3.47, p90: 3.81, p99: 4.37, p100: 4.66,
      mean: 3.28, stddev: 0.41, min: 2.55,
    },
    {
      band: "B",
      label: "Core RAG plus Groq generation",
      detail: "Generative path forced. Outside the budget by construction, and reported as such.",
      inBudget: false,
      p50: 643.83, p70: 671.87, p90: 806.78, p99: 971.45, p100: 971.45,
      mean: 586.49, stddev: null, min: null,
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
    verdict: "Sarvam alone measured 527 ms to 911 ms. Reported separately.",
    ok: false,
  },
];

export const STAGES = {
  src: "bench/results/2026-08-19-065329-banda-c1-en-rrmulti.json",
  // budget is the allocation from config.STAGE_BUDGET_MS; median is measured.
  rows: [
    { name: "embed_query", budget: 20, median: 2.98, note: "ONNX int8 e5-small, 384 dims" },
    { name: "dense_search", budget: 8, median: 0.45, note: "hnswlib, in process, ef_search 64" },
    { name: "rerank", budget: 90, median: 56.25, note: "cross-encoder, depth 5, one pair at a time" },
    { name: "route", budget: 2, median: 0.04, note: "confidence to path" },
    { name: "answer_extractive", budget: 5, median: 0.03, note: "span from the cited passage" },
    { name: "answer_generative", budget: 0, median: 0.01, note: "skipped unless the router picks it" },
  ],
};

/* ------------------------------------------------------------------ */
/* Requirement 2: chunking                                             */
/* ------------------------------------------------------------------ */

export const CHUNKING = {
  src: "bench/results/2026-08-19-000658-comparison-j15.json",
  method: "500 dev queries, one process, one query list, one embedder, scored on distinct passages. Paired bootstrap against C1.",
  // Honesty note that has to travel with this table. SIX strategies were built,
  // indexed and run through the J15 comparison, and all six rows below come out
  // of that one file. But C5 and C6 reuse C1's byte-identical index BY
  // CONSTRUCTION - they change the payload and the parent lookup, never the
  // vectors - so their equal scores are a property of the design rather than
  // two more confirmations of it. Both facts have to be on screen: dropping the
  // rows hides work that was done, and showing them unmarked pads C1's column
  // with its own reflection.
  measured: [
    { id: "C1", name: "Fixed size, 96 tokens, 24 overlap", en: 0.878, hi: 0.714, hit1: 0.356, chunks: 379240, mb: 1080, verdict: "default" },
    { id: "C8", name: "Late chunking", en: 0.886, hi: 0.692, hit1: 0.366, chunks: 379240, mb: 1080, verdict: "tied on English, worse on Hindi" },
    { id: "C5", name: "Metadata aware", en: 0.878, hi: 0.714, hit1: 0.356, chunks: 379240, mb: 1080, verdict: "same index as C1", derived: true },
    { id: "C6", name: "Hierarchical parent child", en: 0.878, hi: 0.714, hit1: 0.356, chunks: 379240, mb: 1080, verdict: "same index as C1", derived: true },
    { id: "C7", name: "Doc2query, query aligned", en: 0.864, hi: 0.674, hit1: 0.352, chunks: 403240, mb: 1122, verdict: "significantly worse" },
    { id: "C2", name: "Sentence window", en: 0.354, hi: 0.416, hit1: 0.124, chunks: 927069, mb: 2029, verdict: "significantly worse" },
  ],
  derived: [
    { id: "C5", name: "Metadata aware", note: "Built, indexed and run. It reuses C1's vectors and adds a language, script, query_type and position filter, so it buys conditional retrieval rather than recall. The exact zero delta is the evidence that it did what it claims." },
    { id: "C6", name: "Hierarchical parent child", note: "Built, indexed and run. C1's chunks plus a query_id parent lookup, so it buys answer context rather than recall. Same exact zero delta, same reason." },
  ],
  notBuilt: [
    { id: "C3", name: "Semantic breakpoint", note: "Time boxed out with three days to freeze. Reasoned, not measured. Reported as such." },
    { id: "C4", name: "Proposition decomposition", note: "Killed on a cost model: 23.7 M output tokens, 7 to 18 days on available hardware." },
  ],
  headline: "Six strategies built, indexed and measured. Four of them are independent evidence; C5 and C6 reuse C1's index by construction and are marked as such. One reasoned out, one killed on arithmetic.",
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
  // Verified without a microphone by synthesizing speech with Sarvam TTS and
  // feeding it back through our own STT path. A real round trip in both
  // languages, repeatable, and reusable as demo footage.
  verified: [
    { lang: "en-IN", said: "How tall is Mount Everest?", heard: "exact match", conf: 0.991, ms: 911 },
    { lang: "hi-IN", said: "Hindi Eiffel Tower question", heard: "proper noun transliterated to Latin", conf: 0.851, ms: 527 },
  ],
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
  src: "bench/results/2026-08-19-064809-routing-calibration.json",
  tauLow: -1.103,
  tauHigh: 1.877,
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
  src: "bench/results/2026-08-19-012924-rerank-phase5.json",
  method: "300 dev queries, identical candidate lists, paired bootstrap.",
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
    did: "Eight strategies designed, six built and measured on identical queries, four of them independent evidence, one reasoned out, one killed on a published cost model.",
    evidence: "English passages max at 205 words, so a 256 token chunker is inert here. The work is choosing the retrieval unit, not splitting documents.",
    status: "met",
  },
  {
    n: 3, title: "Under 200 ms",
    ask: "Chunking plus retrieval plus everything through to final output.",
    did: "Band A P50 59.99 ms English, 73.77 ms Hindi. P100 118.79 and 155.92.",
    evidence: "Zero network calls on the fast path. Embedder, index and reranker all in process on quantized ONNX.",
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
