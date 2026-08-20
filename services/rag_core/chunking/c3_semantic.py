"""C3: semantic breakpoint. Job J3, box EMBED.

Split where the cosine distance between consecutive sentences exceeds the 92nd
percentile. Chunks follow meaning rather than character count.

Built 20 August 2026, after being time-boxed out of Phase 3. `DONT-FORGET.md` 2
recorded it as "never built" and that entry is now wrong in the good direction;
C4 remains unbuilt and killed on its own cost model (`ISSUES.md` I22).

TRAP, from the original spec and obeyed here: compute the 92nd percentile ONCE
over the whole corpus and record it in `params()`, not per passage. Passages
here are p50 48 words and 3.14 sentences, so a per-passage percentile over two
or three sentence gaps is noise, not a threshold. That is why `chunk()` is a
two-pass method rather than a per-passage loop: pass one measures every gap in
the corpus, pass two cuts against the single threshold that falls out of it.

WHAT THIS DOES NOT REUSE, AND WHY
The spec said to reuse J2's sentence embeddings from `artifacts/sentences.parquet`.
That artifact was never written - C2 shipped without persisting it - so the
sentences are embedded here. It is the dominant cost of the strategy and it is
paid once at build time.

SENTENCE SPANS, NOT SENTENCE STRINGS
`c2_sentence_window.split_sentences` returns stripped strings and throws the
offsets away. This file needs character offsets, because chunk text must be
sliced from the source passage verbatim - the extractive path hands that text
straight to the user. So the segmentation rules are mirrored here in a
span-returning form rather than imported. C2 is built, measured and published;
changing it to return spans would invalidate its numbers for a refactor.
"""

from __future__ import annotations

import re
import time
from typing import Callable, Final, Iterable, Sequence

import numpy as np

from ..retrieval.embedder import Embedder
from .base import Chunk, PassageRecord

# The percentile of the corpus-wide gap distribution above which a gap becomes a
# cut. 92 comes from the strategy's specification, not from tuning against the
# benchmark - Rules.md 5 forbids fitting a published number to the test set.
BREAKPOINT_PERCENTILE: Final[float] = 92.0

# Cap before chunking, identical to every other strategy. One Hindi passage runs
# to 4,093 words against a 205-word English source (a translation repetition
# loop) and uncapped it would dominate the build.
MAX_PASSAGE_TOKENS: Final[int] = 384

# Sentences are embedded in LENGTH-SORTED batches, and on this workload that is
# not a tuning detail - it is the difference between a 35 minute build and a two
# hour one. The tokenizer pads each batch to its longest member, and sentence
# length here is p50 22 tokens with a max of 512, so an unsorted batch is mostly
# padding.
#
# Measured on 4,000 real sentences from the slice (scratch benchmark, 20 Aug):
#
#     batch   padding efficiency        throughput, 8 threads
#             unsorted  sorted          unsorted        sorted
#        32     32.2%    88.2%            133/s          446/s
#        64     27.2%    87.2%            103/s          429/s
#       128     23.4%    84.9%          CRASHED             -
#
# Phase 2 measured length sorting at 1.46x on chunk texts. On sentences it is
# 2.5x to 3.3x, because sentences vary in length far more than 96-token windows
# do.
#
# The crash is the reason 32 is the shipped value rather than 128. An unsorted
# batch of 128 containing one 512-token "sentence" - the machine-translation
# repetition loops in this corpus produce them - pads all 128 to 512 and asks
# onnxruntime for a 1.6 GB attention buffer, which fails. Sorting makes that
# batch cheap by grouping the monsters together, and a small batch bounds the
# damage even so.
EMBED_BATCH: Final[int] = 32

# Measured cost of the whole strategy on the frozen slice, so the next person
# does not have to rediscover it: ~922,000 sentences at 446/s is ~35 min, then
# ~346,000 chunks (1.169 per passage, against C1's 1.282) at C1's measured
# 203/s is ~28 min, plus ~2 min of HNSW. About 65 minutes end to end.
#
# DETERMINISM. Same corpus in, same index out. Sentence segmentation is a fixed
# regex, `np.argsort(kind="stable")` fixes the batch composition, and the
# threshold is a percentile over every gap in the corpus rather than anything
# sampled. Note that int8 embeddings do shift slightly with batch composition
# (the same effect ISSUES.md I24 measured on the cross-encoder), which is
# exactly why the batching has to be deterministic - and why the comparison
# against C1 is fair: C1's index was built with length-sorted batches too.

