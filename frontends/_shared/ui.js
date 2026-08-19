/**
 * Render helpers for the demo page.
 *
 * These emit semantic markup with `sh-` prefixed class names and no styling
 * opinions at all: theme.css owns the appearance entirely. Anything that decides
 * what a number MEANS lives here, anything that decides how it LOOKS lives in
 * the theme. That line is why a full visual pass costs a stylesheet rather than
 * a rewrite.
 */

import { fmt, esc } from "./core.js";

/* ------------------------------------------------------------------ */
/* Answer, citations, abstention                                       */
/* ------------------------------------------------------------------ */

/**
 * Put the spaces back.
 *
 * MSMARCO-XI passages are stitched together from several source sentences and
 * the join often lost its whitespace, so the raw text really does read
 * "corporate.A group of people" and "owns itsA CORPORATION". That is in the
 * corpus, not a bug here.
 *
 * This inserts whitespace and changes nothing else. No word is added, removed,
 * reordered or altered, so the extractive answer is still the passage verbatim
 * in every sense that matters. Two rules:
 *
 *   1. after . ! ? , ; : when a letter or digit follows immediately
 *   2. between a run of at least three lowercase letters and a capital
 *
 * Rule 2 is what fixes "owns itsA CORPORATION" and "a corporationNobody owns".
 * Three lowercase letters is the threshold that leaves iPhone, eBay and
 * McDonald alone, since none of them has three lowercase before the capital.
 * It will split a genuine compound like JavaScript, which is a cosmetic cost we
 * accept: this corpus is prose with lost sentence breaks, not identifiers.
 */
export function respace(text) {
  return String(text ?? "")
    .replace(/([.!?,;:])([A-Za-z0-9ऀ-ॿ])/g, "$1 $2")
    .replace(/([a-z]{3})([A-Z])/g, "$1 $2")
    .replace(/\s{2,}/g, " ")
    .trim();
}

/**
 * Split an answer into the part that answers the question and the rest.
 *
 * The lead is set large and the surrounding passage small. It takes whole
 * sentences until it has enough to be worth reading, because MS MARCO passages
 * often open with a fragment: "Also called body corporate." is a true first
 * sentence and a useless headline, while the sentence after it is the actual
 * definition. Nothing is hidden or truncated, it is only weighted.
 */
const LEAD_MIN = 55;
const LEAD_MAX = 240;

function splitAnswer(text) {
  const t = respace(text);
  // Keep the delimiter attached to the sentence it ends.
  const parts = t.split(/(?<=[.!?])\s+/);
  if (parts.length < 2) return { lead: t, rest: "" };

  let lead = "";
  let i = 0;
  while (i < parts.length && lead.length < LEAD_MIN) {
    const next = lead ? `${lead} ${parts[i]}` : parts[i];
    if (lead && next.length > LEAD_MAX) break;
    lead = next;
    i++;
  }
  const rest = parts.slice(i).join(" ").trim();
  return rest ? { lead, rest } : { lead: t, rest: "" };
}

const REASON_TEXT = {
  LOW_CONFIDENCE: "Nothing retrieved scored above the abstention floor.",
  OFF_TOPIC: "The question is outside what this corpus covers.",
  UNSAFE_INPUT: "The input was rejected before retrieval ran.",
  UNGROUNDED_OUTPUT: "An answer was produced but could not be grounded in the cited passages.",
  AMBIGUOUS_RETRIEVAL: "Several passages scored alike and none was clearly the answer.",
};

