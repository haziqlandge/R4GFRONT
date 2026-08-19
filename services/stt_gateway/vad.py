"""Server-side utterance segmentation for the batch path. Phase 4.

Sarvam's realtime socket does its own VAD (`stream_type=vad`), so this exists for
the BATCH path, where the gateway has to decide on its own when the speaker
stopped and it is time to send the buffer.

Deliberately energy-based rather than a model. A neural VAD would be another ONNX
session, another cold start and another thing to be wrong about, to answer a
question - "has this been quiet for 700 ms?" - that RMS answers adequately on
16 kHz mono speech. Rules.md 1: the budget is judged first.

This is NOT on the 200 ms hot path. It runs while the user is still talking, and
Latency.md 1 starts the Band A clock at the transcript, not at the microphone.
"""

from __future__ import annotations

import array
import math
from typing import Final

from .config import FRAME_BYTES, SAMPLE_RATE, SAMPLE_WIDTH

# RMS below this counts as silence. int16 full scale is 32768; 300 is roughly
# -40 dBFS, which sits above typical room tone and below speech on a laptop mic.
SILENCE_RMS: Final[float] = 300.0

# Silence needed to declare end-of-speech. Below ~500 ms this fires inside the
# natural pauses of a spoken question and cuts the user off mid-sentence.
SILENCE_HANG_MS: Final[int] = 700

# Speech needed before end-of-speech can trigger at all, so that a click or a
# cough does not open and immediately close an utterance.
MIN_SPEECH_MS: Final[int] = 300


def rms(pcm: bytes) -> float:
    """Root-mean-square amplitude of a signed 16-bit little-endian frame."""
    if len(pcm) < SAMPLE_WIDTH:
        return 0.0
    samples = array.array("h")
    usable = len(pcm) - (len(pcm) % SAMPLE_WIDTH)
    samples.frombytes(pcm[:usable])
    if not samples:
        return 0.0
    total = 0
    for s in samples:
        total += s * s
    return math.sqrt(total / len(samples))


def duration_ms(pcm: bytes) -> float:
    return 1000.0 * len(pcm) / (SAMPLE_RATE * SAMPLE_WIDTH)


class UtteranceDetector:
    """Accumulates PCM frames and reports when an utterance has ended.

    One instance per connection. Not shared, not reset across sockets - the state
    IS the utterance, and reusing it across speakers is how a previous caller's
    tail ends up prefixed to the next one's question.
    """

    def __init__(
        self,
        silence_rms: float = SILENCE_RMS,
        hang_ms: int = SILENCE_HANG_MS,
        min_speech_ms: int = MIN_SPEECH_MS,
    ) -> None:
        self.silence_rms = silence_rms
        self.hang_ms = hang_ms
        self.min_speech_ms = min_speech_ms
        self._buf = bytearray()
        self._speech_ms = 0.0
        self._silence_ms = 0.0

    @property
    def buffered(self) -> bytes:
        return bytes(self._buf)

    @property
    def speech_ms(self) -> float:
        return self._speech_ms

    def reset(self) -> None:
        self._buf.clear()
        self._speech_ms = 0.0
        self._silence_ms = 0.0

    def push(self, frame: bytes) -> bool:
        """Add a frame. Returns True when the utterance is complete.

        Silence before any speech is discarded rather than buffered, so a user who
        holds the mic open for five seconds before speaking does not send five
        seconds of room tone to a paid endpoint.
        """
        loud = rms(frame) >= self.silence_rms
        ms = duration_ms(frame)

        if loud:
            self._buf.extend(frame)
            self._speech_ms += ms
            self._silence_ms = 0.0
            return False

        if self._speech_ms <= 0.0:
            return False  # leading silence, drop it

        # trailing silence is kept: clipping the tail truncates final consonants
        self._buf.extend(frame)
        self._silence_ms += ms
        return (
            self._silence_ms >= self.hang_ms
            and self._speech_ms >= self.min_speech_ms
        )


def frames(pcm: bytes, size: int = FRAME_BYTES) -> list[bytes]:
    """Split a buffer into fixed frames, discarding a trailing partial frame."""
    return [pcm[i : i + size] for i in range(0, len(pcm) - size + 1, size)]
