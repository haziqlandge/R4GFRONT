/**
 * The runtime: microphone capture, the two service clients, and the session
 * analytics store.
 *
 * Rules.md 4 is HARD: no API key ever reaches the browser. Nothing in this file
 * or anywhere under frontends/ holds a credential. The browser talks to our own
 * stt_gateway on :8001, and the gateway talks to Sarvam.
 *
 * PORT 3000 IS LOAD BEARING IN DEVELOPMENT. services/stt_gateway/config.py
 * allows CORS from localhost:3000 and 127.0.0.1:3000 only. Serve these pages on
 * any other LOCAL port and speech to text fails with a CORS error that looks
 * like a broken gateway. serve.bat pins 3000 for exactly this reason.
 */

/**
 * Where the two services live, decided by where this page is being served from.
 *
 * In development the page is on :3000 and the services are on :8000 and :8001,
 * so the browser has to be told about them explicitly and CORS applies.
 *
 * In deployment there is one origin. Caddy serves these files and reverse
 * proxies /api/core/* and /api/stt/* to the two services on localhost, so the
 * browser makes SAME-ORIGIN requests and CORS stops being a thing that can go
 * wrong rather than a thing that has to be configured correctly.
 *
 * This has to be computed rather than hardcoded: a deployed page pointing at
 * 127.0.0.1:8000 would be asking the VISITOR's own machine for an answer, which
 * fails for everyone except whoever is running the stack locally.
 */
const LOCAL_DEV =
  location.hostname === "localhost" || location.hostname === "127.0.0.1";

export const RAG_CORE = LOCAL_DEV ? "http://127.0.0.1:8000" : "/api/core";
export const STT_GATEWAY = LOCAL_DEV ? "http://127.0.0.1:8001" : "/api/stt";

/* ------------------------------------------------------------------ */
/* Service clients                                                     */
/* ------------------------------------------------------------------ */

