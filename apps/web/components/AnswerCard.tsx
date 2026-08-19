"use client";

import { useState } from "react";
import type { AnswerResponse, Citation } from "@/lib/api";

/**
 * Design.md 7.2. A chip that expands in place to show its source passage.
 *
 * Expansion is a <button> with aria-expanded rather than a hover-only reveal,
 * because hover does not exist on the phone someone will watch the demo video on
 * and then open the live link from.
 */
function CitationChip({ index, citation }: { index: number; citation: Citation }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        className="chip t-mono-sm"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        [{index}] {citation.passage_id} · {citation.score.toFixed(2)}
      </button>
      {open && (
        <p className="t-body chip-body" style={{ flexBasis: "100%" }}>
          {citation.text}
        </p>
      )}
    </>
  );
}

/**
 * Design.md 7.3: an abstention gets EQUAL visual weight to an answer, and is
 * never rendered as an error. It is a correct outcome. Restrained and factual.
 *
 * The inline gauge is the guardrail requirement made visible in one glance - a
 * judge watching a video understands instantly that the system measured its own
 * uncertainty and declined, which is worth more than guardrail code they cannot
 * see.
 */
function Abstention({ res, floor }: { res: AnswerResponse; floor: number }) {
  const top1 = res.confidence.rerank_top1;
  // The score scale is a raw cross-encoder logit running roughly -11..+11, so
  // both ends are mapped into 0..1 for the bar. Showing the raw number next to
  // the bar keeps the real value honest while the bar carries the comparison.
  const norm = (v: number) => Math.max(0, Math.min(1, (v + 11) / 22));

  return (
    <article className="surface" data-kind="abstain">
      <h2 className="t-title" style={{ margin: 0 }}>
        Did not answer
      </h2>
      <p className="t-mono-sm" style={{ color: "var(--signal-halt)", marginTop: "8px" }}>
        {res.abstain_reason ?? "LOW_CONFIDENCE"}
      </p>

      <p className="t-body reason-body">
        {top1 === null
          ? "Retrieval returned nothing for this question."
          : res.abstain_reason === "UNGROUNDED_OUTPUT"
            ? "Passages were retrieved, but reading them against the question showed they do not contain the answer. Answering anyway would mean inventing one."
            : "The best matching passage scored below the floor calibrated on the development set. Nothing in the indexed corpus reliably answers this question."}
      </p>

      {top1 !== null && (
        <div className="gauge t-mono-sm">
          <span className="gauge-label">top match</span>
          <span>{top1.toFixed(3)}</span>
          <span className="gauge-track">
            <span
              className="gauge-fill"
              style={{ width: `${norm(top1) * 100}%`, background: "var(--signal-halt)" }}
            />
          </span>

          <span className="gauge-label">floor</span>
          <span>{floor.toFixed(3)}</span>
          <span className="gauge-track">
            <span
              className="gauge-fill"
              style={{ width: `${norm(floor) * 100}%`, background: "var(--paper-700)" }}
            />
          </span>
        </div>
      )}
    </article>
  );
}

export function AnswerCard({
  res,
  floor,
}: {
  res: AnswerResponse | null;
  floor: number;
}) {
  if (!res) return null;

  if (res.status === "ABSTAINED") {
    return <Abstention res={res} floor={floor} />;
  }

  const total = res.trace?.total_ms;
  const top1 = res.confidence.rerank_top1;

  return (
    <article className="surface">
      <p className="t-display answer-text">{res.answer}</p>

      {res.citations.length > 0 && (
        <div className="chips">
          {res.citations.map((c, i) => (
            <CitationChip key={c.passage_id} index={i + 1} citation={c} />
          ))}
        </div>
      )}

      <p className="t-mono-sm meta">
        {res.path}
        {total !== undefined ? ` · ${total.toFixed(1)}ms` : ""}
        {top1 !== null ? ` · confidence ${top1.toFixed(2)}` : ""}
      </p>
    </article>
  );
}
