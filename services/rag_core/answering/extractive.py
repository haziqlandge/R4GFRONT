"""Extractive answering. The fast path, defined by making no LLM call.

Phase 2 is the naive version specified in Phases.md: return the top chunk's
parent passage verbatim with its citation. Sentence-level span selection lands
in Phase 5 once the reranker exists to score against.

Why this is the correct operation for this corpus rather than a degraded
substitute for generation (Latency.md 3.1): MS MARCO passages were selected
because they contain the answer, and the dataset ships is_selected flags marking
which ones do. The answer to an MS MARCO query is, by construction, a span inside
a retrieved passage. Returning it verbatim also makes hallucination structurally
impossible, which is half of requirement 6 satisfied by construction rather than
by a check.
"""

from __future__ import annotations

import re

from ..chunking.base import ChunkRecord
from .schemas import Citation

MAX_CITATIONS = 3

# One Devanagari character is enough. The corpus is en+hi only, queries are
# short, and a code-mixed query with any Devanagari in it is a Hindi query for
# the purpose of choosing which twin to answer from.
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def detect_language(text: str) -> str:
    """"hi" if the text contains Devanagari, otherwise "en"."""
    return "hi" if _DEVANAGARI.search(text or "") else "en"


def twin_id(passage_id: str, language: str) -> str:
    """The parallel passage id in `language`.

    Passage ids are `{query_id}:{position}:{lang}` and the corpus is parallel:
    every one of the 147,945 passage groups carries BOTH languages, verified
    against passages.parquet. So this always names a real passage.
    """
    head, _, _ = passage_id.rpartition(":")
    return f"{head}:{language}"


def build_answer(
    hits: list[tuple[ChunkRecord, float]],
    passage_text: dict[str, str],
    prefer_language: str | None = None,
) -> tuple[str | None, list[Citation], int]:
    """Turn ranked chunks into an answer plus citations. Returns (answer, cites, remapped).

    `hits` is (chunk_record, score) in descending score order. The answer is the
    full parent passage of the top chunk: a chunk may be a 96-token fragment, and
    handing the user a fragment that starts mid-sentence reads as broken even when
    the retrieval was right.

    ANSWER IN THE LANGUAGE THAT WAS ASKED, WHEN THE CORPUS ALLOWS IT.
    `prefer_language` swaps each cited passage for its parallel twin. This is not
    a filter and nothing is dropped or reordered - the twin is the same passage,
    translated, and the corpus carries both for 100% of its 147,945 passage
    groups.

    Why it is worth doing, measured over the frozen 250 in both languages
    (scripts/09_relevance_floor.py, bench/results/2026-08-20-172217-...json):

      - top-1 came back in the OTHER language on 9 of 499 queries (1.8%), which
        reads as a bug to anyone who asked in Hindi and got English back.
      - those 9 scored 0.0% on Hit@1 and **66.7% once the language tag is ignored
        and the passage itself is compared**. Six of the nine had found the RIGHT
        passage and were being counted as complete misses, because gold ids are
        language-tagged (`gold_en_ids` / `gold_hi_ids`) and a cross-language hit
        cannot match one by construction.

    So this fixes a presentation bug and a measurement bug with the same lookup,
    and it costs no coverage: `prefer_language=None` restores the old behaviour
    exactly.

    It does NOT touch retrieval. The cross-lingual match still happened and is
    still what the reranker scored - README.md has called cross-lingual retrieval
    "a checkable event rather than a demo anecdote" since Phase 1, and this keeps
    it that way while showing the reader something they can read.
    """
    if not hits:
        return None, [], 0

    def resolve(pid: str) -> str:
        """The id to cite: the twin in the asked language, when one exists."""
        if prefer_language is None or pid.rsplit(":", 1)[-1] == prefer_language:
            return pid
        twin = twin_id(pid, prefer_language)
        return twin if twin in passage_text else pid

    remapped = 0
    top_pid = hits[0][0]["passage_id"]
    answer_pid = resolve(top_pid)
    if answer_pid != top_pid:
        remapped += 1
    answer = passage_text.get(answer_pid) or str(hits[0][0]["text"])

    citations: list[Citation] = []
    seen: set[str] = set()
    for chunk, score in hits:
        pid = resolve(chunk["passage_id"])
        if pid != chunk["passage_id"] and pid != answer_pid:
            remapped += 1
        # Dedup on the RESOLVED id, so a passage and its twin collapse into one
        # citation rather than appearing twice saying the same thing.
        if pid in seen:
            continue
        seen.add(pid)
        citations.append(
            Citation(
                passage_id=pid,
                score=round(score, 4),
                text=passage_text.get(pid) or str(chunk["text"]),
                language=pid.rsplit(":", 1)[-1],
            )
        )
        if len(citations) >= MAX_CITATIONS:
            break

    return answer, citations, remapped