export async function ask(query, mode = "fast", strategy = "c1") {
  const res = await fetch(`${RAG_CORE}/v1/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, mode, strategy, trace: true }),
  });
  if (!res.ok) throw new Error(`rag_core returned ${res.status}`);
  return res.json();
}

/**
 * Send one recorded utterance for transcription.
 *
 * Raw PCM16, not a container format. The worklet already produces exactly what
 * Sarvam wants and the gateway tags the codec, so wrapping it as WAV here would
 * add bytes and a second format to be wrong about.
 */
export async function transcribe(pcm) {
  const form = new FormData();
  form.append("file", new Blob([pcm.buffer], { type: "application/octet-stream" }), "utterance.pcm");
  form.append("language", "unknown");
  const res = await fetch(`${STT_GATEWAY}/v1/stt/file`, { method: "POST", body: form });
  const body = await res.json();
  if (!res.ok) throw new Error(body?.detail || `stt_gateway returned ${res.status}`);
  return body;
}

/**
 * Open the live transcription socket. Latency.md 5, gateway /v1/stt/live.
 *
 * Partials arrive WHILE the person is still speaking, so the page can show the
 * question forming instead of a spinner. Measured against Sarvam's realtime
 * model before this was written (scripts/08_probe_realtime_stt.py): over 5.8 s
 * of speech, 19 partials, the first at 991 ms, and the final 385 ms after the
 * audio stopped.
 *
 * Returns null rather than throwing if the socket cannot be opened. Voice input
 * is requirement 1 and it already works through /v1/stt/file; this is an
 * upgrade to it, so a failure here has to degrade to that path silently rather
 * than take the microphone down with it.
 */
export function openLiveTranscript({ onPartial, onFinal, onError } = {}) {
  const url = `${STT_GATEWAY.startsWith("http")
    ? STT_GATEWAY.replace(/^http/, "ws")
    : `${location.origin.replace(/^http/, "ws")}${STT_GATEWAY}`}/v1/stt/live`;

  let ws;
  try {
    ws = new WebSocket(url);
  } catch {
    return null;
  }
  ws.binaryType = "arraybuffer";

  let opened = false;
  const pending = [];  // frames captured before the socket finished opening

  ws.addEventListener("open", () => {
    opened = true;
    for (const buf of pending.splice(0)) ws.send(buf);
  });
  ws.addEventListener("message", (e) => {
    let frame;
    try { frame = JSON.parse(e.data); } catch { return; }
    if (frame.type === "partial") onPartial?.(frame.text || "");
    else if (frame.type === "final") onFinal?.(frame.text || "", frame);
    else if (frame.type === "error") onError?.(frame.code, frame.detail);
  });
  ws.addEventListener("error", () => onError?.("socket", "live transcription socket failed"));

  return {
    get ready() { return ws.readyState === WebSocket.OPEN; },
    /** One PCM16 frame straight off the worklet. */
    send(int16) {
      const buf = int16.buffer.slice(int16.byteOffset, int16.byteOffset + int16.byteLength);
      if (opened && ws.readyState === WebSocket.OPEN) ws.send(buf);
      else if (ws.readyState === WebSocket.CONNECTING) pending.push(buf);
    },
    /** End the utterance. The final is still owed, so the socket stays open. */
    end() { if (ws.readyState === WebSocket.OPEN) ws.send("end"); },
    close() { try { ws.close(); } catch { /* already gone */ } },
  };
}

/**
 * The model's own answer, with no corpus behind it. Accurate mode only.
 *
 * Requested AFTER ours has painted, never before, so nothing here is inside the
 * 200 ms band. Resolves to `{ text: null, model: null }` on any failure - a
 * missing aside is a panel that does not appear, and must never be the reason
 * an answer looks broken.
 *
 * RETURNS THE MODEL, NOT JUST THE TEXT. The panel is the one thing on the page
 * with no citation behind it, so the least it can do is name who said it - and
 * `renderAside()` has accepted that argument since Phase 8 with nothing passing
 * it. `model` is null whenever `text` is, which is what a dead upstream and an
 * exceeded rate limit both look like from here.
 */
export async function aside(query) {
  const none = { text: null, model: null, upstreamMs: 0, usage: null, rateLimited: false };
  try {
    const res = await fetch(`${RAG_CORE}/v1/aside`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    if (!res.ok) return none;
    const body = await res.json();
    return {
      text: body?.text || null,
      model: body?.model || null,
      // How long the hosted model itself took, measured server-side around the
      // call alone. The caller also knows the wall clock of the whole request,
      // and the difference between the two is our own transport - which is what
      // lets the external panel separate "the provider was slow" from "the
      // network was slow" rather than reporting one number for both.
      upstreamMs: typeof body?.upstream_ms === "number" ? body.upstream_ms : 0,
      usage: body?.usage && typeof body.usage === "object" ? body.usage : null,
      // The one empty response the page speaks about. Every other reason the
      // panel is missing - no key, dead upstream, open breaker - stays silent,
      // because none of those is something the visitor did or can act on.
      rateLimited: body?.rate_limited === true,
    };
  } catch {
    return none;
  }
}

export async function health() {
  const out = { core: null, gateway: null };
  try {
    const r = await fetch(`${RAG_CORE}/health`);
    out.core = await r.json();
  } catch { /* offline, reported as such rather than thrown */ }
  try {
    const r = await fetch(`${STT_GATEWAY}/health`);
    out.gateway = await r.json();
  } catch { /* same */ }
  return out;
}

/* ------------------------------------------------------------------ */
/* Microphone                                                          */
/* ------------------------------------------------------------------ */

const WORKLET_URL = "/_shared/pcm-worklet.js";
const AMPLITUDE_FLOOR = 0.004;

/**
 * getUserMedia to 16 kHz PCM16, plus the live amplitude the orb reads.
 *
 * Two outputs, deliberately separate: the PCM frames are the data, and the
 * amplitude is the feedback. The amplitude comes off an AnalyserNode on the same
 * graph rather than from the frames, so a delayed frame never freezes the visual.
 */
export class Recorder {
  constructor(opts = {}) {
    this.opts = opts;
    this.state = "idle";
    this.ctx = null; this.stream = null; this.node = null;
    this.analyser = null; this.buf = null;
    this.chunks = [];
    this.smoothed = [0, 0, 0];
    this.smoothIdx = 0;
  }

  get supported() {
    return typeof navigator?.mediaDevices?.getUserMedia === "function"
      && typeof window.AudioContext === "function";
  }

  _set(s) { this.state = s; this.opts.onState?.(s); }

  _fail(msg) { this._set("error"); this.opts.onError?.(msg); }

  async start() {
    if (!this.supported) {
      this._fail("This browser cannot capture microphone audio. Type your question instead.");
      return;
    }
    this._set("requesting");
    try {
      // Browser processing stays ON. It is tempting to disable AGC and noise
      // suppression to hand the model clean audio, but Saaras is trained on real
      // world Indian audio recorded through exactly this kind of pipeline.
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (err) {
      const name = err?.name;
      this._fail(
        name === "NotAllowedError"
          ? "Microphone access was denied. Allow it in your browser, or type a question instead."
          : name === "NotFoundError"
            ? "No microphone found. Type a question instead."
            : `Could not open the microphone: ${err}`
      );
      return;
    }
    try {
      this.ctx = new AudioContext();
      if (this.ctx.state === "suspended") await this.ctx.resume();
      await this.ctx.audioWorklet.addModule(WORKLET_URL);
      const source = this.ctx.createMediaStreamSource(this.stream);
      this.node = new AudioWorkletNode(this.ctx, "pcm-downsampler");
      this.node.port.onmessage = (e) => {
        // Buffered AND streamed. The buffer is what /v1/stt/file gets if the live
        // socket is unavailable, so the fallback path costs nothing to keep.
        this.chunks.push(e.data);
        this.opts.onChunk?.(e.data);
      };
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 1024;
      this.analyser.smoothingTimeConstant = 0;
      this.buf = new Float32Array(this.analyser.fftSize);
      source.connect(this.analyser);
      source.connect(this.node);
      // Deliberately not connected to ctx.destination. Routing the mic to the
      // speakers howls the moment a demo starts on a laptop without headphones.
      this._set("listening");
    } catch (err) {
      this._fail(`Audio pipeline failed to start: ${err}`);
    }
  }

  async stop() {
    this.node?.port.postMessage("flush");
    await new Promise((r) => setTimeout(r, 30));
    const total = this.chunks.reduce((n, c) => n + c.length, 0);
    const merged = new Int16Array(total);
    let at = 0;
    for (const c of this.chunks) { merged.set(c, at); at += c.length; }
    this.chunks = [];
    this.node?.port.postMessage("close");
    this.node?.disconnect();
    this.analyser?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    await this.ctx?.close();
    this.ctx = this.node = this.analyser = this.stream = null;
    this._set(total > 0 ? "processing" : "idle");
    return merged;
  }

  /** Loudness 0..1 for the orb. Three frame moving average. */
  amplitude() {
    if (!this.analyser || !this.buf) return 0;
    this.analyser.getFloatTimeDomainData(this.buf);
    let sum = 0;
    for (let i = 0; i < this.buf.length; i++) sum += this.buf[i] * this.buf[i];
    const raw = Math.sqrt(sum / this.buf.length);
    this.smoothed[this.smoothIdx] = raw;
    this.smoothIdx = (this.smoothIdx + 1) % 3;
    const avg = (this.smoothed[0] + this.smoothed[1] + this.smoothed[2]) / 3;
    if (avg < AMPLITUDE_FLOOR) return 0;
    // Speech RMS lives around 0.02 to 0.2, so a linear map leaves the ring
    // almost motionless. sqrt expands the quiet end where speech actually sits.
    return Math.min(1, Math.sqrt(avg * 6));
  }

  reset() { this._set("idle"); }
}

/* ------------------------------------------------------------------ */
/* Session analytics                                                   */
/* ------------------------------------------------------------------ */

/**
 * Rolling percentiles over the queries run in THIS browser session.
 *
 * Requirement 4 asks for P50, P70 and P100 across a reasonable number of
 * queries rather than one best case run. The published figures come from a
 * 250 query offline benchmark; this is the live counterpart, so a judge can
 * watch the distribution build up rather than take the table on trust.
 *
 * Two honesty rules are enforced here rather than left to each theme:
 *
 *   1. n is always displayed next to the percentiles. A P100 over four samples
 *      is not a tail measurement and should not be presented as one.
 *   2. Band A and Band B samples are kept in separate series. Averaging an
 *      in process extractive answer with a Groq round trip produces a number
 *      that describes neither.
 */
/**
 * The exact detail string rag_core stamps on the answer_generative span when it
 * reached for the external model. Trace contract; see harness/stages.py.
 */
const LLM_CALLED = "called the model";

/** The one pipeline stage that is not ours. */
export const LLM_STAGE = "answer_generative";

/**
 * The external-source round trip, shown as a row beside the pipeline stages in
 * the external view. NOT a real stage - it is a separate request on a separate
 * endpoint, made after our answer has painted - which is why it is named here
 * rather than coming off the trace.
 */
export const EXTERNAL_SOURCE_STAGE = "external_source";

/**
 * The rows the EXTERNAL view is built from.
 *
 * NOT the pipeline's stages. An earlier version listed all eight of ours beside
 * the external call, which read as a claim that the hosted model had done the
 * reranking - `rerank 96.59` under a heading that says EXTERNAL is a sentence
 * about the wrong system. Retrieval, reranking and answer selection are ours and
 * they belong in the model view; this view answers "what did the work we do not
 * control cost, and where did it go".
 *
 *   compose_answer    the model writing from OUR passages, when routed
 *   queue_wait        waiting for a slot at the provider
 *   read_question     the provider reading our question
 *   write_answer      the provider generating the answer
 *   provider_network  the wire to and from the provider
 *   browser_hop       this browser to our own service and back
 */
export const EXTERNAL_ROWS = {
  GENERATIVE: "compose_answer",
  QUEUE: "queue_wait",
  PROMPT: "read_question",
  WRITE: "write_answer",
  WIRE: "provider_network",
  TRANSPORT: "browser_hop",
};

/**
 * The external breakdown for one sample, as [name, ms] pairs.
 *
 * Every row is MEASURED, and three come from the provider's own usage block
 * rather than from anything this project inferred: queue_time, prompt_time and
 * completion_time. provider_network is whatever the provider did not account
 * for, and browser_hop is the rest of the request the browser timed.
 *
 * Measured on one call - 745.6 ms wall, of which 312.4 queued, 4.5 reading,
 * 75.4 writing, and ~353 on the wire - GENERATION IS THE SMALLEST PART. That is
 * not what a reader would guess, and showing it is the point of breaking this
 * out instead of printing one number for the whole trip.
 *
 * Rows are omitted rather than zeroed when the thing did not happen. A row
 * reading 0.00 says "this was free"; an absent row says "this did not occur",
 * and for a call that was never made - not routed, no key, refused by the rate
 * limit - the second one is the true statement.
 */
export function externalRows(sample) {
  const rows = [];
  if (!sample) return rows;

  if (sample.extMs > 1) rows.push([EXTERNAL_ROWS.GENERATIVE, sample.extMs]);

  const u = sample.srcUsage || {};
  const queue = u.queue_time ?? 0;
  const prompt = u.prompt_time ?? 0;
  const write = u.completion_time ?? 0;
  if (queue > 0) rows.push([EXTERNAL_ROWS.QUEUE, queue]);
  if (prompt > 0) rows.push([EXTERNAL_ROWS.PROMPT, prompt]);
  if (write > 0) rows.push([EXTERNAL_ROWS.WRITE, write]);

  if (sample.srcUpstreamMs > 0) {
    // Clamped at zero: the provider's clock and ours are different machines,
    // and a few ms of skew must not be drawn as a negative bar.
    const wire = Math.max(0, sample.srcUpstreamMs - queue - prompt - write);
    if (wire > 0) rows.push([EXTERNAL_ROWS.WIRE, wire]);
  }

  if (sample.srcMs != null) {
    const hop = Math.max(0, sample.srcMs - (sample.srcUpstreamMs ?? 0));
    if (hop > 0) rows.push([EXTERNAL_ROWS.TRANSPORT, hop]);
  }
  return rows;
}

/** ms this request spent inside the hosted model. 0 when it never called it. */
export function externalMs(trace) {
  const s = (trace?.stages || []).find((x) => x.name === LLM_STAGE);
  return s ? s.ms : 0;
}

/**
 * ms this request spent in OUR pipeline: everything except the hosted model.
 *
 * Subtracted from the total rather than summed from the stages, because
 * total_ms is measured around the whole run and includes the overhead between
 * stages. Summing would quietly under-report us, which is the wrong direction
 * for a number this project publishes.
 */
export function modelMs(trace) {
  if (!trace) return null;
  return Math.max(0, trace.total_ms - externalMs(trace));
}

export class Analytics {
  constructor() {
    this.samples = [];  // { ms, modelMs, extMs, srcMs, path, status, stages, ... }
  }

  /**
   * Attach the external-source round trip to the sample it belongs to.
   *
   * THIS IS THE CALL THAT MAKES THE TWO VIEWS DIFFER AT ALL, and leaving it out
   * was the defect. `answer_generative` only runs for a mid-confidence query -
   * measured over 15 accurate questions it fired on 2 - while the external
   * source is asked on EVERY accurate question and costs 259 to 583 ms. So with
   * only the generative stage counted, MODEL and EXTERNAL printed the same
   * number on 13 of 15 questions, while an external answer the panel did not
   * count sat on screen directly above them.
   *
   * It arrives late by construction: the browser asks for it only after our
   * answer has painted, so the sample already exists and is amended in place.
   * Recorded even when it returned nothing - a refused or rate-limited call
   * still spent its time, and dropping those would flatter the figure.
   */
  attachExternalSource(sample, ms, upstreamMs = 0, usage = null) {
    if (!sample || typeof ms !== "number" || !isFinite(ms) || ms < 0) return;
    sample.srcMs = ms;
    sample.srcUsage = usage || null;
    // Clamped to the wall clock it is a part of. A server figure larger than the
    // browser's would make `transport` negative, which cannot have happened and
    // would be drawn as a bar.
    sample.srcUpstreamMs = Math.max(0, Math.min(ms, upstreamMs || 0));
  }

  /**
   * What the work we do NOT control cost on this request.
   *
   * External work only - the generative call plus the external-source request -
   * and deliberately not `total_ms + srcMs`. The two views are disjoint: MODEL
   * is our pipeline, EXTERNAL is theirs, and they sum to the wall clock. An
   * external figure that silently included our retrieval and reranking was the
   * same category error as listing `rerank` among its rows.
   */
  static externalTotal(s) {
    const gen = s.extMs > 1 ? s.extMs : 0;
    return gen + (s.srcMs ?? 0);
  }

  /**
   * Did this request leave the process?
   *
   * This is the whole basis of the Band A / Band B split, and `path` cannot
   * answer it. THE BUG THIS REPLACES: the band was `path === "GENERATIVE" ? "B"
   * : "A"`, and three outcomes call the model and then report a path that is not
   * GENERATIVE - the model reporting insufficient context (ABSTAIN, path NONE),
   * the call failing (path EXTRACTIVE), and the output guard rejecting the
   * answer (ABSTAIN, path NONE). All three landed a ~600 ms network sample in
   * the core pipeline's percentiles.
   *
   * P100 is where that showed, and it never recovered: it is the session
   * maximum, so one Hindi query routed to the model pinned "Band A P100" above
   * 500 ms for the rest of the session no matter how many fast queries followed.
   *
   * The `path` check is kept as a second signal so an older core that does not
   * stamp the note still classifies the ordinary generative case correctly.
   */
  static usedNetwork(res) {
    if (res.path === "GENERATIVE") return true;
    return (res.trace?.stages || []).some(
      (s) => s.name === "answer_generative" && s.detail === LLM_CALLED
    );
  }

  /**
   * EVERY request contributes to BOTH series, and that is the point.
   *
   *   modelMs  what our pipeline cost, with the hosted model's stage removed
   *   ms       what the same request cost with it left in
   *
   * The difference between the two views is therefore exactly the model, on the
   * same questions, with the same n. An earlier version filtered requests out of
   * one series or the other, which made the two panels describe different sets
   * of questions and left the external percentiles frozen on whatever handful of
   * queries had happened to route - "P100 stuck" was that, not a maths bug.
   */
  record(res, sttMs) {
    if (!res?.trace) return null;
    const sample = {
      ms: res.trace.total_ms,
      srcMs: null,          // whole external-source request, browser wall clock
      srcUpstreamMs: null,  // the hosted model's share of it, server-measured
      srcUsage: null,       // the provider's own breakdown of that share
      modelMs: modelMs(res.trace),
      extMs: externalMs(res.trace),
      usedNetwork: Analytics.usedNetwork(res),
      path: res.path,
      status: res.status,
      reason: res.abstain_reason,
      top1: res.confidence?.rerank_top1 ?? null,
      sttMs: sttMs ?? null,
      stages: res.trace.stages || [],
      at: Date.now(),
    };
    this.samples.push(sample);
    return sample;   // so the caller can attach the external source later
  }

  clear() { this.samples = []; }

  get count() { return this.samples.length; }

  /** Nearest-rank percentile, the same rule as numpy.percentile(method="nearest").
   *
   * One difference, stated because this file is read next to published figures:
   * at an exact half-rank JS rounds up and numpy rounds to even, so a two-sample
   * P50 can pick the upper value here and the lower there. It cannot affect
   * anything published - those figures come from the Python harness, never from
   * this - and it is invisible above a handful of samples, which is the range
   * where the panel already refuses to call P100 a tail measurement.
   */
  static pct(sorted, p) {
    if (!sorted.length) return null;
    const i = Math.min(sorted.length - 1, Math.max(0, Math.round((p / 100) * (sorted.length - 1))));
    return sorted[i];
  }

  /**
   * Percentiles for one view.
   *
   *   "model"     our pipeline alone
   *   "external"  the same requests with the hosted model's time left in
   *
   * Same requests, same n, both views. The only difference is the model.
   */
  stats(view = "model") {
    if (!this.samples.length) return null;
    const sorted = this.samples
      .map((s) => (view === "external" ? Analytics.externalTotal(s) : s.modelMs))
      .sort((a, b) => a - b);
    return {
      n: sorted.length,
      p50: Analytics.pct(sorted, 50),
      p70: Analytics.pct(sorted, 70),
      p90: Analytics.pct(sorted, 90),
      p100: sorted[sorted.length - 1],
      mean: sorted.reduce((a, b) => a + b, 0) / sorted.length,
      min: sorted[0],
    };
  }

  /** Share of each answer path, which is the requirement 6 evidence. */
  paths() {
    const out = { EXTRACTIVE: 0, GENERATIVE: 0, NONE: 0 };
    for (const s of this.samples) out[s.path] = (out[s.path] || 0) + 1;
    return out;
  }

  abstentions() {
    return this.samples.filter((s) => s.status === "ABSTAINED");
  }

  /**
   * Median per stage across the session.
   *
   * Every stage appears in both views, including answer_generative - it reads as
   * 0.00 in the model view rather than vanishing, because a stage that is absent
   * from the list looks like a stage that was forgotten, while a stage sitting at
   * zero is the claim: the fast path makes no model call, and here is the
   * measurement saying so.
   */
  stageMedians(view = "model") {
    const acc = {};
    for (const s of this.samples) {
      if (view === "external") {
        // Only external work. Our stages are not listed here at all - see
        // externalRows() for why putting them here was a misstatement.
        for (const [name, ms] of externalRows(s)) (acc[name] ||= []).push(ms);
        continue;
      }
      for (const st of s.stages) {
        if (st.status === "skipped") continue;
        const ms = st.name === LLM_STAGE ? 0 : st.ms;
        (acc[st.name] ||= []).push(ms);
      }
    }
    return Object.entries(acc).map(([name, arr]) => {
      arr.sort((a, b) => a - b);
      return { name, median: Analytics.pct(arr, 50), n: arr.length };
    });
  }

  /** Every sample in order, for the sparkline, in the requested view. */
  series(view = "model") {
    return this.samples.map((s) =>
      view === "external" ? Analytics.externalTotal(s) : s.modelMs);
  }

  /** Download the session as JSON, so a run can be kept as evidence. */
  export() {
    const blob = new Blob([JSON.stringify({
      generated: new Date().toISOString(),
      note: "Live session samples from the Shruti browser client. Not a substitute for the offline 250 query benchmark in bench/results.",
      model: this.stats("model"),
      external: this.stats("external"),
      paths: this.paths(),
      samples: this.samples,
    }, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `shruti-session-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }
}

