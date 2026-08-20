"""stt_gateway configuration. Phase 4.

Separate from rag_core.config on purpose: this service holds the Sarvam key and
rag_core holds none. Rules.md 4 is HARD - no key ever reaches the browser, and the
browser talks to this gateway, never to Sarvam directly. Keeping the two configs
apart makes that boundary visible rather than a convention.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag_core.config import USER_AGENT, load_env  # noqa: E402

load_env()

SARVAM_API_KEY: Final[str] = os.environ.get("SARVAM_API_KEY", "")

# Sarvam authenticates with its own header name, NOT a bearer token. Sending
# Authorization: Bearer here returns a 401 that reads like a bad key.
AUTH_HEADER: Final[str] = "api-subscription-key"

SARVAM_STT_URL: Final[str] = "https://api.sarvam.ai/speech-to-text"
SARVAM_WS_URL: Final[str] = "wss://api.sarvam.ai/speech-to-text/ws"

# Architecture.md 3.1. saaras:v3 for the batch path, the realtime variant for the
# streaming socket that makes partial transcripts - and therefore the Latency.md 5
# prefetch - possible.
STT_MODEL: Final[str] = "saaras:v3"
STT_REALTIME_MODEL: Final[str] = "saaras:v3-realtime"

# "unknown" is Sarvam's auto-detect sentinel on the batch endpoint. The corpus is
# en+hi and users code-mix, so detection is the correct default rather than a
# client-supplied guess; the detected code comes back on the response and is
# logged as retrieval metadata.
DEFAULT_LANGUAGE: Final[str] = "unknown"

# The realtime endpoint accepts RAW PCM ONLY - 16 kHz, mono, signed 16-bit little
# endian. Sending WebM or Opus fails silently or with an unhelpful 400 (Phases.md
# Phase 4 gotchas). Browsers capture at 48 kHz, so the client downsamples before
# these frames are ever sent.
SAMPLE_RATE: Final[int] = 16_000
SAMPLE_WIDTH: Final[int] = 2
CHANNELS: Final[int] = 1

# Codec tag required by the batch endpoint when the upload is raw PCM rather than
# a container format. Omitting it on a .pcm/.raw upload produces a decode error.
PCM_CODEC: Final[str] = "pcm_s16le"

# Frame size the browser sends. 20 ms at 16 kHz = 320 samples = 640 bytes. Small
# enough that VAD reacts quickly, large enough that the socket is not the
# bottleneck.
FRAME_MS: Final[int] = 20
FRAME_BYTES: Final[int] = SAMPLE_RATE * SAMPLE_WIDTH * FRAME_MS // 1000

# Upload ceiling for the batch path. 30 s of 16 kHz mono PCM is ~960 KB; the cap
# is deliberately generous against that and still bounds a hostile upload.
MAX_UPLOAD_BYTES: Final[int] = 8 * 1024 * 1024

# Sarvam's batch endpoint documents a 30 s limit before a job must be used.
MAX_UTTERANCE_SECONDS: Final[float] = 30.0

HTTP_TIMEOUT_S: Final[float] = 30.0
WS_CONNECT_TIMEOUT_S: Final[float] = 10.0

# Where the browser is served from. A wildcard is not acceptable on a service
# that holds a key (Rules.md 4, HARD).
#
# Phase 7 note: in the deployed setup this list should never be consulted.
# Caddy serves the site and reverse proxies /api/stt/* to this process on
# localhost, so the browser's requests are SAME-ORIGIN and no CORS check runs at
# all. The deployed origin is listed anyway, because the cost is one line and
# the failure it prevents - speech failing while typing works, with a CORS
# rejection that reads exactly like a dead microphone - is one this project has
# already paid for once.
ALLOWED_ORIGINS: Final[tuple[str, ...]] = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://shrutirag.duckdns.org",
)

CLIENT_HEADERS: Final[dict[str, str]] = {
    "api-subscription-key": SARVAM_API_KEY,
    "User-Agent": USER_AGENT,
}


def configured() -> bool:
    return bool(SARVAM_API_KEY)
