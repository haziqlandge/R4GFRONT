"""Freeze a reproducible corpus slice from MSMARCO-XI. Phase 1.

    python scripts/01_freeze_slice.py
    python scripts/01_freeze_slice.py --verify artifacts/slice_manifest.json

Rules.md section 5: the slice is frozen here and never silently changed. If it
changes, every number in bench/results/ is invalidated and must be regenerated.

Design notes that are not obvious from the code:

  Dedup key is the ENGLISH text, for both languages.
      MS MARCO reuses passages across queries. Deduplicating each language
      independently would break the en/hi pairing, because the two languages
      would collapse different sets of rows. Keying both on the English sha1
      keeps every parallel pair intact, which is what makes `parallel_id` - and
      therefore the cross-lingual retrieval demo - measurable.

  Ground truth lives in queries.parquet, not on the passage.
      `is_selected` is a property of a (query, passage) pair, not of a passage.
      After dedup a passage's first-occurrence query is arbitrary, so a passage
      level `is_selected` would be misleading. Gold passage ids per query are
      recorded on the query instead, which is what Recall@10 / MRR@10 / nDCG@10
      actually need in Phase 3. The passage keeps `is_selected_any` as a
      corpus-level signal for C5 metadata-aware chunking.

  Reproducibility is checked on record content, not on the file bytes.
      Parquet output can shift with a pyarrow version bump. `records_sha256` is
      computed over the sorted records themselves and is the authoritative
      check; the file sha256 is recorded too but is advisory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.config import (  # noqa: E402
    BENCH_DIR,
    BENCH_QUERY_COUNT,
    DATASET_REPO,
    DEV_QUERY_COUNT,
    PASSAGES_PARQUET,
    QUERIES_PARQUET,
    RAW_DIR,
    SCRIPT_BY_LANGUAGE,
    SEED,
    SLICE_LANGUAGES,
    SLICE_MANIFEST,
    SLICE_QUERY_COUNT,
    SOURCE_FILES,
    TEST_QUERY_COUNT,
)

BATCH_ROWS = 2_000
# Candidates drawn before validity filtering. Measured acceptance on hinval is
# ~55%: MS MARCO marks no passage as is_selected for roughly 4 rows in 9 ("no
# answer present"), and those rows are useless as retrieval ground truth. 2.0x
# clears 15,000 with margin without reading much more of the file.
OVERSAMPLE = 2.0
_WS = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def dedup_key(text: str) -> str:
    """Hash of normalized text. Normalization affects the key only; the stored
    passage text stays verbatim, because the extractive answer path returns spans
    from it and must not return mangled text."""
    return hashlib.sha1(_WS.sub(" ", text.strip().lower()).encode("utf-8")).hexdigest()


def source_path(remote: str) -> Path:
    return RAW_DIR / remote


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "uncommitted"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def iter_rows(path: Path, columns: list[str]) -> Iterator[dict[str, Any]]:
    """Stream the parquet in batches. The file is a single 440 MB row group, so
    reading it whole would spike memory for no reason."""
    pf = pq.ParquetFile(path)
    for batch in pf.iter_batches(batch_size=BATCH_ROWS, columns=columns):
        yield from batch.to_pylist()


# ---------------------------------------------------------------------------
# Slice construction
# ---------------------------------------------------------------------------


def select_query_ids(path: Path) -> list[int]:
    """Pass 1: cheap columns only, to pick candidate query ids deterministically."""
    seen: set[int] = set()
    candidates: list[int] = []
    for row in iter_rows(path, ["query_id", "query"]):
        qid = row["query_id"]
        if qid is None or qid in seen:
            continue
        if not (row["query"] or "").strip():
            continue
        seen.add(qid)
        candidates.append(qid)

    candidates.sort()  # deterministic order before the seeded shuffle
    rng = random.Random(SEED)
    rng.shuffle(candidates)
    take = min(len(candidates), int(SLICE_QUERY_COUNT * OVERSAMPLE))
    return candidates[:take]


def build_slice(path: Path, ranked_ids: list[int]) -> dict[str, Any]:
    """Pass 2: full columns, kept in rank order until the slice is full."""
    rank = {qid: i for i, qid in enumerate(ranked_ids)}

    accepted: dict[int, dict[str, Any]] = {}  # rank -> query record
    raw_passages: dict[int, list[dict[str, Any]]] = {}  # rank -> passage rows

    for row in iter_rows(
        path,
        ["query_id", "query", "Answer", "query_type", "Eng_Query", "Eng_Answer", "passages"],
    ):
        qid = row["query_id"]
        r = rank.get(qid)
        if r is None or r in accepted:
            continue

        p = row["passages"] or {}
        eng = p.get("English_passages") or []
        trans = p.get("Translated_passages") or []
        sel = p.get("is_selected") or []
        n = min(len(eng), len(trans), len(sel))
        if n == 0 or sum(sel[:n]) == 0:
            continue  # no passages, or no answer-bearing passage: unusable for eval

        kept = [
            {"position": i, "en": eng[i], "hi": trans[i], "is_selected": bool(sel[i])}
            for i in range(n)
            if (eng[i] or "").strip() and (trans[i] or "").strip()
        ]
        if not kept or not any(k["is_selected"] for k in kept):
            continue

        accepted[r] = {
            "query_id": qid,
            "query_hi": row["query"],
            "query_en": row["Eng_Query"],
            "answer_hi": row["Answer"],
            "answer_en": row["Eng_Answer"],
            "query_type": row["query_type"] or "UNKNOWN",
        }
        raw_passages[r] = kept

    chosen_ranks = sorted(accepted)[:SLICE_QUERY_COUNT]
    return {
        "ranks": chosen_ranks,
        "queries": accepted,
        "passages": raw_passages,
    }


def materialize(sliced: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Explode into Passage records, dedup on English text, attach gold ids."""
    canonical: dict[str, dict[str, str]] = {}  # dedup key -> {"en": pid, "hi": pid, "parallel": id}
    passages: list[dict[str, Any]] = []
    queries: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    duplicates = 0

    for r in sliced["ranks"]:
        q = sliced["queries"][r]
        qid = q["query_id"]
        gold_en: list[str] = []
        gold_hi: list[str] = []

        for item in sliced["passages"][r]:
            pos = item["position"]
            key = dedup_key(item["en"])

            if key not in canonical:
                parallel_id = f"{qid}:{pos}"
                ids = {
                    "en": f"{qid}:{pos}:en",
                    "hi": f"{qid}:{pos}:hi",
                    "parallel": parallel_id,
                }
                canonical[key] = ids
                for lang, text in (("en", item["en"]), ("hi", item["hi"])):
                    passages.append({
                        "passage_id": ids[lang],
                        "text": text,
                        "language": lang,
                        "script": SCRIPT_BY_LANGUAGE[lang],
                        "query_id": qid,
                        "position": pos,
                        "parallel_id": parallel_id,
                        "text_sha1": key,
                        "is_selected_any": False,  # filled below
                    })
            else:
                duplicates += 1

            if item["is_selected"]:
                selected_keys.add(key)
                gold_en.append(canonical[key]["en"])
                gold_hi.append(canonical[key]["hi"])

        queries.append({
            "query_id": qid,
            "query_hi": q["query_hi"],
            "query_en": q["query_en"],
            "answer_hi": q["answer_hi"],
            "answer_en": q["answer_en"],
            "query_type": q["query_type"],
            "gold_en_ids": gold_en,
            "gold_hi_ids": gold_hi,
            "split": "",  # assigned by assign_splits
        })

    for p in passages:
        p["is_selected_any"] = p["text_sha1"] in selected_keys

    passages.sort(key=lambda p: (p["query_id"], p["position"], p["language"]))
    queries.sort(key=lambda q: q["query_id"])
    return passages, queries, {"duplicates_dropped": duplicates}