# Devanagari danda and double danda, mirroring c2_sentence_window. A
# period-based splitter leaves an entire Hindi passage as one "sentence", which
# is the silent-quality failure Architecture.md 3.4 warns about.
_DANDA: Final[str] = "।॥"
_INDIC_SPLIT: Final[re.Pattern[str]] = re.compile(f"[{_DANDA}]+")
_LATIN_SPLIT: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?])\s+")


def sentence_spans(text: str, language: str) -> list[tuple[int, int]]:
    """Sentence boundaries as (start, end) character offsets into `text`.

    Same segmentation rules as `c2_sentence_window.split_sentences`, but keeping
    the offsets so the caller can slice the source verbatim. Spans exclude
    surrounding whitespace and skip anything that is whitespace-only, so joining
    consecutive spans reproduces the passage minus the separators.
    """
    pattern = _INDIC_SPLIT if language == "hi" else _LATIN_SPLIT
    spans: list[tuple[int, int]] = []
    cursor = 0
    for m in pattern.finditer(text):
        # Indic: the danda ends the sentence and belongs to it. Latin: the
        # terminal punctuation is already behind the lookbehind, and the match
        # is the whitespace after it.
        end = m.end() if language == "hi" else m.start()
        spans.append((cursor, end))
        cursor = m.end()
    spans.append((cursor, len(text)))

    out: list[tuple[int, int]] = []
    for start, end in spans:
        piece = text[start:end]
        stripped = piece.strip()
        if not stripped:
            continue
        lead = len(piece) - len(piece.lstrip())
        out.append((start + lead, start + lead + len(stripped)))
    return out


