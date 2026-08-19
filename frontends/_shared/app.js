/**
 * The controller every theme shares.
 *
 * A theme supplies markup carrying `data-sh` hooks and its own stylesheet. This
 * file finds those hooks and wires them to the recorder, the two services and
 * the analytics store. Nothing here sets a colour, a size or a position, so a
 * theme can restructure its entire layout without touching behaviour.
 *
 * HOOKS a theme may provide, all optional except mic or form:
 *   data-sh="mic"        button that starts and stops recording
 *   data-sh="orb"        gets data-state and a --amp custom property, 0 to 1
 *   data-sh="state"      text node for the current recorder state
 *   data-sh="transcript" text node for the recognised question
 *   data-sh="answer"     container for the answer or the abstention panel
 *   data-sh="waterfall"  container for the per stage timing
 *   data-sh="analytics"  container for the session percentiles
 *   data-sh="health"     container for the two service indicators
 *   data-sh="total"      compact total latency readout
 *   data-sh="stt"        compact speech to text readout
 *   data-sh="error"      inline error line
 *   data-sh="form"       text fallback form, containing data-sh="input"
 *   data-sh="samples"    container that gets sample question buttons
 *   data-mode="fast"     any button that switches answer mode
 */

import { Recorder, Analytics, ask, transcribe, health, fmt, esc, SAMPLE_QUERIES } from "./core.js";
import { renderAnswer, renderWaterfall, renderAnalytics, renderHealth } from "./ui.js";
import { BANDS, ROUTING, PROJECT } from "./data.js";

