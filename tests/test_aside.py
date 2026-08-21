"""The unverified aside endpoint: rate limiting and client identity. I34, I35.

`tests/test_ratelimit.py` pins the limiter itself. This pins the two things the
ENDPOINT has to get right, both of which would fail silently in production:

  1. A client who has spent their window is refused BEFORE the network, and gets
     the same "no panel" a dead upstream produces rather than an error.
  2. "Per user" resolves to an identity a visitor cannot forge. Getting the
     X-Forwarded-For hop backwards defeats the whole limiter with one header.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.config import (  # noqa: E402
    ASIDE_MAX_TOKENS,
    ASIDE_RATE_LIMIT,
    GROQ_MAX_TOKENS,
    GROQ_MODEL,
)


def test_aside_cap_is_the_measured_200() -> None:
    """ISSUES.md I34: 160 truncated gpt-oss-20b mid-sentence ("Eric Adams is
    the") because it is a reasoning model that spends the cap thinking.

    200 was re-measured on that same query plus seven others - the longest
    answers observed, in both scripts - and none truncated. Pinned because the
    known failure is only 40 tokens below it: this is the number to check first
    if an external answer starts arriving cut off, and it must not be lowered
    without repeating that measurement."""
    assert ASIDE_MAX_TOKENS == 200


def test_the_aside_gets_more_room_than_the_grounded_path() -> None:
    """160 is right where the model paraphrases passages it was handed, and
    wrong where it answers from its own knowledge and has to think first."""
    assert ASIDE_MAX_TOKENS > GROQ_MAX_TOKENS


class _Stub:
    """Minimal stand-in for GroqClient's aside contract."""

    def __init__(self, answer: str | None, configured: bool = True) -> None:
        self.answer = answer
        self.configured = configured
        self.calls = 0

    async def aside(self, query: str) -> str | None:
        self.calls += 1
        return self.answer


@pytest.fixture()
def endpoint(monkeypatch):
    """The real /v1/aside body, with the runtime and limiter swapped out.

    Imported inside the fixture because rag_core.main pulls in the ONNX and index
    stack at module scope, and the rest of this file must stay importable on a
    machine with no artifacts.
    """
    from rag_core import main as m
    from rag_core.harness.ratelimit import RateLimiter

    class _Req:
        headers: dict[str, str] = {}
        client = type("C", (), {"host": "10.0.0.7"})()

    def build(groq, limit: int = ASIDE_RATE_LIMIT):
        monkeypatch.setitem(m.STATE, "ready", True)
        monkeypatch.setitem(m.STATE, "runtime", type("RT", (), {"groq": groq})())
        monkeypatch.setattr(m, "ASIDE_LIMIT", RateLimiter(limit, 60.0, "test"))
        return m

    return build, _Req


async def test_answers_and_names_the_model(endpoint) -> None:
    from rag_core.answering.schemas import AnswerRequest

    m = endpoint[0](_Stub("Eric Adams is the mayor."))
    out = await m.aside(AnswerRequest(query="who is the mayor of nyc"), endpoint[1]())
    assert out == {"text": "Eric Adams is the mayor.", "model": GROQ_MODEL}


async def test_a_failed_call_names_no_model(endpoint) -> None:
    """`text: null` with a model name attached would claim an answer that does
    not exist. Every failure mode reduces to `aside()` returning None."""
    from rag_core.answering.schemas import AnswerRequest

    m = endpoint[0](_Stub(None))
    out = await m.aside(AnswerRequest(query="q"), endpoint[1]())
    assert out == {"text": None, "model": None}


async def test_client_is_cut_off_after_the_limit(endpoint) -> None:
    """The headline requirement: five queries a minute, per user."""
    from rag_core.answering.schemas import AnswerRequest

    groq = _Stub("answer")
    m = endpoint[0](groq, limit=5)
    req, http = AnswerRequest(query="q"), endpoint[1]()

    for _ in range(5):
        assert (await m.aside(req, http))["model"] == GROQ_MODEL
    assert groq.calls == 5

    assert await m.aside(req, http) == {"text": None, "model": None}
    assert groq.calls == 5, "a rate-limited client still reached the network"


async def test_refusal_costs_no_network_call(endpoint) -> None:
    """The limiter runs in FRONT of the breaker. A capped client must not be able
    to record failures against a breaker that is protecting other visitors."""
    from rag_core.answering.schemas import AnswerRequest

    groq = _Stub("answer")
    m = endpoint[0](groq, limit=1)
    req, http = AnswerRequest(query="q"), endpoint[1]()

    await m.aside(req, http)
    for _ in range(10):
        await m.aside(req, http)
    assert groq.calls == 1


async def test_unkeyed_upstream_does_not_spend_the_window(endpoint) -> None:
    """`configured` is checked before the limiter, so a deployment with no Groq
    key does not burn a budget on calls it can never make."""
    from rag_core.answering.schemas import AnswerRequest

    groq = _Stub(None, configured=False)
    m = endpoint[0](groq, limit=2)
    http = endpoint[1]()

    for _ in range(4):
        assert await m.aside(AnswerRequest(query="q"), http) == {"text": None, "model": None}
    assert groq.calls == 0
    assert m.ASIDE_LIMIT.remaining("10.0.0.7") == 2


def test_client_key_prefers_the_rightmost_forwarded_hop() -> None:
    """Caddy APPENDS the real peer, so the rightmost entry is the one it wrote.
    Trusting the leftmost - which is the usual advice - would let any client mint
    a fresh identity per request by varying one header."""
    from rag_core.main import client_key

    req = type(
        "R", (), {"headers": {"x-forwarded-for": "1.2.3.4, 203.0.113.9"},
                  "client": type("C", (), {"host": "127.0.0.1"})()}
    )()
    assert client_key(req) == "203.0.113.9"


def test_client_key_falls_back_to_the_socket_peer() -> None:
    """Local development: no proxy, no header, the peer IS the client."""
    from rag_core.main import client_key

    req = type("R", (), {"headers": {}, "client": type("C", (), {"host": "127.0.0.1"})()})()
    assert client_key(req) == "127.0.0.1"


def test_client_key_degrades_to_one_shared_bucket() -> None:
    """A missing peer must mean "one bucket", never "no limit"."""
    from rag_core.main import client_key

    req = type("R", (), {"headers": {}, "client": None})()
    assert client_key(req) == "unknown"
