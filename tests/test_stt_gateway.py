"""stt_gateway: VAD segmentation and the Sarvam event contract. Phase 4.

No network. The live path was verified separately by round-tripping Sarvam TTS
through Sarvam STT (see the Phase 4 Memory.md entry); what is pinned here is the
logic that decides WHEN to send audio and how upstream events become the frame
shape the browser is written against.

Getting the VAD wrong is not a crash, it is a product that cuts people off
mid-sentence or never responds - both of which look like a broken demo rather than
a tuning problem.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from stt_gateway.sarvam import Transcript, error_frame, parse_event  # noqa: E402
from stt_gateway.vad import (  # noqa: E402
    SILENCE_RMS,
    UtteranceDetector,
    duration_ms,
    frames,
    rms,
)
from stt_gateway.config import FRAME_BYTES, SAMPLE_RATE  # noqa: E402


def tone(ms: int, amplitude: int = 6000) -> bytes:
    """`ms` of loud audio as PCM16LE. Alternating sign keeps RMS at amplitude."""
    n = SAMPLE_RATE * ms // 1000
    return b"".join(
        struct.pack("<h", amplitude if i % 2 == 0 else -amplitude) for i in range(n)
    )


def silence(ms: int) -> bytes:
    return b"\x00\x00" * (SAMPLE_RATE * ms // 1000)


# -- framing ----------------------------------------------------------------


def test_frame_size_is_20ms_at_16khz() -> None:
    """640 bytes. If this drifts, every VAD duration below is wrong."""
    assert FRAME_BYTES == 640
    assert duration_ms(b"\x00" * FRAME_BYTES) == 20.0


def test_frames_discards_a_trailing_partial() -> None:
    """A half frame at the end must not be padded into existence - padding it with
    zeros injects silence the speaker never produced."""
    assert len(frames(b"\x00" * (FRAME_BYTES * 3 + 7))) == 3


def test_rms_separates_speech_from_silence() -> None:
    assert rms(silence(20)) == 0.0
    assert rms(tone(20)) > SILENCE_RMS


def test_rms_tolerates_an_odd_byte_count() -> None:
    """A truncated frame off the socket must not raise inside the audio loop."""
    assert rms(b"\x01") == 0.0


# -- utterance segmentation -------------------------------------------------


def push_all(det: UtteranceDetector, pcm: bytes) -> bool:
    fired = False
    for f in frames(pcm):
        if det.push(f):
            fired = True
    return fired


def test_leading_silence_is_discarded_not_buffered() -> None:
    """A user who opens the mic and pauses must not ship room tone to a paid
    endpoint, or pay latency to transcribe it."""
    det = UtteranceDetector()
    push_all(det, silence(2000))
    assert det.buffered == b""
    assert det.speech_ms == 0.0


def test_end_of_speech_fires_after_the_hang_time() -> None:
    det = UtteranceDetector()
    assert not push_all(det, tone(800)), "must not fire while still speaking"
    assert push_all(det, silence(800)), "must fire once the hang time elapses"


def test_short_pause_does_not_cut_the_speaker_off() -> None:
    """People pause inside a spoken question. A 300ms gap is mid-sentence, not the
    end of one - firing here is the failure that reads as a broken demo."""
    det = UtteranceDetector()
    assert not push_all(det, tone(600) + silence(300) + tone(600))
    assert det.speech_ms >= 1000.0


def test_a_click_is_not_an_utterance() -> None:
    """Below min_speech_ms, silence must not trigger a transcription request."""
    det = UtteranceDetector()
    assert not push_all(det, tone(100) + silence(1500))


def test_trailing_silence_is_kept() -> None:
    """Clipping the tail truncates final consonants and changes the transcript."""
    det = UtteranceDetector()
    push_all(det, tone(800) + silence(800))
    assert duration_ms(det.buffered) > 800.0


def test_reset_clears_state_between_utterances() -> None:
    """State is the utterance. Leaking it across resets prefixes one speaker's
    tail onto the next question."""
    det = UtteranceDetector()
    push_all(det, tone(800))
    det.reset()
    assert det.buffered == b""
    assert det.speech_ms == 0.0


def test_detector_is_reusable_for_a_second_utterance() -> None:
    det = UtteranceDetector()
    assert push_all(det, tone(800) + silence(800))
    det.reset()
    assert push_all(det, tone(800) + silence(800))


# -- the browser-facing contract --------------------------------------------


def test_transcript_frame_matches_architecture_9() -> None:
    payload = json.loads(
        Transcript("नमस्ते", "hi-IN", 0.94, is_final=True).frame()
    )
    assert payload == {
        "type": "final",
        "text": "नमस्ते",
        "language": "hi-IN",
        "language_confidence": 0.94,
    }


def test_partial_and_final_are_distinguishable() -> None:
    assert json.loads(Transcript("a", "en-IN", is_final=False).frame())["type"] == "partial"
    assert json.loads(Transcript("a", "en-IN", is_final=True).frame())["type"] == "final"


def test_devanagari_survives_serialization() -> None:
    """ensure_ascii=False, or the browser receives escape sequences. Memory.md
    Phase 2 already lost time to a non-ASCII transport bug."""
    assert "नमस्ते" in Transcript("नमस्ते", "hi-IN").frame()


def test_error_frame_is_typed() -> None:
    payload = json.loads(error_frame("stt_failed", "boom"))
    assert payload["type"] == "error"
    assert payload["code"] == "stt_failed"


# -- upstream event normalisation -------------------------------------------


def test_parse_event_accepts_a_nested_data_envelope() -> None:
    ev = parse_event(json.dumps(
        {"type": "transcript.partial", "data": {"transcript": "hello", "language_code": "en-IN"}}
    ))
    assert ev is not None and ev.text == "hello" and not ev.is_final


def test_parse_event_accepts_a_flat_shape() -> None:
    ev = parse_event(json.dumps({"event": "final", "text": "done", "language": "en-IN"}))
    assert ev is not None and ev.is_final


def test_parse_event_ignores_non_transcripts() -> None:
    """Keepalives and session acks must not surface as empty transcripts in the
    UI. Returning None keeps the browser contract to transcripts only."""
    assert parse_event(json.dumps({"type": "session.started"})) is None
    assert parse_event(json.dumps({"type": "partial", "transcript": ""})) is None


def test_parse_event_survives_garbage() -> None:
    """An upstream protocol change must degrade to silence, not to a 500 that
    kills the socket mid-demo."""
    assert parse_event("not json") is None
    assert parse_event("[1,2,3]") is None


# -- the service actually starts --------------------------------------------
#
# These exist because the module tests above ALL PASSED while the service could
# not boot. test_stt_gateway.py imported sarvam, vad and config but never main,
# and FastAPI resolves Form/UploadFile parameters when it REGISTERS a route, so
# a missing python-multipart raised at import time and took the whole app down -
# not just the upload endpoint. 198 green tests said nothing about it.
#
# Importing the app is the cheapest possible proxy for "uvicorn can start this".


def test_app_imports() -> None:
    """If this raises, the service cannot boot, whatever the unit tests say."""
    from stt_gateway.main import app

    assert app is not None


def test_every_documented_route_is_registered() -> None:
    """Architecture.md 9 fixes WS /v1/stt. The upload route is the fallback that
    keeps requirement 1 non-zero if the socket path fails, so both are pinned."""
    from stt_gateway.main import app

    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/health", "/v1/stt/file", "/v1/stt"} <= paths


def test_multipart_is_available() -> None:
    """Names the dependency explicitly so a failure reads as 'the dep is missing'
    rather than as an opaque FastAPI RuntimeError during startup."""
    import python_multipart  # noqa: F401


def test_rag_core_app_also_imports() -> None:
    """Same guarantee for the other service. rag_core has no upload route today,
    but the class of bug - a route decorator failing at import - is identical."""
    from rag_core.main import app

    assert app is not None
