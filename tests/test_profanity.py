"""Profanity detection, English and Hindi. Layer 1, Phase 6.

Two halves, and the SECOND one is the one that matters. Catching vulgarity is
easy; a word list that also refuses "what is the function of the prostate" or
"how does a nuclear power station work" has made the product worse in exchange
for nothing. `test_legitimate_questions_are_not_refused` is the control group,
and it is written to fail loudly the moment somebody widens the list to catch one
more variant.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.guardrails.input_guard import InputGuard  # noqa: E402
from rag_core.guardrails.profanity import contains_profanity, normalise  # noqa: E402


def words(text: str) -> int:
    return len(text.split())


def guard() -> InputGuard:
    return InputGuard(count_tokens=words, max_tokens=64, max_chars=512)


# ---------------------------------------------------------------- English


@pytest.mark.parametrize("q", [
    "hot sexy bitch",
    "you are a fucking idiot",
    "what the fuck is this",
    "this is complete bullshit",
    "answer me you bastard",
    "shut up asshole",
    "show me porn",
    "you dumb retard",
])
def test_english_profanity_is_refused(q: str) -> None:
    v = guard().check(q)
    assert v.ok is False, f"not caught: {q}"
    assert v.reason == "UNSAFE_INPUT"
    assert "language" in v.detail.lower()


@pytest.mark.parametrize("q", [
    "f*ck this",          # asterisk stripped
    "sh1t",               # leet digit
    "@sshole",            # leet symbol
    "fuuuuuck off",       # collapsed run
    "f u c k you",        # spaced letters rejoined
    "f.u.c.k",            # punctuation then rejoined
    "PHUCK",              # alternate spelling, uppercase
])
def test_obfuscated_spellings_are_refused(q: str) -> None:
    """A raw word list is defeated by all of these, which is why the text is
    normalised before matching rather than searched as typed."""
    assert contains_profanity(q) is True, f"not caught: {q}"


# ---------------------------------------------------------------- Hindi


@pytest.mark.parametrize("q", [
    "तुम चूतिया हो",
    "मादरचोद कहीं के",
    "भोसड़ी के",
    "तू रंडी है",
    "बहनचोद",
    "गांडू कहीं का",
])
def test_hindi_devanagari_profanity_is_refused(q: str) -> None:
    v = guard().check(q)
    assert v.ok is False, f"not caught: {q}"
    assert v.reason == "UNSAFE_INPUT"


@pytest.mark.parametrize("q", [
    "tu chutiya hai",
    "madarchod",
    "bhosdike",
    "behenchod kya kar raha hai",
    "gandu",
    "randi",
    "harami insaan",
])
def test_hindi_romanised_profanity_is_refused(q: str) -> None:
    """NOT optional. Sarvam returns Devanagari for SPOKEN Hindi, but the text
    box is typed, and Hindi typed on an English keyboard is romanised - so a
    Devanagari-only list would miss the most likely delivery route entirely."""
    v = guard().check(q)
    assert v.ok is False, f"not caught: {q}"


def test_devanagari_matches_inflected_forms() -> None:
    """Hindi inflects with attached suffixes, so the Devanagari patterns anchor
    the start of a word and not the end. चूतिया / चूतिये / चूतियों are one word
    and a trailing boundary would catch the first and miss the rest."""
    for form in ["चूतिया", "चूतिये", "चूतियों"]:
        assert contains_profanity(form) is True, form


# ---------------------------------------------------------------- control


def test_legitimate_questions_are_not_refused() -> None:
    """THE TEST THAT DECIDES WHETHER THIS CHECK IS WORTH HAVING.

    The corpus is general web search. Anatomy, crime, war, drugs and sexuality
    are subjects it legitimately covers, and every question here has real
    passages behind it. A refusal a visitor did not deserve is more damaging
    than a slur that got through to a retrieval system which would not have
    found anything for it anyway.

    This fails the moment someone adds `ass`, `cock`, `prick`, `rape` or `sex`
    to the list to catch one more variant - which is the failure mode of every
    keyword filter.
    """
    legitimate = [
        # anatomy and medicine
        "what is the function of the prostate gland",
        "what are the symptoms of breast cancer",
        "how does the human reproductive system work",
        "what is sex education",
        # words that contain a banned substring
        "how do i pass an assessment in class",
        "what is the assassination of archduke franz ferdinand",
        "who was the analyst on the panel",
        "what is a cocktail of medicines",
        "how deep is scunthorpe harbour",
        "what does the word bass mean in music",
        "what is a titmouse bird",
        # legitimate topics that a blunt list would refuse
        "what is the legal definition of rape in india",
        "what is the history of pornography law",
        "how is rapeseed oil made",
        "what is a donkey also called",
        "what does cock mean in poultry farming",
        "what is a prick test for allergies",
        # Hindi, ordinary questions
        "भारत के प्रधानमंत्री कौन हैं",
        "प्रकाश संश्लेषण क्या है",
        "कतर की राजधानी क्या है",
        "मेरे साला का जन्मदिन कब है",
    ]
    for q in legitimate:
        assert contains_profanity(q) is False, f"FALSE POSITIVE on: {q}"


def test_the_normaliser_does_not_weld_ordinary_words() -> None:
    """Rejoining spaced-out single letters is the one aggressive step in the
    normaliser, so it is bounded to runs of THREE or more isolated letters.
    Ordinary text is not that, and must come through unchanged."""
    _, despaced, _squeezed = normalise("a big cat sat on a mat")
    assert "bigcat" not in despaced
    assert "asatonamat" not in despaced


def test_empty_and_clean_input_passes() -> None:
    assert contains_profanity("") is False
    assert contains_profanity("what is the boiling point of water") is False