def assign_splits(queries: list[dict[str, Any]]) -> dict[str, list[int]]:
    """test / dev / corpus_only. Disjoint, seeded, recorded in the manifest.

    All queries' passages are indexed regardless of split. The split governs which
    queries may be TUNED AGAINST - Rules.md section 5 forbids tuning against a
    benchmark you are still editing.
    """
    ids = sorted(q["query_id"] for q in queries)
    rng = random.Random(SEED + 1)
    rng.shuffle(ids)

    test = sorted(ids[:TEST_QUERY_COUNT])
    dev = sorted(ids[TEST_QUERY_COUNT:TEST_QUERY_COUNT + DEV_QUERY_COUNT])
    corpus_only = sorted(ids[TEST_QUERY_COUNT + DEV_QUERY_COUNT:])

    label = {qid: "test" for qid in test}
    label.update({qid: "dev" for qid in dev})
    label.update({qid: "corpus_only" for qid in corpus_only})
    for q in queries:
        q["split"] = label[q["query_id"]]

    return {"test": test, "dev": dev, "corpus_only": corpus_only}


def records_sha256(passages: list[dict[str, Any]], queries: list[dict[str, Any]]) -> str:
    """Content hash, independent of parquet encoding. The authoritative check."""
    h = hashlib.sha256()
    for p in passages:
        h.update(f"{p['passage_id']}\x1f{p['text_sha1']}\x1f{p['language']}\x1e".encode())
    for q in queries:
        h.update(f"{q['query_id']}\x1f{q['split']}\x1f{','.join(q['gold_en_ids'])}\x1e".encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_parquet(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path, compression="zstd")


def write_bench_queries(queries: list[dict[str, Any]]) -> Path:
    """bench/queries_250.jsonl, drawn from the test split.

    Rules.md section 5: frozen before any optimization starts. Written here, in
    Phase 1, precisely so that it exists before there is anything to tune.
    """
    test = [q for q in queries if q["split"] == "test"][:BENCH_QUERY_COUNT]
    path = BENCH_DIR / f"queries_{BENCH_QUERY_COUNT}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for q in test:
            fh.write(json.dumps({
                "query_id": q["query_id"],
                "query_hi": q["query_hi"],
                "query_en": q["query_en"],
                "query_type": q["query_type"],
                "gold_en_ids": q["gold_en_ids"],
                "gold_hi_ids": q["gold_hi_ids"],
            }, ensure_ascii=False) + "\n")
    return path


def build_manifest(
    passages: list[dict[str, Any]],
    queries: list[dict[str, Any]],
    splits: dict[str, list[int]],
    stats: dict[str, int],
    revision: str,
) -> dict[str, Any]:
    per_lang: dict[str, int] = {}
    for p in passages:
        per_lang[p["language"]] = per_lang.get(p["language"], 0) + 1

    per_type: dict[str, int] = {}
    for q in queries:
        per_type[q["query_type"]] = per_type.get(q["query_type"], 0) + 1

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by_git_sha": git_sha(),
        "dataset": {
            "repo_id": DATASET_REPO,
            "revision": revision,
            "source_files": list(SOURCE_FILES),
            "note": (
                "Downloaded as parquet via hf_hub_download. load_dataset() does not "
                "work on this repo: its loader script points at .jsonl paths that no "
                "longer exist, which is why the HF dataset viewer errors."
            ),
        },
        "sampling": {
            "seed": SEED,
            "target_query_count": SLICE_QUERY_COUNT,
            "actual_query_count": len(queries),
            "oversample_factor": OVERSAMPLE,
            "filters": [
                "non-empty query",
                "at least one passage with non-empty text in both languages",
                "at least one is_selected passage",
            ],
        },
        "corpus": {
            "languages": list(SLICE_LANGUAGES),
            "scripts": [SCRIPT_BY_LANGUAGE[lang] for lang in SLICE_LANGUAGES],
            "total_passages": len(passages),
            "passages_per_language": per_lang,
            "duplicate_pairs_dropped": stats["duplicates_dropped"],
            "dedup_key": "sha1(lowercased, whitespace-collapsed English passage text)",
            "answer_bearing_passages": sum(1 for p in passages if p["is_selected_any"]),
        },
        "queries": {
            "per_query_type": per_type,
            "splits": {name: len(ids) for name, ids in splits.items()},
            "split_ids": splits,
        },
        "integrity": {
            "records_sha256": records_sha256(passages, queries),
            "passages_parquet_sha256": None,  # filled after write
            "queries_parquet_sha256": None,
        },
    }


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def generate(revision: str) -> dict[str, Any]:
    src = source_path(SOURCE_FILES[0])
    if not src.exists():
        raise SystemExit(f"missing {src}. Run scripts/00_download_dataset.py first.")

    print(f"\n  source   {src.name}")
    print("  pass 1   selecting candidate query ids")
    ranked = select_query_ids(src)
    print(f"           {len(ranked):,} candidates (seed {SEED})")

    print("  pass 2   reading passages")
    sliced = build_slice(src, ranked)
    print(f"           {len(sliced['ranks']):,} queries accepted")

    passages, queries, stats = materialize(sliced)
    splits = assign_splits(queries)
    manifest = build_manifest(passages, queries, splits, stats, revision)
    return {"passages": passages, "queries": queries, "manifest": manifest}


