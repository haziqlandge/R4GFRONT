/**
 * The console panel.
 *
 * Styling, not shell. Nothing here executes anything on your machine and there
 * is no server behind it; every command reads from the same data module the
 * documentation page uses, so anything it prints is a real measured figure with
 * a real source file behind it.
 *
 * It exists for two reasons. The layout had dead space under the timing panel,
 * and this theme is a terminal, so a panel you type into is the honest thing to
 * put there rather than another card. It also gives us somewhere to put the
 * admin controls: `off chat` and `on chat` toggle the text input, which is the
 * same switch as Ctrl + . and `shruti.chat.off()`.
 *
 * It starts collapsed as a single prompt strip. Clicking it opens the pane and
 * it stays open; clicking elsewhere on the page only stops the caret. `exit`
 * is the way out, and it clears the session on the way.
 */

import { PROJECT, BANDS, CORPUS, ROUTING, CHUNKING, RERANK, STACK, STT } from "/_shared/data.js";
import { fmt, esc } from "/_shared/core.js";

const PROMPT = "ok4t\\ragfront>";

const line = (text, cls = "") => ({ text, cls });

const COMMANDS = {
  help: () => [
    line("commands", "hd"),
    line("  status      what the two services report right now"),
    line("  session     percentiles for the queries you have run"),
    line("  budget      the published latency, all three bands"),
    line("  corpus      what is indexed"),
    line("  chunking    the six strategies we measured"),
    line("  rerank      why we changed reranker"),
    line("  guard       when it refuses, and when it should not be trusted"),
    line("  stack       what each layer is, and why"),
    line("  speech      the speech to text round trip"),
    line("  whoami"),
    line(""),
    line("  off chat    hide the text input   (also Ctrl + .)"),
    line("  on chat     bring it back"),
    line("  clear       wipe this pane"),
    line("  exit        clear this pane and close it   (also Esc)"),
  ],

  status: async () => {
    const out = [line("checking both services", "dim")];
    try {
      const core = await (await fetch("http://127.0.0.1:8000/health")).json();
      out.push(line(`  rag_core     ${core.status}`, core.status === "ok" ? "ok" : "bad"));
      out.push(line(`  index        ${Number(core.chunks).toLocaleString()} chunks, strategy ${core.strategy}`));
      out.push(line(`  reranker     ${core.reranker || "not loaded"}`));
      out.push(line(`  passages     ${core.passage_store}`));
      out.push(line(`  llm fallback ${core.generative ? "configured" : "no key, extractive only"}`));
      out.push(line(`  warm in      ${core.startup_seconds}s`));
    } catch {
      out.push(line("  rag_core     unreachable on :8000", "bad"));
    }
    try {
      const gw = await (await fetch("http://127.0.0.1:8001/health")).json();
      out.push(line(`  speech       ${gw.status}, expects ${gw.expects}`, gw.status === "ok" ? "ok" : "bad"));
    } catch {
      out.push(line("  speech       unreachable on :8001", "bad"));
    }
    return out;
  },

  session: () => {
    const a = window.shruti?.analytics;
    if (!a || !a.count) return [line("nothing yet. ask something first.", "dim")];
    const A = a.band("A"), B = a.band("B");
    const out = [line(`${a.count} quer${a.count === 1 ? "y" : "ies"} this session`, "hd")];
    if (A) {
      out.push(line(`  band A   n=${A.n}  p50 ${fmt(A.p50, 1)}  p70 ${fmt(A.p70, 1)}  p100 ${fmt(A.p100, 1)} ms`));
    }
    if (B) {
      out.push(line(`  band B   n=${B.n}  p50 ${fmt(B.p50, 0)}  p100 ${fmt(B.p100, 0)} ms`, "warn"));
    }
    const p = a.paths();
    out.push(line(`  answered ${p.EXTRACTIVE || 0} by quoting, ${p.GENERATIVE || 0} by the model, refused ${p.NONE || 0}`));
    if (A && A.n < 20) out.push(line(`  n is small. the published numbers come from 250 queries.`, "dim"));
    return out;
  },

  budget: () => [
    line(`target ${PROJECT.budgetMs} ms, measured on ${PROJECT.bench}`, "hd"),
    ...BANDS.rows.map((r) => line(
      `  ${r.label.padEnd(28)} p50 ${fmt(r.p50, 1).padStart(6)}  p70 ${fmt(r.p70, 1).padStart(6)}  p100 ${fmt(r.p100, 1).padStart(6)}`,
      r.inBudget ? "ok" : "warn"
    )),
    line(""),
    line(`  ${BANDS.method.queries} queries, ${BANDS.method.warmup} warmup runs thrown away, percentiles by nearest rank.`, "dim"),
    line("  band B is the model path. it is over budget on purpose and we publish it anyway.", "dim"),
  ],

  corpus: () => [
    line("the frozen slice", "hd"),
    line(`  ${CORPUS.queries.toLocaleString()} questions, ${CORPUS.passages.toLocaleString()} passages`),
    line(`  half english, half hindi, ${CORPUS.perLang.toLocaleString()} each`),
    line(`  ${CORPUS.dropped.toLocaleString()} duplicate pairs dropped, ${CORPUS.answerBearing.toLocaleString()} marked as answer bearing`),
    line(`  seed ${CORPUS.seed}, content hash ${CORPUS.sha}`),
    line(""),
    line("  english passages top out at 205 words, so nothing here needs splitting.", "dim"),
    line("  that is why the chunking work is about picking the unit, not cutting text.", "dim"),
  ],

  chunking: () => [
    line("measured on 500 held out questions, one code path for every strategy", "hd"),
    ...CHUNKING.measured.map((c) => line(
      `  ${c.id.padEnd(4)} ${c.name.padEnd(34)} en ${fmt(c.en, 3)}  hi ${fmt(c.hi, 3)}${c.derived ? "   reuses c1" : ""}`,
      c.verdict === "default" ? "ok" : c.derived ? "dim" : ""
    )),
    line(""),
    line(`  ${CHUNKING.headline}`, "dim"),
  ],

  rerank: () => [
    line("both candidates, same questions, same candidate lists", "hd"),
    ...RERANK.arms.map((a) => line(
      `  ${a.name.padEnd(32)} en ${fmt(a.en, 3)}  hi ${fmt(a.hi, 3)}${a.shipped ? "   <- shipped" : ""}`,
      a.shipped ? "ok" : a.hi < 0.2 ? "bad" : ""
    )),
    line(""),
    line("  the english only model wins english and destroys hindi.", "dim"),
    line("  0.120 is worse than doing no reranking at all, which scores 0.233.", "dim"),
  ],

  guard: () => [
    line("when it refuses", "hd"),
    line(`  refuse below   ${ROUTING.tauLow}`),
    line(`  quote above    ${ROUTING.tauHigh}`),
    line(`  between        hand it to the model`),
    line(""),
    ...ROUTING.dist.map((d) => line(`  ${d.path.padEnd(12)} ${String(d.pct).padStart(3)}%   ${d.note}`)),
    line(""),
    line("  it refuses every off topic question we tested. that part works.", "ok"),
    line("  it does NOT catch a confident answer taken from the wrong passage.", "bad"),
    line("  62.1% of what it answers is wrong under the strict labelling. see the docs.", "bad"),
  ],

  stack: () => [
    line("what each layer is", "hd"),
    ...STACK.map((s) => line(`  ${s.layer.padEnd(18)} ${s.choice}`)),
    line(""),
    line("  no hosted embeddings, no hosted vector database, no framework on the hot path.", "dim"),
    line("  every one of those is a network round trip the budget cannot pay for.", "dim"),
  ],

  speech: () => [
    line(`${STT.provider} ${STT.model}`, "hd"),
    ...STT.verified.map((v) => line(`  ${v.lang}  confidence ${fmt(v.conf, 3)}  ${v.ms} ms`)),
    line(""),
    line("  tested without a microphone by generating speech and feeding it back in.", "dim"),
    line("  that cost is band C. the 200 ms figure does not include it.", "dim"),
  ],

  whoami: () => [
    line("team ok4t", "hd"),
    line(`  ${PROJECT.task}`),
    line(`  corpus  ${PROJECT.dataset}`),
    line(`  repo    ${PROJECT.repo}`),
    line(`  tag     ${PROJECT.hashtag}`),
  ],

  // Small rewards for poking around.
  "cd goa": () => [
    line("packing the benchmark, the videos and one very tired laptop.", "ok"),
    line("see you there.", "dim"),
  ],
  ls: () => [
    line("index.html  docs.html  theme.css  console.js"),
    line("../_shared/  ../_backup/"),
    line("secrets/    permission denied", "dim"),
  ],
  sudo: () => [line("nice try.", "dim")],
  pwd: () => [line("/goa/2026/task-2/still-under-200ms")],
};

