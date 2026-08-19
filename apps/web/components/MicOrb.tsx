"use client";

import { useEffect, useRef } from "react";
import type { RecorderState } from "@/lib/audio/recorder";

/**
 * Design.md 5. The one piece of the interface that should feel alive.
 *
 * The amplitude ring is driven imperatively through a ref rather than through
 * React state, and that is deliberate: it updates every animation frame, and
 * routing 60 setState calls a second through the reconciler would make the rest
 * of the page janky in order to animate one border. React owns the structure;
 * rAF owns this one transform.
 */
export function MicOrb({
  state,
  flash,
  getAmplitude,
  onToggle,
}: {
  state: RecorderState;
  flash: "answered" | "abstained" | null;
  getAmplitude: () => number;
  onToggle: () => void;
}) {
  const ring = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (state !== "listening") {
      if (ring.current) ring.current.style.transform = "scale(1)";
      return;
    }
    let raf = 0;
    const tick = () => {
      const a = getAmplitude();
      // 1.0 at silence to 1.18 at full voice. Larger and the ring collides with
      // the layout; smaller and it reads as a rendering artifact rather than as
      // a response to the speaker.
      if (ring.current) ring.current.style.transform = `scale(${1 + a * 0.18})`;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [state, getAmplitude]);

  const label =
    state === "listening"
      ? "Stop listening"
      : state === "processing"
        ? "Working"
        : "Start listening";

  return (
    <div className="orb-wrap">
      <button
        className="orb"
        data-state={state}
        data-flash={flash ?? undefined}
        onClick={onToggle}
        disabled={state === "processing" || state === "requesting"}
        aria-label={label}
      >
        <span className="orb-core" />
        <span className="orb-ring" ref={ring} />
      </button>
      <p className="t-body orb-hint">
        {state === "listening" ? (
          <>
            Listening. <strong>Click to stop</strong>, or just stop talking.
          </>
        ) : state === "processing" ? (
          <>Transcribing and retrieving.</>
        ) : state === "requesting" ? (
          <>Waiting for microphone permission.</>
        ) : state === "error" ? (
          <>Microphone unavailable. Type a question instead.</>
        ) : (
          <>
            Ask in <strong>English</strong> or <strong>Hindi</strong>. Click to speak.
          </>
        )}
      </p>
    </div>
  );
}
