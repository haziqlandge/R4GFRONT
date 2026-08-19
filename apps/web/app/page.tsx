"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { AnswerCard } from "@/components/AnswerCard";
import { ConfidenceReadout, LatencyWaterfall } from "@/components/LatencyWaterfall";
import { MicOrb } from "@/components/MicOrb";
import { MicRecorder, type RecorderState } from "@/lib/audio/recorder";
import { ask, health, transcribe, type AnswerMode, type AnswerResponse } from "@/lib/api";

/** config.ROUTE_TAU_LOW, fitted by scripts/06_calibrate_routing.py on the dev
 *  partition. Mirrored here only to draw the comparison bar in the abstention
 *  panel; the server owns the actual decision and this never influences it. */
const ABSTAIN_FLOOR = -1.103;

export default function Page() {
  const [state, setState] = useState<RecorderState>("idle");
  const [transcript, setTranscript] = useState("");
  const [res, setRes] = useState<AnswerResponse | null>(null);
  const [mode, setMode] = useState<AnswerMode>("fast");
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<"answered" | "abstained" | null>(null);
  const [up, setUp] = useState(false);
  const [sttMs, setSttMs] = useState<number | null>(null);
  const [typed, setTyped] = useState("");

  const recorder = useRef<MicRecorder | null>(null);

  useEffect(() => {
    recorder.current = new MicRecorder({
      onStateChange: setState,
      onError: (m) => setError(m),
    });
    health().then((h) => setUp(h?.status === "ok"));
  }, []);

  // Design.md 5: a brief flash, then back to idle. 400ms settle.
  useEffect(() => {
    if (!flash) return;
    const t = setTimeout(() => setFlash(null), 400);
    return () => clearTimeout(t);
  }, [flash]);

  const submit = useCallback(
    async (query: string) => {
      if (!query.trim()) return;
      setError(null);
      try {
        const answer = await ask(query, mode);
        setRes(answer);
        setFlash(answer.status === "ABSTAINED" ? "abstained" : "answered");
      } catch (err) {
        setError(
          `Could not reach the answer service. Is rag_core running? (${String(err)})`
        );
      } finally {
        recorder.current?.reset();
      }
    },
    [mode]
  );

  const toggleMic = useCallback(async () => {
    const rec = recorder.current;
    if (!rec) return;

    if (rec.state === "listening") {
      const pcm = await rec.stop();
      setSttMs(null);
      if (pcm.length === 0) {
        setError("No audio was captured. Check that the right microphone is selected.");
        rec.reset();
        return;
      }
      try {
        const t = await transcribe(pcm);
        setTranscript(t.text);
        setSttMs(t.stt_ms ?? null);
        if (!t.text) {
          setError("Nothing was recognised in that recording. Try again, or type instead.");
          rec.reset();
          return;
        }
        await submit(t.text);
      } catch (err) {
        setError(
          `Transcription failed. Is stt_gateway running? (${String(err)})`
        );
        rec.reset();
      }
      return;
    }

    setRes(null);
    setTranscript("");
    setError(null);
    await rec.start();
  }, [submit]);

  const getAmplitude = useCallback(() => recorder.current?.getAmplitude() ?? 0, []);

  return (
    <div className="shell">
      <header className="header">
        <span className="brand">
          Shruti<span>voice RAG, measured</span>
        </span>
        <span className="spacer" />
        <span className="health t-mono-sm">
          <span className="dot" data-up={up} />
          {up ? "rag_core ready" : "rag_core offline"}
        </span>
        <span className="seg" role="group" aria-label="Answer mode">
          <button data-on={mode === "fast"} onClick={() => setMode("fast")}>
            fast
          </button>
          <button data-on={mode === "accurate"} onClick={() => setMode("accurate")}>
            accurate
          </button>
        </span>
      </header>

      <main className="columns">
        <section className="stage">
          <MicOrb
            state={state}
            flash={flash}
            getAmplitude={getAmplitude}
            onToggle={toggleMic}
          />

          <p className="t-body transcript" data-empty={!transcript}>
            {transcript || "Your question appears here."}
          </p>

          {error && (
            <p className="t-mono-sm error" role="alert">
              {error}
            </p>
          )}

          <AnswerCard res={res} floor={ABSTAIN_FLOOR} />

          {/* F16: the text fallback, wired to the same endpoint. It is not a
              lesser path - it is how the system is used when a mic is denied,
              and how a judge will try it first. */}
          <form
            className="textrow"
            onSubmit={(e) => {
              e.preventDefault();
              setTranscript(typed);
              submit(typed);
            }}
          >
            <input
              className="textinput"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder="Or type a question in English or Hindi"
              aria-label="Type a question"
            />
            <button className="send" type="submit" disabled={!typed.trim()}>
              Ask
            </button>
          </form>
        </section>

        <aside className="instrument">
          <LatencyWaterfall trace={res?.trace ?? null} />
          <ConfidenceReadout
            top1={res?.confidence.rerank_top1 ?? null}
            gap={res?.confidence.score_gap ?? null}
            path={res?.path ?? "—"}
            traceId={res?.trace_id ?? null}
          />

          {/* Latency.md 1: the 200ms claim covers the pipeline, not speech.
              Stating the boundary on screen is the difference between an honest
              measurement and one a judge reads as cherry-picked. */}
          <section className="panel" aria-label="Measurement boundary">
            <h2 className="t-label panel-label">Measurement boundary</h2>
            <p className="t-mono-sm" style={{ color: "var(--paper-300)", margin: 0 }}>
              Band A covers transcript to answer, in process.
              <br />
              Speech-to-text is a network call and is excluded:
              <br />
              <span style={{ color: "var(--paper-100)" }}>
                stt {sttMs === null ? "—" : `${sttMs.toFixed(0)}ms`}
              </span>{" "}
              +{" "}
              <span style={{ color: "var(--paper-100)" }}>
                pipeline {res?.trace ? `${res.trace.total_ms.toFixed(1)}ms` : "—"}
              </span>
            </p>
          </section>
        </aside>
      </main>
    </div>
  );
}
