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
 */

import { Recorder, Analytics, ask, aside, transcribe, openLiveTranscript, health, fmt, esc, SAMPLE_QUERIES } from "./core.js";
import { renderAnswer, renderAside, renderWaterfall, renderAnalytics, renderHealth } from "./ui.js";
import { BANDS, ROUTING, PROJECT } from "./data.js";

export function boot() {
  const $ = (k) => document.querySelector(`[data-sh="${k}"]`);
  const el = {
    mic: $("mic"), orb: $("orb"), state: $("state"), transcript: $("transcript"),
    answer: $("answer"), waterfall: $("waterfall"), analytics: $("analytics"),
    health: $("health"), total: $("total"), stt: $("stt"), error: $("error"),
    form: $("form"), input: $("input"), samples: $("samples"), aside: $("aside"),
  };

  const analytics = new Analytics();

  /* ---------------------------------------------------------------- *
   * Two modes, and only one of them may touch the network.
   *
   *   fast      extractive only. No LLM call can happen, which is what makes
   *             the published Band A figure describe every request on this path.
   *   accurate  the same answer, plus the generative band for mid-confidence
   *             queries, plus the unverified aside below the answer.
   *
   * They are identical for any top-1 above the 1.877 routing threshold, which
   * is every sample question on this page (7.66 to 10.56). Measured on the
   * deployed service, the band where they differ:
   *
   *     who is narendra modi                 2.63   both ANSWERED, ~105 ms
   *     who is the prime minister of india   1.20   fast ANSWERED 100 ms /
   *                                                 accurate ABSTAINED 506 ms
   * ---------------------------------------------------------------- */
  let mode = "fast";
  let sttMs = null;
  let raf = 0;

  /* ---------------------------------------------------------------- *
   * Live transcription state.
   *
   * `live` is the open socket to the gateway's /v1/stt/live relay, or null when
   * there is none - and null is a supported state, not a failure. Voice input is
   * requirement 1 and it works through /v1/stt/file; this is an upgrade layered
   * on top, so every path below falls back to uploading the recording rather
   * than reporting an error the visitor cannot act on.
   * ---------------------------------------------------------------- */
  let live = null;
  let liveFinals = [];       // completed utterances, in the order they were said
  let livePartial = "";      // the utterance currently being spoken
  let liveSpeaking = false;  // a partial has arrived since the last final
  let settleFinal = null;    // resolver for "a final arrived after I pressed stop"
  let speechEndedAt = 0;

  // What is on screen at any moment: everything already finalised, plus the
  // words currently arriving. Sarvam's VAD closes an utterance when someone
  // pauses, so a question said in two breaths is two finals and has to be
  // joined rather than the second one replacing the first.
  const liveSoFar = () => [...liveFinals, livePartial].join(" ").replace(/\s+/g, " ").trim();

  // Sarvam's final arrived 385 ms after the audio stopped when this was measured
  // (scripts/08_probe_realtime_stt.py). Four seconds is not a tuned value, it is
  // "long enough that a slow network is not mistaken for a broken socket".
  const LIVE_FINAL_TIMEOUT_MS = 4000;

  /* ---------------------------------------------------------------- *
   * LIVE TRANSCRIPT IS OFF, AND THE REASON IS MEASURED, NOT SUSPECTED.
   *
   * The relay works. scripts/08_probe_realtime_stt.py got 19 partials over 5.8 s
   * of speech and 08b_probe_live_relay.py got 7 of them back through our own
   * gateway and Caddy. Tried with a real microphone, English was excellent.
   *
   * HINDI WAS NOT. With language_code=auto - which is what makes this system
   * bilingual without asking anyone to declare a language - Sarvam's realtime
   * model streams partials in ROMANISED Hindi and only converts to Devanagari on
   * the final. Speaking Hindi, you watch "Qatar ki rajdhani kya hai" type itself
   * out and then snap to "कतर की राजधानी क्या है" when you stop.
   *
   * Everything was tried before giving up (scripts/08c_probe_hindi_partials.py):
   *
   *   language_code   mode                     partials         final
   *   auto            transcribe/codemix       romanised        correct
   *   auto            verbatim                 romanised        CORRUPT for en
   *   auto            translit                 romanised        romanised
   *   hi-IN           transcribe               DEVANAGARI       correct for hi
   *   hi-IN           transcribe, ENGLISH in   devanagari       'व्हाट इज द कैपिटल'
   *   en-IN           transcribe               latin            correct for en
   *
   * stream_type fast and balanced behave identically on this axis.
   *
   * So the only thing that fixes Hindi partials is pinning the language, and a
   * pinned socket does not merely mis-render the other language - it corrupts
   * the FINAL, which is the string sent to rag_core. "व्हाट इज द कैपिटल ऑफ कतार"
   * retrieves nothing. Trading a correct answer for a prettier caption is the
   * wrong way round, and asking a visitor to declare their language before
   * speaking gives up the auto-detection this project advertises.
   *
   * So the transcript appears whole, when you stop, as it always did. Turn this
   * to true to watch it stream; everything behind it is built, wired and tested.
   * ---------------------------------------------------------------- */
  const LIVE_TRANSCRIPT = false;

  // Drives the blinking caret and suppresses the "Your question appears here."
  // placeholder, which must not sit underneath a transcript that is being typed.
  const setLive = (on) => {
    if (el.transcript) el.transcript.dataset.live = on ? "true" : "false";
  };

  const setError = (msg) => {
    if (!el.error) return;
    el.error.textContent = msg || "";
    el.error.hidden = !msg;
  };

  /* ---------------------------------------------------------------- *
   * The placeholder shrinks to fit, rather than switching at a breakpoint.
   *
   * "type a question in english or hindi" is 308 px in the input's face and
   * clips on a phone, where the box is a little over 200. A media query would
   * work for one font at one zoom; measuring the actual box does not care which
   * of those changed, and the mic column collapsing at 620 changes the width
   * without changing the viewport class.
   *
   * Longest first. The last entry is the floor and ships even if nothing fits,
   * because an empty placeholder is worse than a cramped one.
   * ---------------------------------------------------------------- */
  const PLACEHOLDERS = [
    "type a question in english or hindi",
    "type a question in eng or hindi",
    "type ques in eng or hindi",
    "type in eng or hindi",
  ];

  const fitPlaceholder = () => {
    if (!el.input) return;
    const cs = getComputedStyle(el.input);
    const avail = el.input.clientWidth
      - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    if (!(avail > 0)) return;  // display:none while the text box is off
    fitPlaceholder.ctx ||= document.createElement("canvas").getContext("2d");
    const ctx = fitPlaceholder.ctx;
    ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    const pick = PLACEHOLDERS.find((t) => ctx.measureText(t).width <= avail);
    el.input.placeholder = pick || PLACEHOLDERS[PLACEHOLDERS.length - 1];
  };

  if (el.input && typeof ResizeObserver === "function") {
    // The input itself, not the window: it is what has to hold the text, and it
    // resizes for reasons the window does not (the chat toggle, the mic column).
    new ResizeObserver(fitPlaceholder).observe(el.input);
  }
  fitPlaceholder();

  const recorder = new Recorder({
    // Every PCM frame goes two places: the buffer that /v1/stt/file would upload,
    // and the live socket, when one is open.
    onChunk: (chunk) => live?.send(chunk),
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
    renderAside(el.aside, null);   // clear the previous question's aside first
    try {
      const res = await ask(query, mode);

      // ANALYTICS SEES OUR RETRIEVAL AND NOTHING ELSE. `res` is the /v1/answer
      // trace; the aside below is a separate request on a separate endpoint and
      // is never recorded. The percentiles on this page describe this system's
      // pipeline, and folding an external model's round trip into them would
      // make them describe neither.
      analytics.record(res, sttMs);
      paint(res);

      // Accurate only, and deliberately AFTER paint(). Our answer is already on
      // screen and already timed by the time this is requested, so the panel
      // costs nothing against the 200 ms band - the fast path still makes zero
      // network calls, and this one is not on it.
      if (mode === "accurate") {
        aside(query).then(({ text, model }) => {
          // The mode can change, or another question can be asked, while this is
          // in flight. Only paint if the answer it belongs to is still showing.
          //
          // `model` names who actually answered and the footer prints it. This
          // panel carries no citation and no grounding check, so an
          // unattributed one would be the only unlabelled claim on the page.
          if (mode === "accurate" && el.transcript?.textContent === query) {
            renderAside(el.aside, text, model);
          }
        });
      }
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
      // The clock the boundary line on screen describes: "you time from when you
      // stop speaking". It starts here, not when the request is sent.
      speechEndedAt = performance.now();
      sttMs = null;

      // Tell Sarvam the utterance is over, but do NOT close the socket - the
      // final transcript is still owed and closing here would race it.
      live?.end();

      let finalText = null;
      if (live) {
        if (!liveSpeaking && liveFinals.length) {
          // Everything said has already been finalised - the speaker paused and
          // VAD closed the utterance before the button was pressed. Waiting for
          // another final would stall for the whole timeout and produce nothing.
          finalText = liveSoFar();
        } else {
          finalText = await Promise.race([
            new Promise((resolve) => { settleFinal = resolve; }),
            new Promise((r) => setTimeout(() => r(liveSoFar() || null), LIVE_FINAL_TIMEOUT_MS)),
          ]);
          settleFinal = null;
        }
      }
      setLive(false);
      live?.close();
      live = null;

      if (finalText) {
        sttMs = Math.round(performance.now() - speechEndedAt);
        if (el.transcript) el.transcript.textContent = finalText;
        await submit(finalText);
        return;
      }

      // Fallback: the live socket gave us nothing usable. Upload the recording,
      // which is the path requirement 1 has always been scored on.
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

    // Open the live socket BEFORE the microphone, so the first frames the
    // worklet produces have somewhere to go.
    liveFinals = [];
    livePartial = "";
    liveSpeaking = false;
    settleFinal = null;
    live = LIVE_TRANSCRIPT ? openLiveTranscript({
      onPartial: (text) => {
        livePartial = text;
        liveSpeaking = Boolean(text.trim());
        if (el.transcript) el.transcript.textContent = liveSoFar();
      },
      onFinal: (text) => {
        if (text) liveFinals.push(text);
        livePartial = "";
        liveSpeaking = false;
        if (el.transcript) el.transcript.textContent = liveSoFar();
        // Only unblocks the mic button if it is already waiting. A final that
        // arrives mid-recording just becomes part of the running transcript.
        settleFinal?.(liveSoFar() || null);
      },
      // Resolving with null rather than rejecting: a dead socket must send this
      // back to the upload path, not surface a websocket error to a visitor.
      onError: () => settleFinal?.(null),
    }) : null;
    setLive(Boolean(live));
    await recorder.start();
  });

  el.form?.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = el.input?.value?.trim();
    if (!q) return;
    sttMs = null;
    if (el.transcript) el.transcript.textContent = q;
    // Emptied as soon as it is sent. The question is echoed into the transcript
    // line above, so nothing is lost, and the next question can be typed without
    // clearing the previous one by hand.
    if (el.input) el.input.value = "";
    submit(q);
  });

  document.querySelectorAll("[data-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      mode = btn.dataset.mode;
      document.querySelectorAll("[data-mode]").forEach((b) => {
        b.dataset.on = String(b.dataset.mode === mode);
      });
      // Leaving accurate takes the aside with it: it belongs to that mode, and
      // a stale panel under a fast answer would claim a comparison nobody ran.
      if (mode !== "accurate") renderAside(el.aside, null);
    });
    btn.dataset.on = String(btn.dataset.mode === mode);
  });

  // Four sample questions, two per language, every one of them checked against
  // the real pipeline before it went on the page (see SAMPLE_QUERIES in core.js
  // for what was rejected and why).
  //
  // These used to include two questions the corpus CANNOT answer, so that a
  // judge could trigger the refusal - a scored requirement - in one click.
  // Those were removed on request in favour of four that answer well. The
  // refusal path is still one typed question away: any gibberish triggers it,
  // and the guardrails section of the documentation page shows it measured.
  if (el.samples) {
    el.samples.innerHTML = SAMPLE_QUERIES.map((s) => `
      <button type="button" class="sh-sample" data-kind="${s.kind}" data-q="${esc(s.q)}">
        ${esc(s.q)}
      </button>`).join("");
    el.samples.querySelectorAll(".sh-sample").forEach((b) => {
      b.addEventListener("click", () => {
        const q = b.dataset.q;
        if (el.transcript) el.transcript.textContent = q;
        if (el.input) el.input.value = "";
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
  // ON by default. It was briefly off so the page would read voice-first, but a
  // visitor who cannot or will not talk to a laptop needs the box in front of
  // them, not one keystroke away behind a shortcut nobody advertises.
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
