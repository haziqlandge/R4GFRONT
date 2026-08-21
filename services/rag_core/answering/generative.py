"""Groq client. The FALLBACK path, and the path we publish as over budget.

Architecture.md 3.7, Latency.md 3. This exists for the middle band of confidence:
the reranker found something plausible but not decisive, so a passage returned
verbatim would answer the wrong question while an abstention would be needlessly
unhelpful. High confidence goes extractive and never touches this file; low
confidence abstains and never touches this file either.

Three facts govern everything here, all of them measured rather than assumed:

  1. A minimal Groq call - 5 max tokens, no retrieval, no real prompt - took
     352 ms end to end (Memory.md, 14 Aug). That is 1.75x the entire 200 ms budget
     before this pipeline does anything. This path cannot be made to fit, so it is
     reported separately as Band B rather than optimised at.

  2. Groq's edge returns `403, error code: 1010` - a Cloudflare fingerprint block -
     to any request carrying a default urllib/httpx User-Agent. It looks exactly
     like an auth failure and is not one (a bad key returns 401 with a JSON body).
     config.USER_AGENT exists for this and is set below. Without it this presents
     as a mystery 403 on the fallback path under deadline pressure.

  3. The free tier is 12,000 tokens per window (ISSUES.md I7). The breaker in
     harness/policies.py treats a 429 as terminal for the window rather than
     retryable, because it is.

Rules.md 3.2: `requests` is banned in a service - it blocks the event loop. This
uses httpx.AsyncClient with an explicit connect timeout.
"""

from __future__ import annotations

import os
import re
from typing import Final, Sequence

import httpx

from ..config import (
    ASIDE_MAX_TOKENS,
    ASIDE_REASONING_EFFORT,
    GROQ_CONNECT_TIMEOUT_MS,
    GROQ_CONTEXT_PASSAGES,
    GROQ_MAX_TOKENS,
    GROQ_MODEL,
    GROQ_TEMPERATURE,
    GROQ_TIMEOUT_MS,
    GROQ_URL,
    USER_AGENT,
)
from ..harness.errors import UpstreamUnavailable
from ..harness.policies import CircuitBreaker, RateLimited, call_with_policy
from .schemas import Citation

# The grounding contract. Requirement 6 is "know when NOT to answer", and the
# cheapest place to enforce it is before generation rather than after: a model
# told to answer only from the passages and to say so when it cannot will abstain
# far more often than one talked out of hallucinating afterwards. The Phase 6
# output guard still verifies groundedness - this is the first line, not the only.
SYSTEM_PROMPT: Final[str] = (
    "You answer strictly from the numbered passages provided. "
    "Do not use outside knowledge. Do not speculate. "
    "Cite the passages you used as [1], [2] and so on. "
    "Answer in the same language as the question. "
    "If the passages do not contain the answer, reply with exactly: INSUFFICIENT_CONTEXT. "
    "Be concise: two sentences at most."
)

# THE OTHER PROMPT: the model answering as itself, with no passages at all.
#
# Everything above constrains the model to the corpus, which is what makes the
# generative PATH safe. This one deliberately does the opposite, and it is not a
# contradiction because it never becomes the answer: it is shown beside our
# answer, labelled as unverified and uncited, so a reader can see where a 2017
# corpus and a current model disagree.
#
# Why that is worth showing rather than hiding: the corpus peaks in 2017
# (bench/results/2026-08-20-193717-corpus-vintage.json), so it says India's
# population is 1.21 billion and bitcoin costs $1,242. Both are faithful
# quotations and both look like defects. Putting the model's answer next to
# ours turns the most confusing thing about the demo into the most transparent.
#
# What this must NEVER do is overrule our answer. A council review rejected
# exactly that: disagreement is measured against a stale corpus, so the flag
# would fire hardest on the answers most FAITHFUL to it. Label, never adjudicate.
ASIDE_PROMPT: Final[str] = (
    "Answer the question from your own knowledge, in the same language as the "
    "question. Be direct and factual. Two sentences at most. "
    "If you are not confident, say so plainly rather than guessing."
)

# The model's own abstention token. Returned verbatim so the router can turn it
# into a typed ABSTAINED response rather than showing the user a sentinel string.
INSUFFICIENT: Final[str] = "INSUFFICIENT_CONTEXT"

# Reasoning models (qwen3.6 among the currently-available Groq set) open with a
# thinking block before answering. Two things go wrong if it is not removed: the
# block eats the token cap so the real answer is truncated away, and the model
# tends to QUOTE the abstention sentinel while reasoning about the instruction -
# which a naive substring check reads as a refusal. Measured, not hypothetical.
_THINK_BLOCK: Final[re.Pattern[str]] = re.compile(
    r"<think>.*?</think>", re.DOTALL | re.IGNORECASE
)


def _clean(text: str) -> str:
    """Strip reasoning scaffolding. Rules.md 2.1: compiled at import, not per call."""
    return _THINK_BLOCK.sub("", text).strip()


def _is_abstention(text: str) -> bool:
    """Whether the model refused.

    Deliberately NOT `INSUFFICIENT in text`. That was the original check and it
    produced false abstentions on any model that mentions the sentinel while
    reasoning, or that explains itself ("...otherwise I would return
    INSUFFICIENT_CONTEXT"). An abstention is the model returning the token as its
    answer, so the match is anchored to the start of the cleaned response.
    """
    head = text.strip().strip("`\"'*. ").upper()
    return head.startswith(INSUFFICIENT)