def cmd_generate(revision: str) -> int:
    built = generate(revision)
    passages, queries, manifest = built["passages"], built["queries"], built["manifest"]

    write_parquet(passages, PASSAGES_PARQUET)
    write_parquet(queries, QUERIES_PARQUET)
    manifest["integrity"]["passages_parquet_sha256"] = file_sha256(PASSAGES_PARQUET)
    manifest["integrity"]["queries_parquet_sha256"] = file_sha256(QUERIES_PARQUET)

    SLICE_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    bench_path = write_bench_queries(queries)

    c = manifest["corpus"]
    print(f"\n  passages {c['total_passages']:,}  ({c['passages_per_language']})")
    print(f"  dedup    {c['duplicate_pairs_dropped']:,} duplicate pairs dropped")
    print(f"  gold     {c['answer_bearing_passages']:,} answer-bearing passages")
    print(f"  splits   {manifest['queries']['splits']}")
    print(f"  types    {manifest['queries']['per_query_type']}")
    print(f"\n  wrote {PASSAGES_PARQUET.name}, {QUERIES_PARQUET.name}, "
          f"{SLICE_MANIFEST.name}, {bench_path.name}")
    print(f"  records_sha256 {manifest['integrity']['records_sha256']}")
    print("\n  verify with: python scripts/01_freeze_slice.py --verify "
          "artifacts/slice_manifest.json")
    return 0