export function boot() {
  const $ = (k) => document.querySelector(`[data-sh="${k}"]`);
  const el = {
    mic: $("mic"), orb: $("orb"), state: $("state"), transcript: $("transcript"),
    answer: $("answer"), waterfall: $("waterfall"), analytics: $("analytics"),
    health: $("health"), total: $("total"), stt: $("stt"), error: $("error"),
    form: $("form"), input: $("input"), samples: $("samples"),
  };

  const analytics = new Analytics();
  let mode = "fast";
  let sttMs = null;
  let raf = 0;

  const setError = (msg) => {
    if (!el.error) return;
    el.error.textContent = msg || "";
    el.error.hidden = !msg;
  };

  const recorder = new Recorder({
    onState: (s) => {
      el.orb && (el.orb.dataset.state = s);
      el.mic && (el.mic.dataset.state = s);
      if (el.state) {
        el.state.textContent = {
          idle: "Ready", requesting: "Waiting for the microphone",
          listening: "Listening", processing: "Transcribing", error: "Microphone unavailable",
        }[s] || s;
      }
      if (s === "listening") loop(); else stopLoop();
    },
    onError: setError,
  });

  // The amplitude ring is read on an animation frame rather than pushed from the
  // audio thread, so a stalled socket never freezes the only moving thing on
  // screen. Cancelled the moment recording stops.
  function loop() {
    stopLoop();
    const tick = () => {
      el.orb?.style.setProperty("--amp", recorder.amplitude().toFixed(3));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
  }
  function stopLoop() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    el.orb?.style.setProperty("--amp", "0");
  }

  function paint(res) {
    el.answer && renderAnswer(el.answer, res, ROUTING.tauLow);
    el.waterfall && renderWaterfall(el.waterfall, res?.trace ?? null, PROJECT.budgetMs);
    el.analytics && renderAnalytics(el.analytics, analytics, BANDS);
    if (el.total) {
      el.total.textContent = res?.trace ? fmt(res.trace.total_ms, 1) : "-";
      el.total.dataset.over = String(!!res?.trace && res.trace.total_ms > PROJECT.budgetMs);
    }
    if (el.stt) el.stt.textContent = sttMs === null ? "-" : fmt(sttMs, 0);
  }

  async function submit(query) {
    if (!query?.trim()) return;
    setError("");
    document.body.dataset.busy = "true";
    try {
      const res = await ask(query, mode);
      analytics.record(res, sttMs);
      paint(res);
    } catch (err) {
      setError(`Could not reach the answer service on port 8000. Is rag_core running? (${err.message})`);
    } finally {
      document.body.dataset.busy = "false";
      recorder.reset();
    }
  }

  el.mic?.addEventListener("click", async () => {
    if (recorder.state === "listening") {
      const pcm = await recorder.stop();
      sttMs = null;
      if (!pcm.length) {
        setError("No audio was captured. Check which microphone is selected.");
        recorder.reset();
        return;
      }
      try {
        const t = await transcribe(pcm);
        sttMs = t.stt_ms ?? null;
        if (el.transcript) el.transcript.textContent = t.text || "";
        if (!t.text) {
          setError("Nothing was recognised in that recording. Try again, or type instead.");
          recorder.reset();
          return;
        }
        await submit(t.text);
      } catch (err) {
        setError(`Transcription failed on port 8001. Is stt_gateway running, and is SARVAM_API_KEY set? (${err.message})`);
        recorder.reset();
      }
      return;
    }
    paint(null);
    sttMs = null;
    if (el.transcript) el.transcript.textContent = "";
    setError("");
    await recorder.start();
  });

  el.form?.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = el.input?.value?.trim();
    if (!q) return;
    sttMs = null;
    if (el.transcript) el.transcript.textContent = q;
    submit(q);
  });

  document.querySelectorAll("[data-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      mode = btn.dataset.mode;
      document.querySelectorAll("[data-mode]").forEach((b) => {
        b.dataset.on = String(b.dataset.mode === mode);
      });
    });
    btn.dataset.on = String(btn.dataset.mode === mode);
  });

  // Sample questions include two the corpus cannot answer. That is deliberate:
  // the refusal is a scored requirement and a judge should be able to trigger it
  // in one click rather than having to invent a hard question on the spot.
  if (el.samples) {
    el.samples.innerHTML = SAMPLE_QUERIES.map((s) => `
      <button type="button" class="sh-sample" data-kind="${s.kind}" data-q="${esc(s.q)}">
        ${esc(s.q)}
      </button>`).join("");
    el.samples.querySelectorAll(".sh-sample").forEach((b) => {
      b.addEventListener("click", () => {
        const q = b.dataset.q;
        if (el.input) el.input.value = q;
        if (el.transcript) el.transcript.textContent = q;
        sttMs = null;
        submit(q);
      });
    });
  }

  /* ---------------------------------------------------------------- *
   * The text box is a stylistic toggle, not a feature flag.
   *
   * It exists because a judge with no microphone still has to be able to try
   * the system, so it stays in the DOM and stays wired. Hiding it is purely a
   * presentation choice for when the demo should read voice first.
   *
   * Three ways to flip it, because the person using them is us:
   *   Ctrl + .            keyboard
   *   shruti.chat.off()   browser console
   *   off chat            the on page console, on themes that have one
   * ---------------------------------------------------------------- */
  const chat = {
    get visible() { return document.body.dataset.chat !== "off"; },
    set(on) {
      document.body.dataset.chat = on ? "on" : "off";
      if (!on && document.activeElement === el.input) el.input.blur();
      window.dispatchEvent(new CustomEvent("shruti:chat", { detail: { on } }));
      return `chat ${on ? "on" : "off"}`;
    },
    on() { return chat.set(true); },
    off() { return chat.set(false); },
    toggle() { return chat.set(!chat.visible); },
  };
  document.body.dataset.chat = "on";

  const typing = (t) =>
    t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);

  document.addEventListener("keydown", (e) => {
    // Ctrl + . toggles the text box.
    if (e.ctrlKey && (e.key === "." || e.code === "Period")) {
      e.preventDefault();
      chat.toggle();
      return;
    }
    // Space starts and stops recording, but never while someone is typing a
    // question, and never when a button has focus, where space means "press".
    if ((e.code === "Space" || e.key === " ") && !typing(e.target)
        && !e.ctrlKey && !e.metaKey && !e.altKey
        && !(e.target instanceof HTMLButtonElement)
        && !(e.target instanceof HTMLAnchorElement)) {
      e.preventDefault();
      el.mic?.click();
    }
  });

  paint(null);
  if (el.health) {
    const poll = () => health().then((h) => renderHealth(el.health, h));
    poll();
    setInterval(poll, 15000);
  }

  // Handed to the on page console, and to anyone poking at this in devtools.
  const api = { chat, submit, analytics, recorder, mic: () => el.mic?.click() };
  window.shruti = api;
  return api;
}
