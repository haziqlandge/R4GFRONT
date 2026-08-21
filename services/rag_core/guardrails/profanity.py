"""Profanity and slur detection, English and Hindi. Layer 1, Phase 6.

SEPARATE FROM `UNSAFE_PATTERNS` IN input_guard.py, AND THE DISTINCTION IS THE
WHOLE DESIGN. Those patterns key on an ACT plus its OBJECT - "make a bomb",
"poison someone" - because the TOPICS are ordinary things a web corpus covers
and a list that refuses "how do vaccines work" for containing "dose" has damaged
the product to catch nothing.

This is the opposite case. Here the word IS the violation: there is no
"make"/"someone" pairing to look for, because "bitch" is not a subject anyone
needs answered. So this is a word list, and being a word list it needs the
protections a word list needs.

WHAT IS DELIBERATELY NOT ON THE LIST, and why. The corpus is general web search
and these all have real passages behind them:

  - Clinical anatomy: penis, vagina, breast, anus. "what is the function of the
    prostate" is a biology question.
  - Legal and social topics: rape, incest, abortion, pornography as a subject.
    "what is statutory rape" is a legal question with a real answer.
  - Words that are only rude in one sense: ass (donkey), cock (rooster), prick
    (a pin prick, an allergy prick test), fag (a cigarette in British English),
    screw, balls, crap.
  - `साला`, which is an insult and also simply means brother-in-law.

Every one of those is a false positive waiting to happen, and a refusal a
visitor did not deserve is more damaging here than a slur that got through to a
retrieval system which will not find anything for it anyway.

THE OBFUSCATION PROBLEM. A raw word list is defeated by `f*ck`, `f u c k`,
`fuuuuck` and `phuck`, so the text is normalised first - leet characters folded,
punctuation dropped, long runs collapsed, and single letters spaced out by
anything re-joined. That last one is the only aggressive step, and it is bounded
to runs of single letters so it cannot touch ordinary words.

This is a floor, not a solution. Somebody determined will get a variant through.
What it does is make the common cases - typed vulgarity, a transcribed insult -
refuse cleanly rather than being answered or, worse, quietly abstaining on a low
retrieval score and looking like the system simply did not know.
"""

from __future__ import annotations

import re
from typing import Final, Pattern

# Leet and lookalike folding. Applied before matching so `sh1t` and `@ss` reduce
# to the words the list holds.
_LEET: Final[dict[int, str]] = str.maketrans({
    "@": "a", "4": "a", "3": "e", "1": "i", "!": "i", "|": "i",
    "0": "o", "$": "s", "5": "s", "7": "t", "+": "t", "*": "",
})

# Anything that is not a letter, digit, whitespace or Devanagari becomes a
# space. This is what turns `f.u.c.k` into `f u c k` for the next step.
_PUNCT: Final[Pattern[str]] = re.compile(r"[^\w\sऀ-ॿ]+")

# Three or more of the same character collapse to two: `fuuuuuck` -> `fuuck`,
# and then a second pass to one where the doubled form is not a real word.
_RUNS: Final[Pattern[str]] = re.compile(r"(.)\1{2,}")

# Single letters separated by anything, three or more in a row. `f u c k` and
# `f-u-c-k` rejoin; ordinary text does not, because ordinary words are not runs
# of isolated single letters. Bounded deliberately - this is the one step that
# could damage real input if it were any looser.
_SPACED: Final[Pattern[str]] = re.compile(r"\b(?:[a-z]\s+){2,}[a-z]\b")

# Every repeat down to one. Coarser than _RUNS and kept as a SEPARATE form
# rather than replacing it, so `fuuuuuck` is caught without the folded form
# losing the doubled letters ordinary words legitimately have.
_ANY_RUN: Final[Pattern[str]] = re.compile(r"(.)\1+")


def normalise(text: str) -> tuple[str, str, str]:
    """Return (folded, despaced, squeezed) forms of `text` for matching.

    THREE forms rather than one, because each transformation is lossy in a way
    the others are not. All three are searched, so none has to be safe alone:

      folded    lowercased, leet folded, punctuation dropped, runs of 3+ cut to
                two. `f*ck` and `sh1t` land here.
      despaced  runs of isolated single letters rejoined, so `f u c k` and
                `f.u.c.k` become one word. Bounded to three-plus single letters
                so it cannot weld ordinary words together.
      squeezed  every repeat collapsed to one. This is what catches `fuuuuuck`,
                which `folded` alone reduces only as far as `fuuck` - a real
                miss, found by the test rather than by reading the code.
    """
    folded = text.lower().translate(_LEET)
    folded = _PUNCT.sub(" ", folded)
    folded = _RUNS.sub(r"\1\1", folded)
    despaced = _SPACED.sub(lambda m: m.group(0).replace(" ", ""), folded)
    # The third form. `folded` only reduces `fuuuuuck` as far as `fuuck`,
    # which matched nothing - a real miss, found by the test rather than by
    # reading the code. Collapsing every repeat to one also rewrites innocent
    # words (`assessment` -> `asesment`), which is harmless precisely because
    # the list holds no word an ordinary English word collapses INTO. The
    # control group in tests/test_profanity.py is what keeps that true.
    squeezed = _ANY_RUN.sub(r"\1", despaced)
    return folded, despaced, squeezed


