"use client";

import type { StageSpan, TraceView } from "@/lib/api";

/**
 * Design.md 6. The signature component.
 *
 * Constraint that shapes everything here: it has to stay legible in a compressed
 * Instagram video at arm's length. That is why the bars are chunky, the stage
 * names are truncated rather than wrapped, and the total is set at 20px mono
 * against 12px labels - extreme contrast in one place beats moderate contrast
 * everywhere (Design.md 9).
 *
 * The 200ms rule is drawn even when the total is nowhere near it. Its presence is
 * what makes the number mean anything; a bar chart with no reference line is just
 * a bar chart.
 */

/** Axis span. Fixed at the budget so bar lengths are comparable between runs and
 *  the rule sits in the same place every time - an axis that rescales to the data
 *  would make a fast run and a slow run look identical. */
const AXIS_MS = 200;

function barWidth(ms: number): string {
  return `${Math.min(100, (ms / AXIS_MS) * 100)}%`;
}

export function LatencyWaterfall({ trace }: { trace: TraceView | null }) {
  const stages: StageSpan[] = trace?.stages ?? [];
  const total = trace?.total_ms ?? 0;
  const over = total > AXIS_MS;

  return (
    <section className="panel" aria-label="Per-stage latency">
      <h2 className="t-label panel-label">Latency waterfall</h2>

      {stages.length === 0 ? (
        <p className="t-mono-sm" style={{ color: "var(--paper-700)" }}>
          Ask a question to trace the pipeline.
        </p>
      ) : (
        <>
          {stages.map((s) => (
            <div className="wf-row" key={`${s.name}-${s.ms}`}>
              <span className="t-mono-sm wf-name" title={s.detail ?? s.name}>
                {s.name}
              </span>
              <span className="wf-track">
                <span
                  className="wf-bar"
                  data-status={s.status}
                  data-over={over ? "true" : "false"}
                  style={{ width: barWidth(s.ms) }}
                />
                {/* the 200ms rule sits at 100% of a 200ms axis */}
                <span className="wf-budget" style={{ left: "100%" }} />
              </span>
              <span className="t-mono-sm wf-ms">
                {s.status === "skipped" ? "skip" : `${s.ms.toFixed(1)}`}
              </span>
            </div>
          ))}

          <div className="t-mono-sm wf-axis">
            <span>0</span>
            <span>{AXIS_MS}ms budget</span>
          </div>

          <div className="wf-total">
            <span className="t-label" style={{ color: "var(--paper-500)" }}>
              Total
            </span>
            <span className="t-mono-lg wf-total-value" data-over={over ? "true" : "false"}>
              {total.toFixed(1)} ms
            </span>
          </div>
        </>
      )}
    </section>
  );
}

/**
 * Confidence readout.
 *
 * The score is shown against the floor that produced the decision, not on its
 * own. A bare "0.19" tells a viewer nothing; "0.19 against a floor of -1.10"
 * tells them the system made a measurement and compared it to something. That
 * comparison is the whole of requirement 6 made visible.
 */
export function ConfidenceReadout({
  top1,
  gap,
  path,
  traceId,
}: {
  top1: number | null;
  gap: number | null;
  path: string;
  traceId: string | null;
}) {
  return (
    <section className="panel" aria-label="Confidence">
      <h2 className="t-label panel-label">Confidence</h2>
      <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "1fr auto", gap: "8px 12px" }}>
        <dt className="t-mono-sm" style={{ color: "var(--paper-500)" }}>rerank top-1</dt>
        <dd className="t-mono-sm" style={{ margin: 0 }}>
          {top1 === null ? "—" : top1.toFixed(3)}
        </dd>
        <dt className="t-mono-sm" style={{ color: "var(--paper-500)" }}>score gap</dt>
        <dd className="t-mono-sm" style={{ margin: 0 }}>
          {gap === null ? "—" : gap.toFixed(3)}
        </dd>
        <dt className="t-mono-sm" style={{ color: "var(--paper-500)" }}>path</dt>
        <dd className="t-mono-sm" style={{ margin: 0 }}>{path}</dd>
        <dt className="t-mono-sm" style={{ color: "var(--paper-500)" }}>trace</dt>
        <dd className="t-mono-sm" style={{ margin: 0, color: "var(--paper-500)" }}>
          {traceId ? traceId.slice(0, 12) : "—"}
        </dd>
      </dl>
    </section>
  );
}
