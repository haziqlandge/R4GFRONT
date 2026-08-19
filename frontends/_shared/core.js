/**
 * The runtime: microphone capture, the two service clients, and the session
 * analytics store.
 *
 * Rules.md 4 is HARD: no API key ever reaches the browser. Nothing in this file
 * or anywhere under frontends/ holds a credential. The browser talks to our own
 * stt_gateway on :8001, and the gateway talks to Sarvam.
 *
 * PORT 3000 IS LOAD BEARING. services/stt_gateway/config.py allows CORS from
 * localhost:3000 and 127.0.0.1:3000 only. Serve these pages on any other port
 * and speech to text fails with a CORS error that looks like a broken gateway.
 * serve.bat pins 3000 for exactly this reason.
 */

export const RAG_CORE = "http://127.0.0.1:8000";
export const STT_GATEWAY = "http://127.0.0.1:8001";

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
      this.node.port.onmessage = (e) => { this.chunks.push(e.data); };
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
export class Analytics {
  constructor() {
    this.samples = [];  // { ms, band, path, status, lang, sttMs, stages, query }
  }

  record(res, sttMs) {
    if (!res?.trace) return;
    const band = res.path === "GENERATIVE" ? "B" : "A";
    this.samples.push({
      ms: res.trace.total_ms,
      band,
      path: res.path,
      status: res.status,
      reason: res.abstain_reason,
      top1: res.confidence?.rerank_top1 ?? null,
      sttMs: sttMs ?? null,
      stages: res.trace.stages || [],
      at: Date.now(),
    });
  }

  clear() { this.samples = []; }

  get count() { return this.samples.length; }

  /** Nearest rank percentile, matching numpy.percentile(method="nearest"). */
  static pct(sorted, p) {
    if (!sorted.length) return null;
    const i = Math.min(sorted.length - 1, Math.max(0, Math.round((p / 100) * (sorted.length - 1))));
    return sorted[i];
  }

  band(which) {
    const rows = this.samples.filter((s) => s.band === which);
    if (!rows.length) return null;
    const sorted = rows.map((s) => s.ms).sort((a, b) => a - b);
    const mean = sorted.reduce((a, b) => a + b, 0) / sorted.length;
    return {
      n: sorted.length,
      p50: Analytics.pct(sorted, 50),
      p70: Analytics.pct(sorted, 70),
      p90: Analytics.pct(sorted, 90),
      p100: sorted[sorted.length - 1],
      mean,
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

  /** Median per stage across the session, for the analytics breakdown. */
  stageMedians() {
    const acc = {};
    for (const s of this.samples) {
      for (const st of s.stages) {
        if (st.status === "skipped") continue;
        (acc[st.name] ||= []).push(st.ms);
      }
    }
    return Object.entries(acc).map(([name, arr]) => {
      arr.sort((a, b) => a - b);
      return { name, median: Analytics.pct(arr, 50), n: arr.length };
    });
  }

  /** Every Band A sample, oldest first, for the sparkline. */
  series(which = "A") {
    return this.samples.filter((s) => s.band === which).map((s) => s.ms);
  }

  /** Download the session as JSON, so a run can be kept as evidence. */
  export() {
    const blob = new Blob([JSON.stringify({
      generated: new Date().toISOString(),
      note: "Live session samples from the Shruti browser client. Not a substitute for the offline 250 query benchmark in bench/results.",
      bandA: this.band("A"),
      bandB: this.band("B"),
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

/** A few questions the frozen corpus can actually answer, plus two it cannot. */
export const SAMPLE_QUERIES = [
  { q: "what is a corporation", kind: "answers", lang: "en" },
  { q: "how much does it cost to hire a painter", kind: "answers", lang: "en" },
  { q: "एंड्रोजेन क्या होता है", kind: "answers", lang: "hi" },
  { q: "zxqwv fhqwhgads plorbnak", kind: "refuses", lang: "en" },
  { q: "what will bitcoin be worth in 2031", kind: "refuses", lang: "en" },
];