export function renderAnswer(el, res, floor = -1.103) {
  if (!res) {
    el.innerHTML = `<p class="sh-idle">Ask a question by voice or text. The answer, its sources and its full timing breakdown all appear here.</p>`;
    el.dataset.status = "idle";
    return;
  }

  el.dataset.status = res.status.toLowerCase();
  el.dataset.path = res.path;

  if (res.status === "ABSTAINED") {
    const top1 = res.confidence?.rerank_top1;
    // The score bar is the whole point of this panel: a refusal that shows the
    // number it refused on is a designed state, not an error message.
    const span = 14; // logit range drawn, centred so the floor sits mid scale
    const pos = (v) => Math.max(0, Math.min(100, ((v + span / 2) / span) * 100));
    el.innerHTML = `
      <div class="sh-abstain">
        <p class="sh-abstain-head">This one is not answered.</p>
        <p class="sh-reason-code">${esc(res.abstain_reason || "LOW_CONFIDENCE")}</p>
        <p class="sh-reason-text">${esc(REASON_TEXT[res.abstain_reason] || REASON_TEXT.LOW_CONFIDENCE)}</p>
        <div class="sh-scorebar" role="img"
             aria-label="Top score ${fmt(top1, 2)} against a floor of ${fmt(floor, 2)}">
          <span class="sh-scorebar-floor" style="left:${pos(floor)}%"></span>
          ${top1 !== null && top1 !== undefined
            ? `<span class="sh-scorebar-mark" style="left:${pos(top1)}%"></span>` : ""}
        </div>
        <dl class="sh-scorenums">
          <div><dt>top score</dt><dd class="sh-num">${fmt(top1, 3)}</dd></div>
          <div><dt>floor</dt><dd class="sh-num">${fmt(floor, 3)}</dd></div>
        </dl>
        <p class="sh-fineprint">The floor was fitted on a held out dev partition, never on the benchmark set.</p>
      </div>`;
    return;
  }

  const cites = (res.citations || []).map((c, i) => `
    <li class="sh-cite">
      <button class="sh-cite-head" aria-expanded="false">
        <span class="sh-cite-n">${i + 1}</span>
        <span class="sh-cite-id">${esc(c.passage_id)}</span>
        <span class="sh-cite-lang">${esc(c.language)}</span>
        <span class="sh-num sh-cite-score">${fmt(c.score, 2)}</span>
      </button>
      <div class="sh-cite-body" hidden><p>${esc(respace(c.text))}</p></div>
    </li>`).join("");

  const { lead, rest } = splitAnswer(res.answer);
  const conf = res.confidence || {};

  el.innerHTML = `
    <div class="sh-answered">
      <div class="sh-answer-main">
        <p class="sh-answer-text">${esc(lead)}</p>
        ${rest ? `<p class="sh-answer-rest">${esc(rest)}</p>` : ""}
        <div class="sh-answer-meta">
          <span class="sh-badge" data-path="${esc(res.path)}">${esc(res.path)}</span>
          <span class="sh-fineprint">${res.path === "EXTRACTIVE"
            ? "Quoted from the passage below. No model wrote this."
            : "Written by the fallback model from the passages below."}</span>
        </div>
        ${cites ? `<ol class="sh-cites">${cites}</ol>` : ""}
      </div>
      <aside class="sh-answer-side">
        <div class="sh-side-row">
          <span class="sh-side-k">confidence</span>
          <span class="sh-num sh-side-v">${fmt(conf.rerank_top1, 2)}</span>
        </div>
        <div class="sh-side-row">
          <span class="sh-side-k">margin over 2nd</span>
          <span class="sh-num sh-side-v">${fmt(conf.score_gap, 2)}</span>
        </div>
        <div class="sh-side-row">
          <span class="sh-side-k">refuse below</span>
          <span class="sh-num sh-side-v sh-side-dim">${fmt(floor, 2)}</span>
        </div>
        <div class="sh-side-row">
          <span class="sh-side-k">sources</span>
          <span class="sh-num sh-side-v">${(res.citations || []).length}</span>
        </div>
        <p class="sh-side-note">Score is the cross encoder reading your question against the passage. Higher means it answers the question, not merely shares its topic.</p>
      </aside>
    </div>`;

  el.querySelectorAll(".sh-cite-head").forEach((btn) => {
    btn.addEventListener("click", () => {
      const body = btn.nextElementSibling;
      const open = !body.hidden;
      body.hidden = open;
      btn.setAttribute("aria-expanded", String(!open));
    });
  });
}