def cmd_verify(manifest_path: Path) -> int:
    """Phase 1 exit criterion: a second team member reproduces the identical slice
    from the manifest alone."""
    committed = json.loads(manifest_path.read_text(encoding="utf-8"))
    revision = committed["dataset"]["revision"]

    print(f"\n  verifying against {manifest_path}")
    print(f"  dataset revision {revision}")
    rebuilt = generate(revision)["manifest"]

    checks = [
        ("records_sha256",
         committed["integrity"]["records_sha256"],
         rebuilt["integrity"]["records_sha256"]),
        ("total_passages",
         committed["corpus"]["total_passages"],
         rebuilt["corpus"]["total_passages"]),
        ("actual_query_count",
         committed["sampling"]["actual_query_count"],
         rebuilt["sampling"]["actual_query_count"]),
        ("split_ids",
         committed["queries"]["split_ids"],
         rebuilt["queries"]["split_ids"]),
    ]

    ok = True
    print()
    for name, expected, actual in checks:
        match = expected == actual
        ok &= match
        shown = expected if not isinstance(expected, (dict, list)) else "<collection>"
        print(f"  {'PASS' if match else 'FAIL'}  {name:<20} {shown}")

    if not ok:
        print("\n  SLICE DOES NOT REPRODUCE. Do not use any benchmark taken against it.")
        return 1
    print("\n  slice reproduces exactly.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path, metavar="MANIFEST",
                        help="rebuild from a manifest and assert it matches")
    parser.add_argument("--revision", default=None,
                        help="dataset revision SHA (default: resolve from the Hub)")
    args = parser.parse_args()

    if args.verify:
        return cmd_verify(args.verify)

    revision = args.revision
    if revision is None:
        from huggingface_hub import HfApi

        revision = HfApi().dataset_info(DATASET_REPO).sha or "unknown"
    return cmd_generate(revision)


if __name__ == "__main__":
    raise SystemExit(main())