class SemanticChunker:
    """Sentences grouped between corpus-calibrated semantic breakpoints."""

    name = "c3"

    def __init__(
        self,
        embedder: Embedder,
        percentile: float = BREAKPOINT_PERCENTILE,
        max_tokens: int = MAX_PASSAGE_TOKENS,
        batch: int = EMBED_BATCH,
        **kwargs: object,
    ) -> None:
        self.embedder = embedder
        self.percentile = percentile
        self.max_tokens = max_tokens
        self.batch = batch
        self.truncated_count = 0
        # Filled by chunk(). Recorded in params() so meta.json carries the actual
        # cut value and not merely the percentile that produced it - the
        # percentile is reproducible, the threshold is the reproduction.
        self.threshold: float | None = None
        self.gap_count = 0
        # Opt-in progress hook, called as (done, total, elapsed_seconds).
        #
        # C3 is the first strategy whose CHUNKING step is long - the sentence
        # pass is ~35 minutes on the full slice, and every other strategy chunks
        # in under a minute. Without this the build prints nothing between
        # "passages 295,890" and the chunk count, which for half an hour is
        # indistinguishable from a hang.
        #
        # It is an attribute rather than a constructor argument so the build
        # script can offer it to any chunker that declares one, without knowing
        # which strategies have a slow phase. Left None, nothing is printed and
        # nothing in services/ writes to stdout.
        self.on_progress: Callable[[int, int, float], None] | None = None

    def params(self) -> dict[str, object]:
        return {
            "strategy": self.name,
            "breakpoint_percentile": self.percentile,
            "breakpoint_threshold": self.threshold,
            "gaps_measured": self.gap_count,
            "max_passage_tokens": self.max_tokens,
            "embed_batch": self.batch,
            "sentence_split": "danda for hi, terminal punctuation for en",
            "distance": "1 - cosine, on L2-normalised passage-prefixed vectors",
            "unit": "sub-passage",
        }

    # -- pass 1 ---------------------------------------------------------------

    def _capped_spans(self, passage: PassageRecord) -> tuple[list[tuple[int, int]], bool]:
        """Sentence spans inside the token cap, plus whether the cap bit."""
        text: str = passage["text"]
        enc = self.embedder.tokenizer.encode(text, add_special_tokens=False)
        offsets = enc.offsets
        truncated = len(offsets) > self.max_tokens
        limit = offsets[self.max_tokens - 1][1] if truncated else len(text)
        if truncated:
            self.truncated_count += 1
        spans = [(s, min(e, limit)) for s, e in sentence_spans(text, passage["language"]) if s < limit]
        return [(s, e) for s, e in spans if e > s], truncated

    # -- the protocol ---------------------------------------------------------

    def chunk(self, passages: Iterable[PassageRecord]) -> list[Chunk]:
        rows: list[PassageRecord] = list(passages)

        # Pass 1a: segment everything, and collect every sentence for embedding.
        spans_per_row: list[list[tuple[int, int]]] = []
        truncated_per_row: list[bool] = []
        flat: list[tuple[int, int]] = []  # (row index, sentence index)
        for i, p in enumerate(rows):
            spans, truncated = self._capped_spans(p)
            spans_per_row.append(spans)
            truncated_per_row.append(truncated)
            flat.extend((i, j) for j in range(len(spans)))

        # Pass 1b: embed every sentence, length-sorted, and reduce immediately to
        # the only thing pass 2 needs - the gap between each adjacent pair. The
        # vectors themselves are never all held at once: 929k x 384 floats is
        # 1.4 GB of nothing anyone reads twice.
        vectors = self._embed_sentences(rows, spans_per_row, flat)
        gaps_per_row: list[list[float]] = []
        at = 0
        all_gaps: list[float] = []
        for spans in spans_per_row:
            n = len(spans)
            block = vectors[at : at + n]
            at += n
            if n < 2:
                gaps_per_row.append([])
                continue
            # Vectors are L2-normalised by the embedder, so the dot product IS
            # the cosine and no renormalisation is needed here.
            sims = np.einsum("ij,ij->i", block[:-1], block[1:])
            gaps = (1.0 - sims).tolist()
            gaps_per_row.append(gaps)
            all_gaps.extend(gaps)

        # The threshold: one number, from the whole corpus, recorded in params().
        self.gap_count = len(all_gaps)
        self.threshold = (
            float(np.percentile(np.asarray(all_gaps, dtype=np.float64), self.percentile))
            if all_gaps
            else 0.0
        )

        # Pass 2: cut.
        out: list[Chunk] = []
        for i, p in enumerate(rows):
            out.extend(
                self._assemble(p, spans_per_row[i], gaps_per_row[i], truncated_per_row[i])
            )
        return out

    def _embed_sentences(
        self,
        rows: Sequence[PassageRecord],
        spans_per_row: Sequence[list[tuple[int, int]]],
        flat: Sequence[tuple[int, int]],
    ) -> np.ndarray:
        """Embed every sentence in the corpus, returned in `flat` order."""
        if not flat:
            return np.zeros((0, 384), dtype=np.float32)

        texts = [rows[i]["text"][slice(*spans_per_row[i][j])] for i, j in flat]
        lengths = np.fromiter(
            (len(self.embedder.tokenizer.encode(t, add_special_tokens=False).ids) for t in texts),
            dtype=np.int32,
            count=len(texts),
        )
        order = np.argsort(lengths, kind="stable")

        out: np.ndarray | None = None
        started = time.perf_counter()
        done = 0
        for start in range(0, len(order), self.batch):
            idx = order[start : start + self.batch]
            vecs = self.embedder.encode([texts[j] for j in idx], "passage")
            if out is None:
                out = np.empty((len(texts), vecs.shape[1]), dtype=np.float32)
            out[idx] = vecs
            done += len(idx)
            if self.on_progress is not None:
                self.on_progress(done, len(texts), time.perf_counter() - started)
        assert out is not None
        return out

    def _assemble(
        self,
        passage: PassageRecord,
        spans: list[tuple[int, int]],
        gaps: list[float],
        truncated: bool,
    ) -> list[Chunk]:
        if not spans:
            return []
        assert self.threshold is not None

        # Group sentence indices into runs, cutting where a gap clears the bar.
        groups: list[list[int]] = [[0]]
        for j, gap in enumerate(gaps):
            if gap > self.threshold:
                groups.append([j + 1])
            else:
                groups[-1].append(j + 1)

        chunks: list[Chunk] = []
        ordinal = 0
        text: str = passage["text"]
        for group in groups:
            start = spans[group[0]][0]
            end = spans[group[-1]][1]
            piece = text[start:end].strip()
            if not piece:
                continue
            chunks.append(
                Chunk(
                    chunk_id=f"{passage['passage_id']}#{ordinal}",
                    text=piece,
                    passage_id=passage["passage_id"],
                    parallel_id=passage["parallel_id"],
                    language=passage["language"],
                    ordinal=ordinal,
                    token_count=len(
                        self.embedder.tokenizer.encode(piece, add_special_tokens=False).ids
                    ),
                    truncated=truncated,
                    meta={"sentences": str(len(group))},
                )
            )
            ordinal += 1
        return chunks
