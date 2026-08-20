"""Why do Hindi partials arrive in Latin script, and does any parameter fix it?

Reported from a real microphone on 20 Aug: speaking Hindi, the live transcript
shows English words while you talk and only switches to Devanagari on the final.
The suspicion is `language_code=auto` - detection has not settled early in the
utterance, so partials come back in whatever the model is currently guessing.

This runs the same Hindi audio through the realtime socket under several
configurations and prints the partials from each, so the fix is chosen on
evidence rather than on a plausible story.

    python scripts/08c_probe_hindi_partials.py
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
import wave
from pathlib import Path
from typing import Any, Final

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.config import load_env  # noqa: E402

TTS_URL: Final[str] = "https://api.sarvam.ai/text-to-speech"
WS_BASE: Final[str] = "wss://api.sarvam.ai/speech-to-text-realtime/ws"
SAMPLE_RATE: Final[int] = 16_000
CHUNK_BYTES: Final[int] = 3200
HINDI: Final[str] = "कतर की राजधानी क्या है और पानी का क्वथनांक क्या है?"

ARMS: Final[list[dict[str, str]]] = [
    {"name": "A  auto + fast (what ships)", "language_code": "auto", "stream_type": "fast"},
    {"name": "B  auto + balanced", "language_code": "auto", "stream_type": "balanced"},
    {"name": "C  hi-IN + fast", "language_code": "hi-IN", "stream_type": "fast"},
    {"name": "D  hi-IN + balanced", "language_code": "hi-IN", "stream_type": "balanced"},
]

# The second half of the question: pinning hi-IN fixes Hindi partials, but this
# system is bilingual and half its traffic is English. If a pinned socket mangles
# English speech then pinning is not a fix, it is a trade.
ENGLISH: Final[str] = "What is the capital of Qatar and what is the boiling point of water?"
ENGLISH_ARMS: Final[list[dict[str, str]]] = [
    {"name": "E  english audio, auto + fast", "language_code": "auto", "stream_type": "fast"},
    {"name": "F  english audio, hi-IN + fast", "language_code": "hi-IN", "stream_type": "fast"},
    {"name": "G  english audio, en-IN + fast", "language_code": "en-IN", "stream_type": "fast"},
]


def synthesize(key: str, text: str, lang: str) -> bytes:
    r = httpx.post(
        TTS_URL,
        headers={"api-subscription-key": key, "Content-Type": "application/json"},
        json={
            "text": text,
            "target_language_code": lang,
            "speaker": "priya",
            "model": "bulbul:v3",
            "speech_sample_rate": SAMPLE_RATE,
        },
        timeout=60.0,
    )
    r.raise_for_status()
    with wave.open(io.BytesIO(base64.b64decode(r.json()["audios"][0])), "rb") as w:
        return w.readframes(w.getnframes())


def devanagari_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    deva = sum(1 for c in letters if "ऀ" <= c <= "ॿ")
    return deva / len(letters)


async def run_arm(key: str, pcm: bytes, arm: dict[str, str]) -> dict[str, Any]:
    url = (
        f"{WS_BASE}?model=saaras:v3-realtime&language_code={arm['language_code']}"
        f"&stream_type={arm['stream_type']}&encoding=linear16"
        f"&sample_rate={SAMPLE_RATE}&endpointing=vad"
    )
    partials: list[str] = []
    finals: list[str] = []

    async with websockets.connect(url, additional_headers={"API-SUBSCRIPTION-KEY": key}) as ws:
        async def send() -> None:
            for i in range(0, len(pcm), CHUNK_BYTES):
                await ws.send(json.dumps({
                    "event": "audio_input",
                    "audio": base64.b64encode(pcm[i:i + CHUNK_BYTES]).decode("ascii"),
                }))
                await asyncio.sleep(0.1)
            await ws.send(json.dumps({"event": "end"}))

        async def recv() -> None:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                ev = json.loads(raw)
                name = ev.get("event")
                if name == "transcript.partial" and ev.get("text"):
                    partials.append(ev["text"])
                elif name == "transcript.final":
                    finals.append(ev.get("text", ""))
                elif name in {"session.end"}:
                    return
                elif name == "error":
                    partials.append(f"[error {ev.get('code')}: {ev.get('message')}]")
                    return

        sender = asyncio.create_task(send())
        try:
            await asyncio.wait_for(recv(), timeout=60.0)
        except asyncio.TimeoutError:
            pass
        sender.cancel()

    non_empty = [p for p in partials if p.strip()]
    return {
        "arm": arm["name"],
        "partials": len(non_empty),
        "first_partial": non_empty[0] if non_empty else None,
        "last_partial": non_empty[-1] if non_empty else None,
        "final": " ".join(finals).strip(),
        "deva_share_partials": round(
            sum(devanagari_share(p) for p in non_empty) / len(non_empty), 3
        ) if non_empty else 0.0,
        "deva_share_final": round(devanagari_share(" ".join(finals)), 3),
        "all_partials": non_empty,
    }


async def main_async(key: str) -> int:
    pcm = synthesize(key, HINDI, "hi-IN")
    print(f"spoken (hi): {HINDI}")
    print(f"audio: {len(pcm) / (SAMPLE_RATE * 2):.2f}s\n")

    rows = []
    for arm in ARMS:
        row = await run_arm(key, pcm, arm)
        rows.append(row)
        print(f"{row['arm']}")
        print(f"   partials {row['partials']:>3}   devanagari share in partials "
              f"{row['deva_share_partials']:.2f}   in final {row['deva_share_final']:.2f}")
        print(f"   first partial : {row['first_partial']!r}")
        print(f"   last partial  : {row['last_partial']!r}")
        print(f"   final         : {row['final']!r}\n", flush=True)

    pcm_en = synthesize(key, ENGLISH, "en-IN")
    print("")
    print(f"spoken (en): {ENGLISH}")
    print(f"audio: {len(pcm_en) / (SAMPLE_RATE * 2):.2f}s")
    print("")
    for arm in ENGLISH_ARMS:
        row = await run_arm(key, pcm_en, arm)
        rows.append(row)
        print(f"{row['arm']}")
        print(f"   partials {row['partials']:>3}   devanagari share in partials "
              f"{row['deva_share_partials']:.2f}   in final {row['deva_share_final']:.2f}")
        print(f"   last partial  : {row['last_partial']!r}")
        print(f"   final         : {row['final']!r}", flush=True)
        print("")

    print("=" * 78)
    for row in rows:
        verdict = ("DEVANAGARI while speaking" if row["deva_share_partials"] > 0.8
                   else "latin while speaking" if row["deva_share_partials"] < 0.2
                   else "mixed while speaking")
        print(f"{row['arm']:<32} {verdict}")
    print("=" * 78)
    return 0


def main() -> int:
    load_env()
    key = os.environ.get("SARVAM_API_KEY", "")
    if not key:
        print("SARVAM_API_KEY is not set.")
        return 1
    return asyncio.run(main_async(key))


if __name__ == "__main__":
    sys.exit(main())
