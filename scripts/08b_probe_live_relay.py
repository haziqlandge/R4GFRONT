"""End-to-end check of the /v1/stt/live relay, without a microphone.

`08_probe_realtime_stt.py` proved Sarvam emits partials. This proves OUR relay
forwards them: it connects to the deployed gateway exactly as the browser does,
over wss through Caddy, sends the same PCM16 frames the AudioWorklet produces,
and checks that partial and final frames come back in the Architecture.md 9
contract.

Everything the browser does is covered here except `getUserMedia` itself, which
needs a person and a real microphone.

    python scripts/08b_probe_live_relay.py
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
from typing import Final

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.config import load_env  # noqa: E402

WS_URL: Final[str] = "wss://shrutirag.duckdns.org/api/stt/v1/stt/live"
TTS_URL: Final[str] = "https://api.sarvam.ai/text-to-speech"
SAMPLE_RATE: Final[int] = 16_000
CHUNK_BYTES: Final[int] = 3200
SENTENCE: Final[str] = "What is the capital of Qatar?"


def synthesize(key: str, text: str) -> bytes:
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
    with wave.open(io.BytesIO(base64.b64decode(r.json()["audios"][0])), "rb") as w:
        return w.readframes(w.getnframes())


async def main_async(pcm: bytes) -> int:
    partials: list[str] = []
    final: str | None = None
    started = time.perf_counter()

    async with websockets.connect(WS_URL, max_size=None) as ws:
        async def send() -> None:
            # Binary frames, exactly what the worklet hands the page.
            for i in range(0, len(pcm), CHUNK_BYTES):
                await ws.send(pcm[i:i + CHUNK_BYTES])
                await asyncio.sleep(0.1)
            await ws.send("end")  # the text control frame the mic button sends

        async def recv() -> None:
            nonlocal final
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                frame = json.loads(raw)
                ms = (time.perf_counter() - started) * 1000.0
                kind = frame.get("type")
                if kind == "partial":
                    partials.append(frame.get("text", ""))
                    print(f"  {ms:8.1f} ms  partial  {frame.get('text')!r}", flush=True)
                elif kind == "final":
                    final = frame.get("text", "")
                    print(f"  {ms:8.1f} ms  FINAL    {final!r}", flush=True)
                    return
                elif kind == "error":
                    print(f"  {ms:8.1f} ms  ERROR    {frame}", flush=True)
                    return

        sender = asyncio.create_task(send())
        try:
            await asyncio.wait_for(recv(), timeout=45.0)
        except asyncio.TimeoutError:
            print("  timed out waiting for a final frame")
        sender.cancel()

    print("\n" + "=" * 70)
    print(f"partial frames relayed  {len(partials)}")
    print(f"final frame             {final!r}")
    print("=" * 70)
    ok = bool(partials) and bool(final)
    print("\nVERDICT: the relay works. The browser will see the question form as it is spoken."
          if ok else "\nVERDICT: the relay did NOT deliver what the browser needs.")
    return 0 if ok else 1


def main() -> int:
    load_env()
    key = os.environ.get("SARVAM_API_KEY", "")
    if not key:
        print("SARVAM_API_KEY is not set locally; needed only to synthesize test audio.")
        return 1
    pcm = synthesize(key, SENTENCE)
    print(f"spoken: {SENTENCE!r}  ({len(pcm) / (SAMPLE_RATE * 2):.2f}s)\n")
    return asyncio.run(main_async(pcm))


if __name__ == "__main__":
    sys.exit(main())
