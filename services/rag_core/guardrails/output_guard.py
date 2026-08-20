"""Layer 4: check the answer against the passage it claims to come from. Phase 6.

Architecture.md 7 Layer 4, and the layer ISSUES.md I26 promoted from a nicety to
the only thing that addresses the project's largest measured weakness.

Phase 5 calibrated an abstention floor on the cross-encoder score. That floor
catches 100% of off-topic and gibberish input and lets 92.5% of wrong top-1
answers through, because the two populations it has to separate are not
separable on that axis: a passage about the right subject that does not answer
the question scores +5.89 against a correct answer's +8.30. Retrieval confidence
answers "is this question in the corpus", and no threshold on it answers "is
this answer supported".

Checking the answer text against the cited passages does answer that, and it is
the only measurement here that can.
"""

from __future__ import annotations

import re
from typing import Final, Sequence

# Word characters including Devanagari, so Hindi tokenizes rather than
# collapsing to one blob. Rules.md 2.1: compiled at import.
_WORD: Final[re.Pattern[str]] = re.compile(r"[0-9A-Za-zऀ-ॿ]+")

# Function words carry no evidence and would inflate every score toward the
# mean. English only by design: Hindi's postpositions are separate tokens but a
# hand-written Hindi stopword list is a guess, and leaving them in costs less
# than getting them wrong. The bigram half of the score is what carries Hindi.
_STOP: Final[frozenset[str]] = frozenset(
    """a an the of in on at to for from by with and or but is are was were be been
    being it its this that these those as if then than so such no not have has had
    do does did will would can could should may might must i you he she they we
    what which who whom whose when where why how""".split()
)


def _content_tokens(text: str) -> list[str]:
    return [t for t in (m.group().lower() for m in _WORD.finditer(text)) if t not in _STOP]


# The citation markers the generation prompt asks for: [1], [2] and so on.
_CITE: Final[re.Pattern[str]] = re.compile(r"\[(\d{1,2})\]")


def invalid_citations(answer: str, n: int) -> list[int]:
    """Cited indices that do not exist in the retrieved set.

    Architecture.md 7 Layer 4. A model that cites [4] against three passages has
    named a source nobody can open, and the interface renders citations as
    clickable chips, so an invalid index is a dead control as well as an
    unverifiable claim. Returned in the order they appear, deduplicated.
    """
    seen: list[int] = []
    for m in _CITE.finditer(answer):
        i = int(m.group(1))
        if (i < 1 or i > n) and i not in seen:
            seen.append(i)
    return seen


def _bigrams(tokens: Sequence[str]) -> list[tuple[str, str]]:
    return [(tokens[i], tokens[i + 1]) for i in range(len(tokens) - 1)]


def groundedness(answer: str, passages: Sequence[str]) -> float:
    """How much of the answer is supported by the cited passages, 0 to 1.

    Deliberately a RECALL of the answer's own content, not a similarity: the
    question is "is every claim here traceable to a source", so the denominator
    is the answer and a long passage cannot inflate it.

    Two halves, and the second is the one that does the work. Counting single
    words alone scores a perfect 1.0 on an answer assembled entirely out of the
    passage's own vocabulary arranged into claims the passage never made, which
    is the exact shape of a plausible hallucination. Adjacent pairs are cheap
    evidence that phrases survived intact rather than being redealt, so a
    verbatim span scores strictly above a reassembly.

    It is not entailment. A determined reassembly that preserves word order will
    still score well, and the honest description of this measure is that it
    catches drift and invention rather than proving truth.
    """
    tokens = _content_tokens(answer)
    if not tokens:
        return 0.0

    source_tokens: set[str] = set()
    source_bigrams: set[tuple[str, str]] = set()
    for p in passages:
        p_tokens = _content_tokens(p)
        source_tokens.update(p_tokens)
        source_bigrams.update(_bigrams(p_tokens))

    unigram = sum(1 for t in tokens if t in source_tokens) / len(tokens)

    pairs = _bigrams(tokens)
    if not pairs:
        # A one-word answer has no adjacency to check, so the word is all the
        # evidence there is. Averaging against an absent half would halve a
        # correct one-word answer for having only one word.
        return unigram

    bigram = sum(1 for b in pairs if b in source_bigrams) / len(pairs)
    return (unigram + bigram) / 2.0
