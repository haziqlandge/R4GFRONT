"""Sarvam client. Batch transcription plus the realtime socket. Phase 4.

Two paths, and the split is deliberate risk management rather than indecision:

  transcribe_pcm()  BATCH. One buffered utterance, one HTTPS POST, one final
                    transcript. Documented, stable, and the thing requirement 1
                    is actually scored on. If the realtime socket fights back,
                    this alone still delivers voice input end to end.

  stream()          REALTIME. A WebSocket that emits partial transcripts as the
                    user speaks. This is what Latency.md 5's speculative prefetch
                    needs, and it is the better demo - but it is the riskier of
                    the two and it is layered ON TOP of a working batch path, not
                    instead of one.

Rules.md 4, HARD: the key lives here and never reaches the browser. Every function
in this module is called by our own gateway, never by a client.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import AsyncIterator, Final

import httpx

from .config import (
    CLIENT_HEADERS,
    DEFAULT_LANGUAGE,
    HTTP_TIMEOUT_S,
    PCM_CODEC,
    SAMPLE_RATE,
    SARVAM_STT_URL,
    SARVAM_WS_URL,
    STT_MODEL,
    STT_REALTIME_MODEL,
    configured,
)


class SarvamError(RuntimeError):
    """Any Sarvam failure. Typed so the gateway can emit a structured error frame
    rather than dropping the socket and leaving the browser guessing."""


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str
    confidence: float | None = None
    is_final: bool = True

    def frame(self) -> str:
        """Serialize to the WS contract in Architecture.md 9.

        The browser is written against this shape once and must not have to care
        whether a transcript arrived over the socket or from the batch endpoint.
        """
        return json.dumps(
            {
                "type": "final" if self.is_final else "partial",
                "text": self.text,
                "language": self.language,
                "language_confidence": self.confidence,
            },
            ensure_ascii=False,
        )


def error_frame(code: str, detail: str = "") -> str:
    return json.dumps({"type": "error", "code": code, "detail": detail})


async def transcribe_pcm(
    client: httpx.AsyncClient, pcm: bytes, language: str = DEFAULT_LANGUAGE
) -> Transcript:
    """Transcribe one buffered utterance of raw PCM16.

    `input_audio_codec` is REQUIRED for raw PCM - the endpoint accepts container
    formats by sniffing them, and raw samples have nothing to sniff. Omitting it
    on a .pcm upload produces a decode error that reads like corrupt audio.
    """
    if not configured():
        raise SarvamError("SARVAM_API_KEY is not set")
    if not pcm:
        raise SarvamError("empty audio")

    files = {"file": ("utterance.pcm", pcm, "application/octet-stream")}
    data = {
        "model": STT_MODEL,
        "language_code": language,
        "input_audio_codec": PCM_CODEC,
        "with_timestamps": "false",
    }
    try:
        resp = await client.post(
            SARVAM_STT_URL, data=data, files=files, timeout=HTTP_TIMEOUT_S
        )
    except httpx.HTTPError as exc:
        raise SarvamError(f"transport: {exc}") from exc

    if resp.status_code == 401:
        raise SarvamError("401 - check SARVAM_API_KEY and the api-subscription-key header")
    if resp.status_code == 429:
        raise SarvamError("429 - Sarvam rate limit")
    if resp.status_code >= 400:
        raise SarvamError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    body = resp.json()
    return Transcript(
        text=(body.get("transcript") or "").strip(),
        language=body.get("language_code") or language,
        confidence=body.get("language_probability"),
        is_final=True,
    )


# The realtime socket's query parameters, corrected against the documented
# contract on 20 Aug. Three of the five were wrong and two of those were silent:
#
#   stream_type=vad         -> "fast". The allowed values are fast, balanced and
#                              simulated; "vad" is not one of them. Segmentation
#                              is `endpointing`, which is a different parameter.
#   input_audio_codec=...   -> `encoding`, whose value here is linear16 rather
#                              than the batch endpoint's pcm_s16le. Same bytes,
#                              different vocabulary on a different endpoint.
#   (no endpointing)        -> endpointing=vad, so Sarvam segments on silence and
#                              emits vad.speech_start / vad.speech_end.
#
# `fast` rather than `balanced` because this drives a caret on screen: a partial
# that is 300 ms fresher is worth more here than one that is slightly more
# considered, and the FINAL is what gets sent to rag_core either way.
_WS_PARAMS: Final[str] = (
    f"?model={STT_REALTIME_MODEL}&stream_type=fast&language_code=auto"
    f"&sample_rate={SAMPLE_RATE}&encoding=linear16&endpointing=vad"
)


def realtime_url() -> str:
    return f"{SARVAM_WS_URL}{_WS_PARAMS}"


def parse_event(raw: str) -> Transcript | None:
    """Normalise one Sarvam realtime event into our contract.

    Sarvam's event vocabulary has shifted across versions and is not worth
    hard-coding narrowly, so this accepts the shapes seen in the wild and returns
    None for anything that is not a transcript (keepalives, session acks). The
    caller forwards only what this produces, which keeps the browser contract
    stable regardless of what the upstream adds.
    """
    try:
        ev = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(ev, dict):
        return None

    kind = str(ev.get("type") or ev.get("event") or "").lower()
    nested = ev.get("data")
    data: dict[str, object] = nested if isinstance(nested, dict) else ev

    text = data.get("transcript") or data.get("text") or ""
    if not text:
        return None

    final = any(k in kind for k in ("final", "complete", "end")) or bool(
        data.get("is_final")
    )
    raw_conf = data.get("language_probability") or data.get("language_confidence")
    confidence = float(raw_conf) if isinstance(raw_conf, (int, float)) else None
    return Transcript(
        text=str(text).strip(),
        language=str(data.get("language_code") or data.get("language") or "unknown"),
        confidence=confidence,
        is_final=final,
    )


async def stream_events(ws: object) -> AsyncIterator[Transcript]:
    """Yield normalised transcripts from an open Sarvam realtime socket."""
    async for raw in ws:  # type: ignore[attr-defined]
        if isinstance(raw, bytes):
            continue
        parsed = parse_event(raw)
        if parsed is not None:
            yield parsed