/* ------------------------------------------------------------------ */
/* Latency waterfall                                                   */
/* ------------------------------------------------------------------ */

/**
 * Per stage timing against the 200 ms budget.
 *
 * Bars are scaled to the BUDGET, not to the total. Scaling to the total would
 * make every run fill the width and destroy the only thing this chart exists to
 * show, which is how much headroom is left.
 */
export function renderWaterfall(el, trace, budgetMs = 200) {
  if (!trace) {
    el.innerHTML = `<p class="sh-idle">No run yet.</p>`;
    return;
  }
  const budget = trace.budget_ms || budgetMs;
  const over = trace.total_ms > budget;
  const scale = Math.max(budget, trace.total_ms);

  const bars = trace.stages.map((s) => {
    const pct = Math.max(0.4, (s.ms / scale) * 100);
    return `
      <div class="sh-wf-row" data-status="${esc(s.status)}">
        <span class="sh-wf-name">${esc(s.name)}</span>
        <span class="sh-wf-track">
          <span class="sh-wf-bar" style="width:${pct}%"></span>
        </span>
        <span class="sh-num sh-wf-ms">${fmt(s.ms, 2)}</span>
      </div>`;
  }).join("");

  el.innerHTML = `
    <div class="sh-wf" data-over="${over}">
      <div class="sh-wf-total">
        <span class="sh-num sh-wf-big">${fmt(trace.total_ms, 1)}</span>
        <span class="sh-wf-unit">ms</span>
        <span class="sh-wf-verdict">${over ? `over the ${budget} ms budget` : `of a ${budget} ms budget`}</span>
      </div>
      <div class="sh-wf-rows">${bars}</div>
      <p class="sh-fineprint">Band A only. Speech to text is a network call and is timed separately.</p>
    </div>`;
}

/* ------------------------------------------------------------------ */
/* Session analytics                                                   */
/* ------------------------------------------------------------------ */

function sparkline(values, w = 240, h = 34, budget = 200) {
  if (values.length < 2) return "";
  const max = Math.max(budget, ...values);
  const step = w / (values.length - 1);
  const pts = values.map((v, i) => `${(i * step).toFixed(1)},${(h - (v / max) * h).toFixed(1)}`).join(" ");
  const budgetY = (h - (budget / max) * h).toFixed(1);
  // An information graphic, not decoration: the line is the session and the
  // dashed rule is the budget it is being judged against.
  return `
    <svg class="sh-spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img"
         aria-label="Band A latency across ${values.length} queries this session">
      <line class="sh-spark-budget" x1="0" y1="${budgetY}" x2="${w}" y2="${budgetY}"></line>
      <polyline class="sh-spark-line" points="${pts}"></polyline>
    </svg>`;
}

