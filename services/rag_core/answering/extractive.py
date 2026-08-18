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

from ..chunking.base import ChunkRecord
from .schemas import Citation

MAX_CITATIONS = 3


def build_answer(
    hits: list[tuple[ChunkRecord, float]], passage_text: dict[str, str]
) -> tuple[str | None, list[Citation]]:
    """Turn ranked chunks into an answer plus citations.

    `hits` is (chunk_record, score) in descending score order. The answer is the
    full parent passage of the top chunk: a chunk may be a 96-token fragment, and
    handing the user a fragment that starts mid-sentence reads as broken even when
    the retrieval was right.
    """
    if not hits:
        return None, []

    top_chunk, _ = hits[0]
    answer = passage_text.get(top_chunk["passage_id"]) or str(top_chunk["text"])

    citations: list[Citation] = []
    seen: set[str] = set()
    for chunk, score in hits:
        pid = chunk["passage_id"]
        if pid in seen:
            continue  # two chunks of one passage are one citation
        seen.add(pid)
        citations.append(
            Citation(
                passage_id=pid,
                score=round(score, 4),
                text=passage_text.get(pid) or str(chunk["text"]),
                language=str(chunk["language"]),
            )
        )
        if len(citations) >= MAX_CITATIONS:
            break

    return answer, citations