/* ------------------------------------------------------------------ */
/* Small helpers every theme needs                                     */
/* ------------------------------------------------------------------ */

export const fmt = (n, d = 1) => (n === null || n === undefined || Number.isNaN(n) ? "-" : n.toFixed(d));

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
));

/**
 * Four questions the frozen corpus answers well, two per language.
 *
 * EVERY ONE OF THESE WAS RUN THROUGH THE DEPLOYED PIPELINE AND THE ANSWER READ
 * BEFORE IT WENT ON THE PAGE, with the reranker's top-1 score in brackets. A
 * sample button that abstains, or that answers confidently from the wrong
 * passage, is worse than no sample button at all - it is the first thing a
 * visitor clicks.
 *
 * Checking matters more than it sounds. Several obvious-looking candidates were
 * rejected for answering wrongly rather than for abstaining: "who invented the
 * telephone" returns a passage about Philo Farnsworth and television, "how many
 * bones are in the human body" returns 80 (the axial skeleton alone, not 206),
 * and "how far is the moon from earth" returns an Apollo 13 passage. All three
 * come back ANSWERED with citations attached, which is exactly the failure
 * ISSUES.md I26 describes: the abstention floor knows when a question is outside
 * the corpus, and does not know when the answer it found is wrong.
 */
export const SAMPLE_QUERIES = [
  { q: "size of earth", kind: "answers", lang: "en" },                 // top-1 7.66
  { q: "what is the boiling point of water", kind: "answers", lang: "en" },  // top-1 8.93
  { q: "कतर की राजधानी क्या है", kind: "answers", lang: "hi" },          // top-1 10.56
  { q: "प्रकाश संश्लेषण क्या है", kind: "answers", lang: "hi" },          // top-1 8.35
  // The fifth is gibberish and is SUPPOSED to fail. Requirement 6 asks the
  // system to show it knows when not to answer, and a judge should be able to
  // see that in one click rather than having to invent a hard question. It comes
  // back ABSTAINED / LOW_CONFIDENCE at top-1 -2.93, well under the -1.103 floor.
  // Drawn with a dashed border by base.css so it does not read as a failure.
  { q: "zxc asid", kind: "refuses", lang: "en" },                      // top-1 -2.93
];
