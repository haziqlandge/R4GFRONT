"""Layer 4, the output guard. Phase 6.

This is the layer ISSUES.md I26 made load-bearing rather than decorative.

The abstention floor from Phase 5 is an excellent out-of-domain detector and a
poor grounding detector: it catches 100% of off-topic and gibberish input, and
lets 92.5% of wrong top-1 answers through, because a passage about the right
subject that simply does not answer the question scores +5.89 against a correct
answer's +8.30. No threshold on the retrieval score separates those two.

The only thing that can is checking the answer against the passage it claims to
have come from. That is what this layer does.

One consequence worth stating, because it is the reason the extractive path was
chosen at all: an extractive answer is a verbatim span of its cited passage, so
it is grounded by construction and scores 1.0 here. This guard is really about
the generative path, where a model composes and can drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.guardrails.output_guard import groundedness  # noqa: E402

PASSAGE = (
    "Mount Everest is Earth's highest mountain above sea level. "
    "Its peak rises 8,849 metres above sea level and it lies in the Himalayas "
    "on the border between Nepal and China."
)


def test_a_verbatim_span_is_fully_grounded() -> None:
    """The extractive path returns exactly this shape, so it must score at the
    top. If it did not, the guard would be refusing the one path that cannot
    hallucinate."""
    answer = "Its peak rises 8,849 metres above sea level"

    assert groundedness(answer, [PASSAGE]) == 1.0


def test_an_answer_the_passage_does_not_support_scores_low() -> None:
    answer = "The Burj Khalifa in Dubai is 828 metres tall and was finished in 2010."

    assert groundedness(answer, [PASSAGE]) < 0.25


def test_words_borrowed_from_the_passage_and_rearranged_do_not_score_full() -> None:
    """The failure mode of counting single words, and the reason this test exists.

    Every content word below is genuinely in the passage, so word-level overlap
    calls this perfectly grounded. It says Nepal is the highest mountain and that
    China is 8,849 metres tall, which the passage does not say and which is the
    exact shape of a plausible hallucination: right vocabulary, wrong claims.

    A verbatim span must score strictly higher than this, or the measure cannot
    tell quoting from reassembly and is not worth computing.
    """
    reassembled = "Nepal is Earth's highest mountain and China rises 8,849 metres"
    verbatim = "Its peak rises 8,849 metres above sea level"

    assert groundedness(reassembled, [PASSAGE]) < groundedness(verbatim, [PASSAGE])


def test_this_measure_does_not_detect_falsehood_and_we_pin_that() -> None:
    """What this guard is not, asserted so nobody can quietly claim otherwise.

    Measured on the passage above: a TRUE paraphrase scores about 0.64 while a
    FALSE sentence reassembled out of the passage's own words scores about 0.83.
    The false one scores higher. That is not a bug to be tuned away, it is what
    lexical overlap measures: how much of the answer's wording is traceable to
    the source, which is a different question from whether the answer is right.

    So the floor is set low, to catch content that is not in the passages at all
    rather than to adjudicate claims, and the documentation says the same. If
    this assertion ever starts failing it means the measure changed character
    and every sentence written about it needs rechecking.
    """
    true_paraphrase = "Everest reaches 8,849 metres and sits between Nepal and China"
    false_reassembly = "Nepal is Earth's highest mountain and China rises 8,849 metres"
    unsupported = "The Burj Khalifa in Dubai is 828 metres tall and was finished in 2010."

    assert groundedness(false_reassembly, [PASSAGE]) > groundedness(true_paraphrase, [PASSAGE])
    # What it IS reliable at: noticing that the answer is about something else.
    assert groundedness(unsupported, [PASSAGE]) < 0.25 < groundedness(true_paraphrase, [PASSAGE])


def test_it_works_on_devanagari() -> None:
    """Half the corpus is Hindi, and a guard that only reads Latin script would
    pass every Hindi answer unchecked."""
    passage = "माउंट एवरेस्ट पृथ्वी का सबसे ऊंचा पर्वत है और इसकी चोटी समुद्र तल से 8849 मीटर ऊपर है।"
    span = "इसकी चोटी समुद्र तल से 8849 मीटर ऊपर है"
    unrelated = "दिल्ली भारत की राजधानी है"

    assert groundedness(span, [passage]) > 0.9
    assert groundedness(unrelated, [passage]) < 0.25


def test_a_citation_index_that_was_never_retrieved_is_invalid() -> None:
    """The generative path is told to cite as [1], [2]. A model that invents a
    [4] against three passages has cited a source that does not exist, and the
    citation chip in the interface would have nothing to open."""
    from rag_core.guardrails.output_guard import invalid_citations

    assert invalid_citations("Everest is tallest [1] and lies in Nepal [4].", n=3) == [4]
    assert invalid_citations("Everest is tallest [1][2].", n=3) == []
    assert invalid_citations("No citations here at all.", n=3) == []
