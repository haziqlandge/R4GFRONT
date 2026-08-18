"""Build the BM25 lexical index for a strategy. Job J11.

    python scripts/02b_build_lexical.py --strategy c1

OWNED BY BENCH, like 02_build_indexes.py.

Deliberately a separate script rather than a step inside 02_build_indexes.py.
The lexical index needs no embeddings at all, so folding it into the dense build
would mean a 30-minute re-embed of C1 just to add a BM25 index that takes about
a minute - and the same again for every strategy already on disk. Splitting them
also means a tokenizer change can be re-run against every existing index without
touching a single vector.

The row-alignment invariant that fusion depends on (see lexical.py) is satisfied
by construction here: this reads the very `chunks.parquet` that the dense index's
row numbers already index into, so row i means the same chunk to both. That is
the reason this script reads the built index rather than re-chunking passages -
re-deriving the chunk list would risk producing a different order for the same
inputs, which fusion could not detect.

Writes to artifacts/indexes/<strategy>/bm25/. Nothing under artifacts/ is
committed except meta.json (Phase3-Parallel.md 4).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.config import (  # noqa: E402
    BM25_B,
    BM25_K1,
    BM25_METHOD,
    INDEX_DIR,
    LEXICAL_DIRNAME,
)
from rag_core.retrieval.lexical import BM25Index, tokenize  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", default="c1")
    args = parser.parse_args()

    index_dir = INDEX_DIR / args.strategy
    chunks_path = index_dir / "chunks.parquet"
    if not chunks_path.exists():
        print(f"  {chunks_path} missing.")
        print(f"  Build the dense index first: "
              f"python scripts/02_build_indexes.py --strategy {args.strategy}")
        return 1

    print("")
    print(f"  strategy   {args.strategy}")
    print(f"  bm25       k1={BM25_K1} b={BM25_B} method={BM25_METHOD}")

    chunks = pq.read_table(chunks_path, columns=["text", "language"]).to_pylist()
    print(f"  chunks     {len(chunks):,}  (from {chunks_path.name}, row-aligned)")

    t0 = time.perf_counter()
    index = BM25Index()
    index.build(chunks)
    build_secs = time.perf_counter() - t0
    print(f"  built      {build_secs:.1f}s")

    out_dir = index_dir / LEXICAL_DIRNAME
    index.save(out_dir)

    # Vocabulary and term counts per language, because they are the cheapest
    # available check that the Indic tokenizer actually ran. Whitespace-splitting
    # Devanagari fails silently - you still get an index - but it inflates the
    # Hindi vocabulary with danda-suffixed duplicates of terms that already
    # exist. A hi vocabulary far larger than en on a parallel corpus is the
    # symptom to look for. See ISSUES.md I5.
    per_language: dict[str, dict[str, int]] = {}
    for language in ("en", "hi"):
        texts = [c["text"] for c in chunks if c.get("language") == language]
        vocab: set[str] = set()
        term_count = 0
        for text in texts:
            tokens = tokenize(text, language)
            vocab.update(tokens)
            term_count += len(tokens)
        per_language[language] = {
            "chunks": len(texts),
            "terms": term_count,
            "vocabulary": len(vocab),
        }
        print(f"  {language}         {len(texts):,} chunks, {term_count:,} terms, "
              f"{len(vocab):,} vocabulary")

    size_mb = sum(f.stat().st_size for f in out_dir.glob("*")) / 1_048_576
    meta = {
        "strategy": args.strategy,
        "params": {"k1": BM25_K1, "b": BM25_B, "method": BM25_METHOD},
        "tokenizer": "indic-nlp trivial_tokenize (hi) / unicode word regex (en)",
        "counts": {"chunks": len(chunks), "per_language": per_language},
        "build_seconds": round(build_secs, 1),
        "size_mb": round(size_mb, 1),
        # Bound to the dense index this is aligned with. If the dense index is
        # rebuilt, this one is stale and fusion would combine mismatched rows.
        "aligned_with": str(chunks_path.relative_to(INDEX_DIR.parent)),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print("")
    print(f"  wrote {out_dir}  ({size_mb:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