const ALIASES = {
  "?": "help", man: "help", h: "help",
  health: "status", latency: "budget", timing: "budget",
  guardrails: "guard", abstain: "guard", chunks: "chunking",
  stt: "speech", voice: "speech", who: "whoami", team: "whoami",
};

/**
 * One prompt, one caret, and a panel that closes when you leave it.
 *
 * Collapsed, the strip IS the prompt. Expanded, the strip is removed from the
 * layout entirely and the form's prompt is the only one on screen, because two
 * `ok4t\ragfront>` lines stacked on top of each other read as two terminals
 * rather than one that happens to be open.
 *
 * There is no button bar. The commands are discoverable through `help`, which
 * is the discovery mechanism a terminal already has, and a row of chips
 * offering eight of them is a second, worse copy of it.
 *
 * The caret is ours, not the browser's. The native one is painted transparent
 * and a `_` is positioned at the exact pixel where the next character will
 * land, measured with a hidden mirror span, so what you type starts where the
 * caret was rather than after it. It blinks only while the console has focus,
 * and there is no caret at all when it does not, which is the rule a real
 * terminal follows for an unfocused window.
 */
export function mountConsole(host) {
  host.innerHTML = `
    <button class="term-open" type="button" aria-expanded="false">
      <span class="term-prompt">${PROMPT}</span>
      <span class="term-open-hint">click to open</span>
    </button>
    <div class="term-panel" hidden>
      <div class="term-out" role="log" aria-live="polite"></div>
      <form class="term-form">
        <span class="term-prompt">${PROMPT}</span>
        <span class="term-field">
          <input class="term-in" type="text" autocomplete="off" spellcheck="false"
                 aria-label="Console input" placeholder="type help">
          <span class="term-caret" aria-hidden="true">_</span>
          <span class="term-mirror" aria-hidden="true"></span>
        </span>
        <span class="term-esc">type exit to close</span>
      </form>
    </div>`;

  const openBtn = host.querySelector(".term-open");
  const panel = host.querySelector(".term-panel");
  const out = host.querySelector(".term-out");
  const form = host.querySelector(".term-form");
  const field = host.querySelector(".term-field");
  const input = host.querySelector(".term-in");
  const mirror = host.querySelector(".term-mirror");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const history = [];
  let histIdx = -1;

  /* ---------------------------------------------------------------- *
   * The caret
   *
   * The mirror carries the same font, size and letter spacing as the input
   * and holds the same string, so its width is exactly where the next glyph
   * will start. Once the string is wider than the field the input scrolls its
   * own content and that measurement stops being true, so past that point the
   * caret is handed back to the browser rather than drawn in the wrong place.
   * ---------------------------------------------------------------- */
  function syncCaret() {
    mirror.textContent = input.value;
    const x = mirror.offsetWidth;
    const overflow = x > field.clientWidth - 12;
    field.dataset.overflow = String(overflow);
    if (!overflow) field.style.setProperty("--cx", `${x}px`);
  }

  function print(lines, delay = true) {
    lines.forEach((l, i) => {
      const p = document.createElement("p");
      p.className = `term-line ${l.cls || ""}`.trim();
      p.textContent = l.text;
      // A short cascade rather than an instant dump. It is the one animation in
      // this panel and it is what makes it read as output rather than as a
      // paragraph that was always there.
      if (delay && !reduce) {
        p.style.animationDelay = `${Math.min(i * 22, 320)}ms`;
        p.classList.add("term-line-in");
      }
      out.appendChild(p);
    });
    out.scrollTop = out.scrollHeight;
  }

  // `exit` ends a session rather than minimising one. Everything typed and
  // everything printed goes, so reopening starts on a clean pane instead of in
  // the middle of whatever was there before.
  function wipe() {
    out.innerHTML = "";
    input.value = "";
    history.length = 0;
    histIdx = -1;
    syncCaret();
  }

  async function run(raw) {
    const cmd = raw.trim().toLowerCase().replace(/\s+/g, " ");
    print([line(`${PROMPT} ${raw}`, "echo")], false);
    if (!cmd) return;

    history.unshift(raw);
    histIdx = -1;

    if (cmd === "clear") { out.innerHTML = ""; return; }
    if (cmd === "exit" || cmd === "close") { wipe(); collapse(); return; }

    // The admin switch, in both word orders because both read naturally.
    const chatMatch = cmd.match(/^(?:(on|off)\s+chat|chat\s+(on|off))$/);
    if (chatMatch) {
      const state = chatMatch[1] || chatMatch[2];
      // The confirmation is not printed here. chat.set() fires shruti:chat and
      // the listener at the bottom of this file reports the new state for every
      // route into that switch, Ctrl + . and the devtools call included.
      // Printing it here as well put the same sentence on screen twice, once
      // in each style.
      const res = window.shruti?.chat.set(state === "on");
      if (!res) print([line("chat control unavailable", "bad")]);
      return;
    }
    if (cmd === "chat") {
      print([line(`text input is ${window.shruti?.chat.visible ? "on" : "off"}. try: off chat`, "dim")]);
      return;
    }

    const key = ALIASES[cmd] || cmd;
    const fn = COMMANDS[key];
    if (!fn) {
      print([line(`${cmd}: not a command. type help.`, "bad")]);
      return;
    }
    const result = await fn();
    print(result);
  }

  function expand() {
    panel.hidden = false;
    openBtn.setAttribute("aria-expanded", "true");
    host.dataset.open = "true";
    if (!out.childElementCount) {
      print([
        line("shruti console. type help to learn commands.", "dim"),
        line(""),
      ]);
    }
    input.focus();
    sync();
    syncCaret();
  }

  // Once it is open it stays open. Clicking away from it stops the caret and
  // nothing else: a pane that folded itself up because you clicked the answer
  // above it took the session with it, and losing what you had just read is a
  // worse outcome than a pane left open. Closing is a thing you say, and
  // saying it ends the session, so the only two exits both go through wipe().
  function collapse() {
    panel.hidden = true;
    openBtn.setAttribute("aria-expanded", "false");
    host.dataset.open = "false";
    host.dataset.active = "false";
    // Focus has to go somewhere, and the strip is where it belongs: it is the
    // control that opened the pane, and dropping focus on the body would leave
    // a keyboard user at the top of the document. It is moved quietly though.
    // Closing came from Esc or from typing `exit`, so :focus-visible matches
    // and paints a ring around a strip nobody asked to highlight; `quiet`
    // suppresses that one ring and nothing else.
    openBtn.dataset.quiet = "true";
    openBtn.focus();
  }

  openBtn.addEventListener("click", expand);

  // The moment the strip is used or left on its own terms, it is an ordinary
  // focusable control again and gets its ring back.
  const unquiet = () => { delete openBtn.dataset.quiet; };
  openBtn.addEventListener("blur", unquiet);
  openBtn.addEventListener("keydown", unquiet);

  /* -------------------------------------------------------------- *
   * What makes the caret blink is focus, and only focus.
   *
   * The state is recomputed from where focus actually is rather than
   * toggled by each event, because the events do not tell the whole
   * story on their own: a focus that never left cannot fire focusin
   * again, and a window that loses focus fires nothing at all on the
   * element. The window handlers are what stop the caret when you
   * switch away from the tab, which is what a terminal does when its
   * window goes to the background.
   * -------------------------------------------------------------- */
  const sync = () => {
    // Open as well as focused. Closing hands focus back to the strip, which is
    // inside the host, and a containment test on its own would call that
    // active.
    host.dataset.active =
      String(host.dataset.open === "true" && host.contains(document.activeElement));
  };
  host.addEventListener("focusin", sync);
  // document.activeElement is only correct after this tick, and a focusout
  // whose next target is still inside the panel is a move, not a departure.
  host.addEventListener("focusout", () => setTimeout(sync, 0));
  addEventListener("focus", sync);
  addEventListener("blur", () => { host.dataset.active = "false"; });
  // A click anywhere on the page re-reads where focus ended up. Clicking a
  // region that takes no focus at all fires no focus event on the way out, and
  // this is the case the caret must not survive: the pane stays open, and the
  // caret stops.
  document.addEventListener("pointerdown", () => setTimeout(sync, 0));

  function submitLine() {
    const v = input.value;
    input.value = "";
    syncCaret();
    run(v);
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    submitLine();
  });

  input.addEventListener("input", syncCaret);

  input.addEventListener("keydown", (e) => {
    // The keyboard spelling of `exit`, and it does the same thing: the pane is
    // cleared, not just hidden.
    if (e.key === "Escape") {
      e.preventDefault();
      wipe();
      collapse();
      return;
    }
    // Enter is handled here rather than left to the form's implicit submission.
    // A form with one field and no submit button is supposed to submit on
    // Enter, but that is exactly the case browsers and embedded views disagree
    // about, and a console whose Return key sometimes does nothing is not a
    // console. preventDefault stops the implicit path, so this runs once.
    if (e.key === "Enter") {
      e.preventDefault();
      submitLine();
      return;
    }
    if (e.key === "ArrowUp" && history.length) {
      e.preventDefault();
      histIdx = Math.min(histIdx + 1, history.length - 1);
      input.value = history[histIdx];
      syncCaret();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      histIdx = Math.max(histIdx - 1, -1);
      input.value = histIdx === -1 ? "" : history[histIdx];
      syncCaret();
    }
  });

  // The field width moves with the layout, so the overflow test has to be
  // re-run rather than measured once.
  addEventListener("resize", syncCaret);

  // Keep the console honest about the switch it shares with the keyboard.
  window.addEventListener("shruti:chat", (e) => {
    if (!panel.hidden) print([line(`text input ${e.detail.on ? "on" : "off"}`, "dim")], false);
  });

  host.dataset.open = "false";
  host.dataset.active = "false";
}
