/**
 * Microphone capture -> 16 kHz PCM16, plus the live amplitude the mic orb reads.
 *
 * Architecture.md 3.1: Sarvam's realtime endpoint takes raw PCM only, 16 kHz,
 * mono. The browser captures at whatever the device reports (usually 48 kHz,
 * sometimes 44.1) so the downsampling happens here, on the audio thread, in
 * public/pcm-worklet.js.
 *
 * Two outputs, deliberately separate:
 *
 *   onFrame(pcm)      20 ms frames of Int16 for the gateway. This is the data.
 *   getAmplitude()    a 0..1 RMS reading for the orb ring. This is the feedback.
 *
 * Design.md 5 calls the amplitude-reactive ring "the most important detail in the
 * whole design", because it makes the product feel responsive before any latency
 * number exists. It is read from an AnalyserNode on the same graph rather than
 * computed from the PCM frames, so a dropped or delayed frame never freezes the
 * visual - the ring keeps moving even if the socket stalls.
 */

export type RecorderState =
  | "idle"
  | "requesting"
  | "listening"
  | "processing"
  | "error";

export interface RecorderOptions {
  onFrame?: (pcm: Int16Array) => void;
  onStateChange?: (state: RecorderState) => void;
  onError?: (message: string) => void;
}

const WORKLET_URL = "/pcm-worklet.js";

/** Frames of silence the orb should read as zero rather than as noise floor. */
const AMPLITUDE_FLOOR = 0.004;

export class MicRecorder {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private analyser: AnalyserNode | null = null;
  private buf: Float32Array | null = null;
  private chunks: Int16Array[] = [];
  private smoothed = [0, 0, 0];
  private smoothIdx = 0;
  private opts: RecorderOptions;

  state: RecorderState = "idle";

  constructor(opts: RecorderOptions = {}) {
    this.opts = opts;
  }

  private setState(s: RecorderState) {
    this.state = s;
    this.opts.onStateChange?.(s);
  }

  get supported(): boolean {
    return (
      typeof window !== "undefined" &&
      typeof navigator?.mediaDevices?.getUserMedia === "function" &&
      typeof window.AudioContext === "function"
    );
  }

  async start(): Promise<void> {
    if (!this.supported) {
      this.fail("This browser cannot capture microphone audio. Use the text box below.");
      return;
    }
    this.setState("requesting");
    try {
      // Browser processing is left ON. It is tempting to disable AGC and noise
      // suppression to hand the model "clean" audio, but Saaras is trained on
      // real-world Indian audio recorded through exactly this kind of pipeline,
      // and a raw laptop mic in a noisy room transcribes worse without it.
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (err) {
      const name = (err as DOMException)?.name;
      this.fail(
        name === "NotAllowedError"
          ? "Microphone access was denied. Allow it in your browser, or type a question instead."
          : name === "NotFoundError"
            ? "No microphone found. Type a question instead."
            : `Could not open the microphone: ${String(err)}`
      );
      return;
    }

    try {
      this.ctx = new AudioContext();
      // Safari and some Chromium builds start suspended until a user gesture has
      // been observed by the context itself, not merely by the page.
      if (this.ctx.state === "suspended") await this.ctx.resume();
      await this.ctx.audioWorklet.addModule(WORKLET_URL);

      const source = this.ctx.createMediaStreamSource(this.stream);
      this.node = new AudioWorkletNode(this.ctx, "pcm-downsampler");
      this.node.port.onmessage = (e: MessageEvent<Int16Array>) => {
        const pcm = e.data;
        this.chunks.push(pcm);
        this.opts.onFrame?.(pcm);
      };

      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 1024;
      this.analyser.smoothingTimeConstant = 0; // Design.md 5: our own 3-frame average
      this.buf = new Float32Array(this.analyser.fftSize);

      source.connect(this.analyser);
      source.connect(this.node);
      // Deliberately NOT connected to ctx.destination. Routing the mic to the
      // speakers is a feedback loop, and on a laptop without headphones it
      // howls the moment the demo starts.
      this.setState("listening");
    } catch (err) {
      this.fail(`Audio pipeline failed to start: ${String(err)}`);
    }
  }

  /** Stop capture and return everything recorded as one PCM16 buffer. */
  async stop(): Promise<Int16Array> {
    this.node?.port.postMessage("flush");
    // One render quantum, so the flush lands before the graph is torn down.
    await new Promise((r) => setTimeout(r, 30));

    const total = this.chunks.reduce((n, c) => n + c.length, 0);
    const merged = new Int16Array(total);
    let at = 0;
    for (const c of this.chunks) {
      merged.set(c, at);
      at += c.length;
    }
    this.chunks = [];

    this.node?.port.postMessage("close");
    this.node?.disconnect();
    this.analyser?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    await this.ctx?.close();
    this.ctx = null;
    this.node = null;
    this.analyser = null;
    this.stream = null;

    this.setState(total > 0 ? "processing" : "idle");
    return merged;
  }

  /**
   * Current loudness, 0..1, for the orb ring.
   *
   * Three-frame moving average, per Design.md 5 - enough to stop single-frame
   * jitter reading as a flicker, short enough that the ring still tracks speech
   * rather than lagging behind it.
   */
  getAmplitude(): number {
    if (!this.analyser || !this.buf) return 0;
    this.analyser.getFloatTimeDomainData(this.buf);
    let sum = 0;
    for (let i = 0; i < this.buf.length; i++) sum += this.buf[i] * this.buf[i];
    const raw = Math.sqrt(sum / this.buf.length);

    this.smoothed[this.smoothIdx] = raw;
    this.smoothIdx = (this.smoothIdx + 1) % this.smoothed.length;
    const avg = (this.smoothed[0] + this.smoothed[1] + this.smoothed[2]) / 3;

    if (avg < AMPLITUDE_FLOOR) return 0;
    // Speech RMS lives around 0.02-0.2, so a linear map would leave the ring
    // almost motionless. sqrt expands the quiet end where normal speech sits.
    return Math.min(1, Math.sqrt(avg * 6));
  }

  setProcessing() {
    this.setState("processing");
  }

  reset() {
    this.setState("idle");
  }

  private fail(message: string) {
    this.setState("error");
    this.opts.onError?.(message);
  }
}

/** Wrap raw PCM16 as a WAV file, for the upload path and for saving demo audio. */
export function pcmToWav(pcm: Int16Array, sampleRate = 16000): Blob {
  const header = new ArrayBuffer(44);
  const v = new DataView(header);
  const ascii = (off: number, s: string) => {
    for (let i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i));
  };
  const dataBytes = pcm.length * 2;
  ascii(0, "RIFF");
  v.setUint32(4, 36 + dataBytes, true);
  ascii(8, "WAVEfmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // mono
  v.setUint32(24, sampleRate, true);
  v.setUint32(28, sampleRate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  ascii(36, "data");
  v.setUint32(40, dataBytes, true);
  return new Blob([header, pcm.buffer as ArrayBuffer], { type: "audio/wav" });
}