export function renderAnalytics(el, analytics, published) {
  const a = analytics.band("A");
  const b = analytics.band("B");
  const paths = analytics.paths();
  const total = analytics.count;

  if (!total) {
    el.innerHTML = `
      <div class="sh-an">
        <p class="sh-idle">Percentiles build up here as you ask questions. The published figures below come from a 250 query offline run.</p>
        ${publishedTable(published)}
      </div>`;
    return;
  }

  const pathRows = Object.entries(paths)
    .filter(([, n]) => n > 0)
    .map(([p, n]) => `
      <div class="sh-an-path" data-path="${esc(p)}">
        <span class="sh-an-path-name">${p === "NONE" ? "ABSTAINED" : esc(p)}</span>
        <span class="sh-an-path-bar"><span style="width:${((n / total) * 100).toFixed(0)}%"></span></span>
        <span class="sh-num">${n}</span>
      </div>`).join("");

  const stageRows = analytics.stageMedians()
    .sort((x, y) => y.median - x.median)
    .map((s) => `
      <div class="sh-an-stage">
        <span>${esc(s.name)}</span>
        <span class="sh-num">${fmt(s.median, 2)} ms</span>
      </div>`).join("");

  el.innerHTML = `
    <div class="sh-an">
      <div class="sh-an-head">
        <h3 class="sh-an-title">This session</h3>
        <button class="sh-an-export" type="button">Export JSON</button>
      </div>

      ${a ? `
      <div class="sh-an-band" data-band="A">
        <p class="sh-an-band-label">Band A, core pipeline, n=${a.n}</p>
        <div class="sh-an-grid">
          ${["p50", "p70", "p90", "p100"].map((k) => `
            <div class="sh-an-cell" data-over="${a[k] > 200}">
              <span class="sh-an-k">${k.toUpperCase()}</span>
              <span class="sh-num sh-an-v">${fmt(a[k], 1)}</span>
            </div>`).join("")}
        </div>
        ${sparkline(analytics.series("A"))}
      </div>` : ""}

      ${b ? `
      <div class="sh-an-band" data-band="B">
        <p class="sh-an-band-label">Band B, generative path, n=${b.n}</p>
        <div class="sh-an-grid">
          ${["p50", "p70", "p100"].map((k) => `
            <div class="sh-an-cell" data-over="true">
              <span class="sh-an-k">${k.toUpperCase()}</span>
              <span class="sh-num sh-an-v">${fmt(b[k], 0)}</span>
            </div>`).join("")}
        </div>
      </div>` : ""}

      <div class="sh-an-block">
        <p class="sh-an-band-label">Which path answered</p>
        ${pathRows}
      </div>

      ${stageRows ? `
      <div class="sh-an-block">
        <p class="sh-an-band-label">Median per stage</p>
        <div class="sh-an-stages">${stageRows}</div>
      </div>` : ""}

      <p class="sh-fineprint">${total < 20
        ? `A P100 over ${total} sample${total === 1 ? "" : "s"} is not a tail measurement. The published numbers below come from 250 queries with 30 warmup runs discarded.`
        : "Live samples from this browser. The published numbers below remain the ones we submit."}</p>

      ${publishedTable(published)}
    </div>`;

  el.querySelector(".sh-an-export")?.addEventListener("click", () => analytics.export());
}

function publishedTable(published) {
  if (!published) return "";
  return `
    <div class="sh-an-block sh-an-published">
      <p class="sh-an-band-label">Published, 250 frozen queries</p>
      <table class="sh-table">
        <thead><tr><th>Run</th><th class="sh-th-num">P50</th><th class="sh-th-num">P70</th><th class="sh-th-num">P100</th></tr></thead>
        <tbody>
          ${published.rows.map((r) => `
            <tr data-ok="${r.inBudget}">
              <td>${esc(r.label)}</td>
              <td class="sh-num">${fmt(r.p50, 1)}</td>
              <td class="sh-num">${fmt(r.p70, 1)}</td>
              <td class="sh-num">${fmt(r.p100, 1)}</td>
            </tr>`).join("")}
        </tbody>
      </table>
    </div>`;
}

/* ------------------------------------------------------------------ */
/* Health                                                              */
/* ------------------------------------------------------------------ */

export function renderHealth(el, h) {
  const core = h.core?.status === "ok";
  const gw = h.gateway?.status === "ok";
  el.dataset.up = String(core);
  // The label is a separate element so a narrow screen can drop the words and
  // keep the dot, which is the part carrying the state.
  el.innerHTML = `
    <span class="sh-health-item" data-up="${core}">
      <span class="sh-health-dot"></span>
      <span class="sh-health-label">rag_core${core && h.core?.reranker ? ` · ${esc(h.core.reranker)}` : ""}</span>
    </span>
    <span class="sh-health-item" data-up="${gw}">
      <span class="sh-health-dot"></span>
      <span class="sh-health-label">speech${gw ? "" : " offline"}</span>
    </span>`;
}
