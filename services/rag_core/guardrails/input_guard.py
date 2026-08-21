"""Layer 1: bound the input before it reaches the embedder. Phase 6.

Architecture.md 7 Layer 1. This is the first of four guard layers and the only
one that is a LATENCY mechanism as well as a safety one.

ISSUES.md I1 is why it exists in this shape. One query in the frozen benchmark
is 7,168 characters of a single Devanagari phrase repeating, and it costs 118 ms
in `embed_query` against a Hindi P99 of 5.89 ms. ISSUES.md I25 then established
that nothing downstream can save us: a stage timeout is enforced with
`asyncio.wait_for`, which only fires at an await point, and ONNX inference never
yields. A synchronous stage with a 50 ms timeout was measured running 123.7 ms
and reporting success. So the bound has to be at the input or it does not exist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Final, Pattern

from ..answering.schemas import AbstainReason
from .profanity import contains_profanity

# Prompt-injection patterns. Rules.md 2.1: compiled at import, never at request
# time.
#
# Deliberately narrow. The realistic delivery route on this product is a
# transcript - somebody says it out loud and Sarvam transcribes it faithfully -
# so these have to match spoken phrasing while leaving a normal question alone.
# Each one requires a verb AND its object, because "ignore" on its own is an
# ordinary English word and "instructions" appears in perfectly good questions
# about assembly manuals.
#
# A pattern set is a floor, not a solution. It catches the stated attack and the
# obvious rephrasings and it will not catch a determined one. The layer that
# actually protects the answer is the output guard, which checks what came back
# against the passage it was supposed to come from.
INJECTION_PATTERNS: Final[tuple[Pattern[str], ...]] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # "ignore/disregard/forget ... previous/prior/above/all instructions"
        r"\b(ignore|disregard|forget|override)\b[^.?!]{0,40}\b(previous|prior|earlier|above|all|your)\b[^.?!]{0,20}\b(instruction|instructions|prompt|prompts|rule|rules|direction|directions)\b",
        # "reveal/show/print/repeat ... your system prompt"
        r"\b(reveal|show|print|repeat|output|display|tell me)\b[^.?!]{0,30}\b(system|initial|original|hidden)\b[^.?!]{0,15}\b(prompt|instruction|instructions|message)\b",
        # role reassignment: "you are now", "act as", "pretend to be"
        r"\byou are now\b|\bact as (if you are|a|an)\b|\bpretend (to be|you are)\b|\bfrom now on,? you\b",
        # "disable/turn off your ... guardrails/filters/safety"
        r"\b(disable|turn off|bypass|remove)\b[^.?!]{0,30}\b(guardrail|guardrails|filter|filters|safety|restriction|restrictions)\b",
    )
)


# Unsafe-intent patterns.
#
# Rules.md 3.3 names "a small distilled ONNX classifier" for toxicity with
# "keyword list plus the LLM path only" as the allowed fallback. This is that
# fallback, and the deviation is deliberate: a classifier is another model to
# load, warm and budget for on a 2 vCPU box, and it would land the day before a
# code freeze without time to measure its false positive rate. A pattern set
# that is measured against a control group is more honest than a classifier that
# is not.
#
# These key on an ACT and its OBJECT, never on a topic. The corpus is general
# web search: medicine, weapons, crime and drugs are subjects it legitimately
# covers, and a list that refuses "how do vaccines work" for containing "dose"
# has damaged the product to catch nothing. tests/test_input_guard.py holds the
# control group that keeps this honest.
UNSAFE_PATTERNS: Final[tuple[Pattern[str], ...]] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # building a weapon: needs a making verb AND a device
        r"\b(make|making|build|building|construct|assemble)\b[^.?!]{0,30}\b(bomb|explosive|explosives|grenade|silencer|meth|napalm)\b",
        # harming a person: needs a harm verb AND a person object
        r"\b(kill|poison|hurt|harm|injure|murder|attack)\b[^.?!]{0,25}\b(someone|somebody|a person|my|him|her|them|people)\b",
        # self harm
        r"\b(hurt|harm|kill|cut)\b\s+(myself|yourself)\b|\bhow (to|do i) (commit suicide|end my life)\b",
        # dose framed as lethality rather than treatment
        r"\b(dose|amount|how much)\b[^.?!]{0,30}\b(would|will|to)\b[^.?!]{0,15}\b(kill|be fatal|be lethal|overdose)\b",
        # unauthorised access, keyed on the target being someone else's
        r"\b(hack|break)\s*(in)?to\b[^.?!]{0,30}\b(someone|somebody|another|else's|other people)\b",
        # circumventing a legal control
        r"\b(buy|get|obtain|purchase)\b[^.?!]{0,30}\b(gun|firearm|weapon)\b[^.?!]{0,30}\b(without|no)\b[^.?!]{0,20}\b(background check|licence|license|permit)\b",
        # theft and entry, keyed on the object not being yours
        r"\b(steal|stealing)\b[^.?!]{0,20}\b(a car|a bike|money|from)\b|\bpick a lock\b[^.?!]{0,40}\bnot mine\b",
        # threats and harassment
        r"\b(threaten|threatening|blackmail|stalk)\b[^.?!]{0,20}\b(my|someone|somebody|him|her|them)\b",
        # mixing household chemicals for a toxic result
        r"\b(mix|combine)\b[^.?!]{0,30}\b(toxic|poison|poisonous|deadly)\b[^.?!]{0,15}\bgas\b",
        # Hindi: bomb making, poisoning
        r"बम\s*(कैसे\s*)?बनाएं|बम\s*बनाने",
        r"ज़हर|जहर\s*(कैसे\s*)?(दें|देना|दे)",
    )
)


@dataclass(frozen=True)
class InputVerdict:
    """The guard's answer, with the number that caused it.

    `detail` is carried rather than discarded because the abstention panel shows
    the user why, and "we refused" without "because it was 512 tokens" is the
    kind of refusal that reads as a bug.
    """

    ok: bool
    reason: AbstainReason | None = None
    detail: str = ""
    tokens: int | None = None


class InputGuard:
    """Rejects an input before any allocation happens on its behalf.

    `count_tokens` is injected rather than imported so the guard can be handed
    the embedder's own tokenizer in production - the count that matters is the
    one the model will actually process - while a unit test can prove the bound
    is enforced without loading 90 MB of ONNX.
    """

    def __init__(
        self,
        count_tokens: Callable[[str], int],
        max_tokens: int,
        max_chars: int,
    ) -> None:
        self.count_tokens = count_tokens
        self.max_tokens = max_tokens
        self.max_chars = max_chars

    def check(self, query: str) -> InputVerdict:
        # Empty first, because it is the cheapest test of all and because the
        # request schema does not catch it: min_length=1 accepts a single space,
        # and a space costs a full embedding to retrieve nothing.
        if not query.strip():
            return InputVerdict(
                ok=False,
                reason="UNSAFE_INPUT",
                detail="the question is empty",
                tokens=0,
            )

        # Characters first, and it must stay first. ISSUES.md I1 measured the
        # character check at 0.00007 ms against 0.04228 ms to tokenize, so this
        # is a 600x cheaper early-out that rejects gross input before any
        # allocation happens on its behalf.
        #
        # It is NOT the safety bound. Characters per token are script dependent
        # (English 4.56, Hindi 3.19, and an adversarial input can push lower),
        # so a character limit bounds characters while cost is driven by tokens.
        # Both, in this order, is the design.
        if len(query) > self.max_chars:
            return InputVerdict(
                ok=False,
                reason="UNSAFE_INPUT",
                detail=f"{len(query)} characters exceeds the {self.max_chars} character limit",
            )

        for pattern in INJECTION_PATTERNS:
            if pattern.search(query):
                return InputVerdict(
                    ok=False,
                    reason="UNSAFE_INPUT",
                    detail="the question looks like a prompt injection attempt",
                )

        for pattern in UNSAFE_PATTERNS:
            if pattern.search(query):
                return InputVerdict(
                    ok=False,
                    reason="UNSAFE_INPUT",
                    detail="the question asks for help causing harm",
                )

        # Vulgarity and slurs, English and Hindi. A separate check from the one
        # above and not a widening of it: those patterns key on an act plus its
        # object because the topics are legitimate, and this one keys on the word
        # because the word is the violation. guardrails/profanity.py holds the
        # list, the normalisation that survives f*ck and f u c k, and the
        # explicit account of what is left OFF the list and why.
        #
        # The detail never names the word that matched. Echoing it back would
        # reprint the thing being refused, on screen and into the logs.
        if contains_profanity(query):
            return InputVerdict(
                ok=False,
                reason="UNSAFE_INPUT",
                detail="the question contains language this system will not answer",
            )

        n = self.count_tokens(query)
        if n > self.max_tokens:
            return InputVerdict(
                ok=False,
                reason="UNSAFE_INPUT",
                detail=f"{n} tokens exceeds the {self.max_tokens} token limit",
                tokens=n,
            )
        return InputVerdict(ok=True, tokens=n)