# --------------------------------------------------------------------------
# English. Vulgarity and slurs only - see the module docstring for the line.
# --------------------------------------------------------------------------
_EN: Final[tuple[str, ...]] = (
    # f-word family, including the spellings that survive a spoken transcript
    r"fuc?k(?:ing|ed|er|ers|s|off|wit|tard)?", r"fuk+", r"fck", r"phuck", r"fuq",
    r"motherfuc?k(?:er|ers|ing)?", r"mofo",
    # s-word family
    r"shit(?:ty|ting|head|s|bag)?", r"bullshit", r"shite",
    # insults aimed at a person
    r"bitch(?:es|ing|y)?", r"bastard(?:s)?", r"asshole(?:s)?", r"arsehole(?:s)?",
    r"dumbass", r"jackass", r"dickhead(?:s)?", r"douchebag", r"twat(?:s)?",
    r"wanker(?:s)?", r"cocksucker(?:s)?", r"scumbag",
    # sexual vulgarity, not clinical anatomy
    r"cunt(?:s)?", r"whore(?:s)?", r"slut(?:s|ty)?", r"horny", r"boobs?",
    r"titties", r"tits", r"blowjob", r"handjob", r"jerk\s?off", r"porn(?:o|hub)?",
    r"nudes?", r"sexy", r"milf", r"bdsm",
    # slurs
    r"nigg(?:er|ers|a|as)", r"faggot(?:s)?", r"tranny", r"retard(?:ed|s)?",
    r"chink", r"paki", r"spic", r"wetback", r"raghead", r"kike", r"gook",
)

# --------------------------------------------------------------------------
# Hindi, Devanagari.
#
# Leading boundary only, no trailing one. Hindi inflects with attached suffixes
# and matras - चूतिया, चूतिये, चूतियों are the same word - so anchoring the end
# would catch the base form and miss every inflection of it.
# --------------------------------------------------------------------------
_HI_DEVA: Final[tuple[str, ...]] = (
    r"चूतिय", r"चुतिय", r"चूत", r"भोसड़", r"भोसड", r"भोंसड",
    r"मादरचोद", r"मादरचूद", r"बहनचोद", r"बहनचूद", r"भेनचोद", r"भैनचोद",
    r"गांड", r"गाँड", r"लंड", r"लौड", r"लौड़", r"रंडी", r"रांड",
    r"हरामी", r"हरामज़ाद", r"हरामजाद", r"कुतिय", r"कुत्तिय",
    r"भड़वा", r"भडवा", r"चुदाई", r"चुदा", r"चोदू", r"गांडू", r"गाण्डू",
    r"झांट", r"झाँट", r"टट्टी", r"मादरजात",
)

# --------------------------------------------------------------------------
# Hindi, romanised.
#
# NOT optional. Sarvam returns Devanagari for spoken Hindi, but the text box on
# this site is typed, and Hindi typed on an English keyboard is romanised - so a
# Devanagari-only list would miss the most likely delivery route entirely.
# Spellings vary wildly with no standard, hence the alternates.
# --------------------------------------------------------------------------
_HI_ROMAN: Final[tuple[str, ...]] = (
    r"chut(?:iya|iye|iyapa|ia)", r"chutad",
    r"bhosdi(?:ke|wala)?", r"bhosad(?:i|ike)?", r"bhosri",
    r"madar\s?ch(?:o|u)d(?:a|s)?", r"mader\s?chod",
    r"beh?en\s?ch(?:o|u)d(?:a)?", r"bhen\s?chod", r"bahan\s?chod",
    r"gaand(?:u|oo)?", r"gandu", r"gandoo",
    r"l(?:a|au|ow)nd", r"lauda", r"lawda", r"lund",
    r"randi", r"raand", r"harami", r"haramzad(?:a|e|i)",
    r"kutiya", r"kutti", r"bhadwa", r"bhadve",
    r"chud(?:ai|wa|na)", r"jhant", r"jhaat", r"tatti", r"lodu", r"loda",
)

# Latin-script terms need BOTH boundaries: without a trailing one, `chut` would
# fire on `chute` and `randi` on nothing useful but plenty of names. Devanagari
# needs a leading one only, for the inflection reason above.
PROFANITY_PATTERNS: Final[tuple[Pattern[str], ...]] = tuple(
    [re.compile(rf"\b(?:{w})\b", re.IGNORECASE) for w in _EN + _HI_ROMAN]
    + [re.compile(rf"\b(?:{w})", re.IGNORECASE) for w in _HI_DEVA]
)


def contains_profanity(text: str) -> bool:
    """Whether `text` carries vulgar or hateful language in English or Hindi.

    Checked against both normalised forms, so `f*ck`, `f u c k` and `fuuuck` all
    reduce to the same hit. Returns a plain bool: nothing downstream needs to
    know WHICH word matched, and not returning it means the matched term can
    never be echoed back to the visitor or into a log.
    """
    if not text:
        return False
    forms = normalise(text)
    for pattern in PROFANITY_PATTERNS:
        if any(pattern.search(f) for f in forms):
            return True
    return False
