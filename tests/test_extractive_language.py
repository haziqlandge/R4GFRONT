"""Answering in the language that was asked. See answering/extractive.py.

Measured motivation (scripts/09_relevance_floor.py over the frozen 250): top-1
came back in the other language on 1.8% of queries, and those queries scored
0.0% on Hit@1 against 66.7% once the language tag is ignored - six of nine had
found the right passage and were counted as complete misses because gold ids
are language-tagged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.answering.extractive import (  # noqa: E402
    build_answer,
    detect_language,
    twin_id,
)


def chunk(pid: str, text: str, lang: str) -> dict:
    return {"passage_id": pid, "text": text, "language": lang, "chunk_id": pid + ":0"}


STORE = {
    "42:1:en": "The capital of Qatar is Doha.",
    "42:1:hi": "कतर की राजधानी दोहा है।",
    "99:2:en": "Something else entirely.",
    "99:2:hi": "कुछ और ही।",
}


def test_detect_language_reads_script() -> None:
    assert detect_language("who is narendra modi") == "en"
    assert detect_language("नरेंद्र मोदी कौन है") == "hi"
    # Code-mixed counts as Hindi: any Devanagari means the asker reads Devanagari.
    assert detect_language("narendra modi कौन है") == "hi"
    assert detect_language("") == "en"


def test_twin_id_swaps_only_the_language_suffix() -> None:
    assert twin_id("42:1:en", "hi") == "42:1:hi"
    assert twin_id("42:1:hi", "en") == "42:1:en"


def test_hindi_question_is_answered_from_the_hindi_twin() -> None:
    """The complaint this was built for: ask in Hindi, get English back."""
    hits = [(chunk("42:1:en", "The capital of Qatar is Doha.", "en"), 9.0)]
    answer, cites, remapped = build_answer(hits, STORE, prefer_language="hi")
    assert answer == "कतर की राजधानी दोहा है।"
    assert cites[0].passage_id == "42:1:hi"
    assert cites[0].language == "hi"
    assert remapped == 1


def test_english_question_is_answered_from_the_english_twin() -> None:
    hits = [(chunk("42:1:hi", "कतर की राजधानी दोहा है।", "hi"), 9.0)]
    answer, cites, _ = build_answer(hits, STORE, prefer_language="en")
    assert answer == "The capital of Qatar is Doha."
    assert cites[0].passage_id == "42:1:en"


def test_no_preference_leaves_everything_exactly_as_retrieved() -> None:
    """prefer_language=None must be the old behaviour, byte for byte."""
    hits = [(chunk("42:1:en", "The capital of Qatar is Doha.", "en"), 9.0)]
    answer, cites, remapped = build_answer(hits, STORE, prefer_language=None)
    assert answer == "The capital of Qatar is Doha."
    assert cites[0].passage_id == "42:1:en"
    assert remapped == 0


def test_a_passage_and_its_twin_collapse_into_one_citation() -> None:
    """Both halves of a parallel pair say the same thing. Citing both is noise."""
    hits = [
        (chunk("42:1:en", "The capital of Qatar is Doha.", "en"), 9.0),
        (chunk("42:1:hi", "कतर की राजधानी दोहा है।", "hi"), 8.5),
        (chunk("99:2:hi", "कुछ और ही।", "hi"), 4.0),
    ]
    _, cites, _ = build_answer(hits, STORE, prefer_language="hi")
    assert [c.passage_id for c in cites] == ["42:1:hi", "99:2:hi"]


def test_a_missing_twin_falls_back_to_what_was_retrieved() -> None:
    """The corpus is 100% parallel, but this must not explode if that ever changes."""
    store = {"7:0:en": "Only English exists for this one."}
    hits = [(chunk("7:0:en", "Only English exists for this one.", "en"), 5.0)]
    answer, cites, remapped = build_answer(hits, store, prefer_language="hi")
    assert answer == "Only English exists for this one."
    assert cites[0].passage_id == "7:0:en"
    assert remapped == 0


def test_ranking_is_untouched() -> None:
    """This changes which twin is shown, never the order the reranker chose."""
    hits = [
        (chunk("99:2:en", "Something else entirely.", "en"), 9.0),
        (chunk("42:1:en", "The capital of Qatar is Doha.", "en"), 1.0),
    ]
    _, cites, _ = build_answer(hits, STORE, prefer_language="hi")
    assert [c.passage_id for c in cites] == ["99:2:hi", "42:1:hi"]
    assert cites[0].score > cites[1].score
