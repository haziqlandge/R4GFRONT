"""Does Sarvam's realtime socket actually emit partial transcripts? Phase 4/8.

`Memory.md` A3 has been open since Phase 4 - "Sarvam's realtime endpoint emits
partials fast enough and stably enough" - and `DONT-FORGET.md` 13 says partials
are hypothetical and must not be claimed. Nothing in the repo has ever opened
that socket. This opens it.

The method avoids needing a microphone or a person: synthesize a question with
Sarvam TTS at 16 kHz, then feed the PCM to the realtime socket in 100 ms chunks
paced in real time, exactly as a browser would. Every event is logged with the
milliseconds since the first audio chunk was sent, so the answer is not just
"are there partials" but "how far behind the speech are they".

    python scripts/08_probe_realtime_stt.py

Run it on the deployed box: the key lives there, and so does the network path
the real thing will use.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
import time
import wave
from pathlib import Path
from typing import Any, Final

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.config import load_env  # noqa: E402

TTS_URL: Final[str] = "https://api.sarvam.ai/text-to-speech"
WS_URL: Final[str] = "wss://api.sarvam.ai/speech-to-text-realtime/ws"
SAMPLE_RATE: Final[int] = 16_000
CHUNK_BYTES: Final[int] = 3200  # 100 ms of 16 kHz mono PCM16
SENTENCE: Final[str] = "What is the capital of Qatar, and what is the boiling point of water?"


def synthesize(key: str, text: str) -> bytes:
    """Return 16 kHz mono PCM16 for `text`, via Sarvam TTS."""
    r = httpx.post(
        TTS_URL,
        headers={"api-subscription-key": key, "Content-Type": "application/json"},
        json={
            "text": text,
            "target_language_code": "en-IN",
            "speaker": "priya",
            "model": "bulbul:v3",
            "speech_sample_rate": SAMPLE_RATE,
        },
        timeout=60.0,
    )
    r.raise_for_status()
    wav_b64 = r.json()["audios"][0]
    with wave.open(io.BytesIO(base64.b64decode(wav_b64)), "rb") as w:
        print(f"tts: {w.getnchannels()}ch {w.getframerate()}Hz "
              f"{w.getsampwidth() * 8}bit {w.getnframes() / w.getframerate():.2f}s")
        return w.readframes(w.getnframes())


async def probe(key: str, pcm: bytes) -> dict[str, Any]:
    url = (
        f"{WS_URL}?language_code=auto&model=saaras:v3-realtime&stream_type=fast"
        f"&encoding=linear16&sample_rate={SAMPLE_RATE}&endpointing=vad"
    )
    events: list[dict[str, Any]] = []
    started = 0.0

    async with websockets.connect(url, additional_headers={"API-SUBSCRIPTION-KEY": key}) as ws:

        async def send() -> None:
            nonlocal started
            started = time.perf_counter()
            for i in range(0, len(pcm), CHUNK_BYTES):
                chunk = pcm[i:i + CHUNK_BYTES]
                await ws.send(json.dumps({
                    "event": "audio_input",
                    "audio": base64.b64encode(chunk).decode("ascii"),
                }))
                await asyncio.sleep(0.1)  # pace it like a person speaking
            await ws.send(json.dumps({"event": "end"}))

        async def recv() -> None:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                ev = json.loads(raw)
                ms = (time.perf_counter() - started) * 1000.0 if started else 0.0
                ev["_ms"] = round(ms, 1)
                events.append(ev)
                name = ev.get("event", "?")
                text = ev.get("text")
                extra = f"  {text!r}" if text else ""
                print(f"  {ms:8.1f} ms  {name}{extra}", flush=True)
                if name in {"session.end", "error"} and ev.get("is_fatal", name == "session.end"):
                    return

        sender = asyncio.create_task(send())
        try:
            await asyncio.wait_for(recv(), timeout=60.0)
        except asyncio.TimeoutError:
            print("  (timed out waiting for session.end)")
        sender.cancel()

    partials = [e for e in events if e.get("event") == "transcript.partial"]
    finals = [e for e in events if e.get("event") == "transcript.final"]
    audio_ms = len(pcm) / (SAMPLE_RATE * 2) * 1000.0
    return {
        "audio_ms": round(audio_ms, 1),
        "events": len(events),
        "partials": len(partials),
        "finals": len(finals),
        "first_partial_ms": partials[0]["_ms"] if partials else None,
        "first_partial_text": partials[0].get("text") if partials else None,
        "last_partial_ms": partials[-1]["_ms"] if partials else None,
        "final_ms": finals[0]["_ms"] if finals else None,
        "final_text": " ".join(f.get("text", "") for f in finals).strip(),
        "partial_texts": [p.get("text") for p in partials],
    }


def main() -> int:
    load_env()
    key = os.environ.get("SARVAM_API_KEY", "")
    if not key:
        print("SARVAM_API_KEY is not set. This has to run where the key lives.")
        return 1

    pcm = synthesize(key, SENTENCE)
    print(f"spoken: {SENTENCE!r}")
    print(f"pcm: {len(pcm)} bytes, {len(pcm) / (SAMPLE_RATE * 2):.2f}s\n")
    print("events, ms since first audio chunk:")
    result = asyncio.run(probe(key, pcm))

    print("\n" + "=" * 70)
    print(f"audio duration      {result['audio_ms']} ms")
    print(f"partial events      {result['partials']}")
    print(f"first partial at    {result['first_partial_ms']} ms")
    print(f"last partial at     {result['last_partial_ms']} ms")
    print(f"final at            {result['final_ms']} ms")
    print(f"final text          {result['final_text']!r}")
    print("=" * 70)
    if result["partials"]:
        print("\nVERDICT: partials ARE emitted mid-utterance. A live transcript is buildable.")
    else:
        print("\nVERDICT: no partials. Only a final arrives, so a live transcript is NOT buildable.")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
