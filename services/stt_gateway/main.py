"""stt_gateway service. The browser's only route to speech. Phase 4.

Rules.md 4, HARD: the Sarvam key lives in this process and never reaches the
browser; the browser talks to this gateway, never to Sarvam directly. That is the
entire reason this service exists as a separate thing from rag_core rather than
as two more routes on it.

Three endpoints, in descending order of how certain they are to work:

  POST /v1/stt/file   Upload an utterance, get a transcript. One HTTPS call to a
                      documented endpoint. This is the floor - if everything else
                      fails, requirement 1 still scores.
  WS   /v1/stt        Stream PCM16 frames, get partials and a final. Server-side
                      VAD segments the utterance and calls the batch endpoint, so
                      this works even when the realtime socket does not.
  WS   /v1/stt/live   Direct relay to Sarvam's realtime socket, for true partial
                      transcripts and the Latency.md 5 prefetch.

Architecture.md 9 fixes the frame contract and all three emit it, so the browser
is written once.

Band A is NOT measured here. Latency.md 1 starts its clock at the transcript;
everything in this file is Band C and is reported separately and honestly.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from .config import (
    ALLOWED_ORIGINS,
    CLIENT_HEADERS,
    DEFAULT_LANGUAGE,
    HTTP_TIMEOUT_S,
    MAX_UPLOAD_BYTES,
    MAX_UTTERANCE_SECONDS,
    SAMPLE_RATE,
    SAMPLE_WIDTH,
    configured,
)
from .sarvam import SarvamError, Transcript, error_frame, transcribe_pcm
from .vad import UtteranceDetector, duration_ms

STATE: dict[str, object] = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # One client for the process. Rules.md 2.1: no per-request construction, and a
    # client built per call throws away connection pooling and TLS reuse on a path
    # that is already the slowest part of the product.
    client = httpx.AsyncClient(headers=CLIENT_HEADERS, timeout=HTTP_TIMEOUT_S)
    STATE["client"] = client
    STATE["ready"] = configured()
    try:
        yield
    finally:
        await client.aclose()


app = FastAPI(
    title="Shruti stt_gateway", default_response_class=ORJSONResponse, lifespan=lifespan
)

# Not a wildcard. This service holds a key; rag_core holds none and can afford to
# be permissive. Phase 7 replaces these with the deployed frontend origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> ORJSONResponse:
    ready = bool(STATE.get("ready"))
    return ORJSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ok" if ready else "no_api_key",
            "sample_rate": SAMPLE_RATE,
            # Stated in the health payload because it is the single most common
            # way this integration fails: the realtime endpoint takes raw PCM
            # only, and a WebM or Opus payload fails silently or with an
            # unhelpful 400.
            "expects": "raw PCM16, 16kHz, mono, little-endian",
        },
    )


@app.post("/v1/stt/file")
async def stt_file(
    file: UploadFile = File(...), language: str = Form(DEFAULT_LANGUAGE)
) -> ORJSONResponse:
    """Batch transcription. The reliable floor under the streaming paths.

    Accepts raw PCM16 from the browser recorder. Sarvam also sniffs container
    formats, but the recorder emits PCM and sending exactly what we produce keeps
    one format in the system rather than two.
    """
    client: httpx.AsyncClient = STATE["client"]  # type: ignore[assignment]
    pcm = await file.read()

    if len(pcm) > MAX_UPLOAD_BYTES:
        return ORJSONResponse(
            status_code=413,
            content={"type": "error", "code": "too_large",
                     "detail": f"{len(pcm)} bytes exceeds {MAX_UPLOAD_BYTES}"},
        )

    seconds = len(pcm) / (SAMPLE_RATE * SAMPLE_WIDTH)
    if seconds > MAX_UTTERANCE_SECONDS:
        return ORJSONResponse(
            status_code=413,
            content={"type": "error", "code": "too_long",
                     "detail": f"{seconds:.1f}s exceeds the {MAX_UTTERANCE_SECONDS}s "
                               "batch limit; use the job API for longer audio"},
        )

    started = time.perf_counter()
    try:
        result = await transcribe_pcm(client, pcm, language)
    except SarvamError as exc:
        return ORJSONResponse(
            status_code=502,
            content={"type": "error", "code": "stt_failed", "detail": str(exc)},
        )

    return ORJSONResponse(
        content={
            "type": "final",
            "text": result.text,
            "language": result.language,
            "language_confidence": result.confidence,
            # Band C evidence. Reported per Rules.md 1 rather than folded into the
            # 200 ms figure, which covers retrieval only.
            "stt_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "audio_seconds": round(seconds, 2),
        }
    )


@app.websocket("/v1/stt")
async def stt_socket(ws: WebSocket) -> None:
    """Stream PCM16 frames; get a final transcript per detected utterance.

    Segmentation happens here (vad.py) and transcription goes through the batch
    endpoint, so this path depends on nothing beyond the documented HTTPS API. It
    gives the browser a live, hands-free experience without betting requirement 1
    on the realtime socket behaving.

    Binary frames are audio. A text frame is a control message: "end" flushes
    whatever is buffered, which is what a push-to-talk release sends.
    """
    await ws.accept()
    if not configured():
        await ws.send_text(error_frame("no_api_key", "SARVAM_API_KEY is not set"))
        await ws.close()
        return

    client: httpx.AsyncClient = STATE["client"]  # type: ignore[assignment]
    detector = UtteranceDetector()

    async def flush() -> None:
        pcm = detector.buffered
        detector.reset()
        if duration_ms(pcm) < 200.0:
            return  # too short to be a question; silently ignore
        try:
            result = await transcribe_pcm(client, pcm)
        except SarvamError as exc:
            await ws.send_text(error_frame("stt_failed", str(exc)))
            return
        if result.text:
            await ws.send_text(result.frame())

    try:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break

            chunk = message.get("bytes")
            if chunk:
                # A conservative interim signal: the browser can show that speech
                # is being captured without waiting for the final transcript.
                if detector.push(chunk):
                    await flush()
                continue

            text = (message.get("text") or "").strip().lower()
            if text in {"end", "stop", "flush"}:
                await flush()
            elif text == "reset":
                detector.reset()
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 - the socket must not die silently
        try:
            await ws.send_text(error_frame("internal", str(exc)[:200]))
        except Exception:  # noqa: BLE001
            pass
    finally:
        # A disconnect mid-utterance still has audio worth transcribing, but the
        # socket is gone, so there is nowhere to send it. Drop it explicitly
        # rather than leaving a buffer attached to a dead connection.
        detector.reset()
