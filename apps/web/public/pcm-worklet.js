/**
 * AudioWorklet: microphone float32 -> 16 kHz PCM16, anti-aliased.
 *
 * Runs on the audio render thread, so it must never allocate per block, never
 * touch the DOM, and never take longer than one 128-sample quantum (2.67 ms at
 * 48 kHz). Everything below is written to that constraint.
 *
 * WHY THIS IS NOT JUST "TAKE EVERY THIRD SAMPLE"
 * Phases.md Phase 4 calls this out and it is the single easiest way to ship
 * broken audio: decimating 48 kHz to 16 kHz without filtering folds everything
 * above 8 kHz back down into the speech band as aliasing. Sibilance ("s", "sh")
 * and Devanagari retroflex consonants carry real energy up there, so the damage
 * lands exactly on the sounds an Indic STT model needs. It does not sound
 * obviously broken to a human ear on a laptop speaker - it just quietly costs
 * transcription accuracy, which is the worst kind of bug to ship.
 *
 * So: low-pass first, then resample.
 *
 *   1. A windowed-sinc FIR at 7.4 kHz, comfortably under the 8 kHz Nyquist of
 *      the 16 kHz output, applied at the INPUT rate.
 *   2. Fractional-position resampling with linear interpolation between filtered
 *      samples, so an arbitrary input rate works. This matters: 48 kHz gives a
 *      clean 3.0 ratio, but plenty of hardware reports 44.1 kHz, where the ratio
 *      is 2.75625 and integer decimation is not even an option.
 *
 * The filter is designed once at construction from the real sampleRate rather
 * than hard-coded for 48 kHz, because AudioContext gives you whatever the device
 * wants and assuming 48 kHz is how this breaks on someone else's laptop during a
 * demo.
 */

const TARGET_RATE = 16000;
const CUTOFF_HZ = 7400; // under the 8 kHz output Nyquist, with transition room
const FIR_TAPS = 63; // odd, so the filter has an exact integer group delay
const FRAME_SAMPLES = 320; // 20 ms at 16 kHz, matching stt_gateway FRAME_BYTES

/** Windowed-sinc low-pass. Blackman window: ~-58 dB stopband, which is more than
 *  enough here and cheaper to reason about than a parametric design. */
function designLowPass(cutoffHz, sampleRate, taps) {
  const h = new Float32Array(taps);
  const fc = cutoffHz / sampleRate; // normalised cutoff, cycles/sample
  const mid = (taps - 1) / 2;
  let sum = 0;
  for (let i = 0; i < taps; i++) {
    const n = i - mid;
    // sinc, with the removable singularity at n = 0 handled explicitly
    const sinc = n === 0 ? 2 * fc : Math.sin(2 * Math.PI * fc * n) / (Math.PI * n);
    const w =
      0.42 -
      0.5 * Math.cos((2 * Math.PI * i) / (taps - 1)) +
      0.08 * Math.cos((4 * Math.PI * i) / (taps - 1));
    h[i] = sinc * w;
    sum += h[i];
  }
  // Normalise to unity DC gain so the filter cannot change loudness, which would
  // shift the gateway's RMS thresholds out from under it.
  for (let i = 0; i < taps; i++) h[i] /= sum;
  return h;
}

class PCMDownsampler extends AudioWorkletProcessor {
  constructor() {
    super();
    this.taps = designLowPass(CUTOFF_HZ, sampleRate, FIR_TAPS);
    // Circular delay line for the FIR. Sized to the filter, written once per
    // input sample, never reallocated.
    this.history = new Float32Array(FIR_TAPS);
    this.historyPos = 0;
    // Fractional read position, in input samples, relative to the current block.
    this.phase = 0;
    this.step = sampleRate / TARGET_RATE;
    this.out = new Int16Array(FRAME_SAMPLES);
    this.outPos = 0;
    this.prevFiltered = 0;
    this.closed = false;

    this.port.onmessage = (e) => {
      if (e.data === "flush") this.flush();
      if (e.data === "close") this.closed = true;
    };
  }

  /** One FIR output for the sample just pushed into the delay line. */
  filterAt(pos) {
    const h = this.taps;
    const hist = this.history;
    let acc = 0;
    let idx = pos;
    for (let i = 0; i < FIR_TAPS; i++) {
      acc += h[i] * hist[idx];
      idx = idx === 0 ? FIR_TAPS - 1 : idx - 1;
    }
    return acc;
  }

  flush() {
    if (this.outPos === 0) return;
    // Copy: the buffer is reused, and a transferred view would be detached
    // underneath the next block.
    const slice = this.out.slice(0, this.outPos);
    this.port.postMessage(slice, [slice.buffer]);
    this.outPos = 0;
  }

  emit(sample) {
    // Clamp before scaling. A filter can overshoot slightly past +-1 on
    // transients, and wrapping an int16 turns a loud consonant into a click.
    const s = sample < -1 ? -1 : sample > 1 ? 1 : sample;
    this.out[this.outPos++] = s < 0 ? s * 0x8000 : s * 0x7fff;
    if (this.outPos === FRAME_SAMPLES) this.flush();
  }

  process(inputs) {
    if (this.closed) return false;
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    // Mono. A stereo mic is downmixed by taking channel 0 rather than averaging:
    // averaging two channels that are out of phase cancels speech, and laptop
    // arrays are not guaranteed to be in phase.
    const ch = input[0];
    if (!ch) return true;

    for (let i = 0; i < ch.length; i++) {
      this.history[this.historyPos] = ch[i];
      const filtered = this.filterAt(this.historyPos);
      this.historyPos = (this.historyPos + 1) % FIR_TAPS;

      // Emit every output sample whose fractional position falls inside the step
      // we just advanced through. Linear interpolation between the previous and
      // current filtered values; the signal is already band-limited by the FIR,
      // so linear interpolation adds negligible error here.
      while (this.phase < 1) {
        this.emit(this.prevFiltered + (filtered - this.prevFiltered) * this.phase);
        this.phase += this.step;
      }
      this.phase -= 1;
      this.prevFiltered = filtered;
    }
    return true;
  }
}

registerProcessor("pcm-downsampler", PCMDownsampler);