def build_prompt(query: str, citations: Sequence[Citation]) -> str:
    """Numbered passages then the question. Numbering is the citation contract:
    the model's [1] must index the same passage the UI's first CitationChip shows,
    so the numbering here is positional and must not be re-sorted downstream."""
    blocks = [
        f"[{i + 1}] {c.text}"
        for i, c in enumerate(citations[:GROQ_CONTEXT_PASSAGES])
    ]
    passages = "\n\n".join(blocks)
    return f"Passages:\n{passages}\n\nQuestion: {query}\n\nAnswer:"


class GroqClient:
    """One client, one breaker, created at startup and reused.

    Rules.md 2.1 bans per-request setup on the hot path; an AsyncClient built per
    call also throws away connection pooling and TLS session reuse, which on a
    path already measured at a 352 ms floor is the last thing to give away.
    """

    def __init__(self, api_key: str | None = None, model: str = GROQ_MODEL) -> None:
        self.api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        self.model = model
        self.breaker = CircuitBreaker("groq")
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        """No key is a legitimate deployment, not an error: the extractive path is
        the default and needs none. The router checks this and routes around."""
        return bool(self.api_key)

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                GROQ_TIMEOUT_MS / 1000.0, connect=GROQ_CONNECT_TIMEOUT_MS / 1000.0
            ),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                # See module docstring note 2. Do not remove.
                "User-Agent": USER_AGENT,
            },
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def generate(self, query: str, citations: Sequence[Citation]) -> str | None:
        """Grounded answer, or None when the model reports insufficient context.

        Raises UpstreamUnavailable (degradable) on any transport or quota failure,
        so the pipeline runs the stage's declared fallback and the user still gets
        the extractive answer.
        """
        if not self.configured or self._client is None:
            raise UpstreamUnavailable("groq", "no API key configured")

        payload = {
            "model": self.model,
            "temperature": GROQ_TEMPERATURE,
            "max_tokens": GROQ_MAX_TOKENS,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(query, citations)},
            ],
        }

        async def _call() -> str:
            assert self._client is not None
            resp = await self._client.post(GROQ_URL, json=payload)
            if resp.status_code == 429:
                raise RateLimited("groq", "rate limited")
            if resp.status_code >= 400:
                # 403/1010 is the Cloudflare UA block, not auth. Surfaced with the
                # body so it is diagnosable instead of a bare status code.
                raise UpstreamUnavailable(
                    "groq", f"HTTP {resp.status_code}: {resp.text[:200]}"
                )
            data = resp.json()
            content: str = data["choices"][0]["message"]["content"] or ""
            return _clean(content)

        try:
            text = await call_with_policy(
                _call, self.breaker, timeout_ms=GROQ_TIMEOUT_MS, retries=0
            )
        except httpx.HTTPError as exc:  # transport-level, never reaches _call's raise
            self.breaker.record_failure()
            raise UpstreamUnavailable("groq", str(exc)) from exc

        if not text or _is_abstention(text):
            return None
        return text

    async def aside(self, query: str) -> tuple[str | None, dict[str, float]]:
        """The model answering as itself, with no retrieved context.

        Never part of the pipeline and never inside Band A. It is requested by
        the browser AFTER our answer has already painted, on its own endpoint,
        so no measured latency figure moves and the fast path still makes zero
        network calls. Returns None on any failure - a missing aside is a panel
        that does not appear, never a broken answer.

        It shares the circuit breaker with the generative path on purpose: the
        two draw on one rate limit (ISSUES.md I7, 12,000 tokens per window), and
        an aside must never be the reason the real fallback is unavailable.

        RETURNS THE PROVIDER'S OWN TIMING ALONGSIDE THE TEXT. Groq reports
        `queue_time`, `prompt_time` and `completion_time` in its usage block, and
        those three plus the wall clock are what let the external timing panel say
        WHERE the time went instead of printing one number for the whole trip.
        Measured on one call: 745.6 ms wall, of which 312.4 was queue, 4.5 was
        reading the prompt, 75.4 was writing the answer and the remaining 353 was
        the wire. Generation is the smallest part, which is not what a reader
        would guess, and is exactly the sort of thing this page exists to show.

        The dict is empty when there is nothing to report - no call made, or a
        provider that did not send usage - and the panel draws no rows for it
        rather than drawing zeros.
        """
        if not self.configured or self._client is None or not self.breaker.allows():
            return None, {}

        payload = {
            "model": self.model,
            "temperature": GROQ_TEMPERATURE,
            "max_tokens": ASIDE_MAX_TOKENS,
            "reasoning_effort": ASIDE_REASONING_EFFORT,
            "messages": [
                {"role": "system", "content": ASIDE_PROMPT},
                {"role": "user", "content": query},
            ],
        }

        usage: dict[str, float] = {}

        async def _call() -> str:
            assert self._client is not None
            resp = await self._client.post(GROQ_URL, json=payload)
            if resp.status_code == 429:
                raise RateLimited("groq", "rate limited")
            if resp.status_code >= 400:
                raise UpstreamUnavailable("groq", f"HTTP {resp.status_code}")
            data = resp.json()
            u = data.get("usage") or {}
            # Seconds on the wire, milliseconds everywhere in this project.
            for key in ("queue_time", "prompt_time", "completion_time", "total_time"):
                if isinstance(u.get(key), (int, float)):
                    usage[key] = float(u[key]) * 1000.0
            for key in ("prompt_tokens", "completion_tokens"):
                if isinstance(u.get(key), (int, float)):
                    usage[key] = float(u[key])
            return _clean(data["choices"][0]["message"]["content"] or "")

        try:
            text = await call_with_policy(
                _call, self.breaker, timeout_ms=GROQ_TIMEOUT_MS, retries=0
            )
        except (UpstreamUnavailable, RateLimited, httpx.HTTPError):
            return None, {}
        return (text or None), usage
