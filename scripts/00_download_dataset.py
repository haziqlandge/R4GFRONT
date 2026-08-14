"""Download the MSMARCO-XI source parquet and verify its schema. Phase 1.

    python scripts/00_download_dataset.py

Why not datasets.load_dataset():
    The HF README documents `load_dataset("ai4bharat/MSMARCO-XI", "hi")`, and it
    does not work. The repo's loader script ms_marco_translations.py resolves
    paths like "validation/hival.jsonl", but the repo now contains parquet files
    named "validation/hinval.parquet". That mismatch is why the HF dataset viewer
    returns "500 Internal Server Error: The dataset generation failed".

    We therefore pull the parquet file directly with hf_hub_download and read it
    with pyarrow. This is also faster and uses far less disk than the datasets
    cache would.

Why only the Hindi file:
    Every row carries parallel `English_passages` and `Translated_passages` for
    the same ~10 passages, so one 462 MB download yields both an English and a
    Hindi corpus, aligned by position. Train files are ~3.7 GB each and are not
    needed - the validation split alone is ~100k queries.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pyarrow.parquet as pq
from huggingface_hub import HfApi, hf_hub_download

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from rag_core.config import DATASET_REPO, RAW_DIR, SOURCE_FILES, load_env  # noqa: E402

# HF_TOKEN is optional - the dataset is public and downloads fine without it.
# A read token only raises rate limits and speeds up transfers.
load_env()

# The schema we depend on, verified against the file rather than trusted from the
# HF README (which is stale on file format and on the language count).
REQUIRED_TOP_LEVEL = {
    "query",
    "Answer",
    "query_id",
    "query_type",
    "passages",
    "Eng_Query",
    "Eng_Answer",
    "source_lang",
    "target_lang",
}
REQUIRED_PASSAGE_FIELDS = {"is_selected", "English_passages", "Translated_passages"}


def resolve_revision() -> str:
    """The dataset commit SHA. Goes in the manifest; without it the slice is not
    reproducible, because the Hub could change the files under us."""
    info = HfApi().dataset_info(DATASET_REPO)
    return info.sha or "unknown"


def verify_schema(path: Path) -> dict[str, int]:
    """Assert the parquet schema before any sampling logic runs.

    Failing loudly here costs seconds. Failing silently produces a corpus with
    missing fields and costs a day.
    """
    pf = pq.ParquetFile(path)
    schema = pf.schema_arrow

    top = set(schema.names)
    missing = REQUIRED_TOP_LEVEL - top
    if missing:
        raise SystemExit(f"schema mismatch in {path.name}: missing {sorted(missing)}")

    passages_type = schema.field("passages").type
    passage_fields = {passages_type.field(i).name for i in range(passages_type.num_fields)}
    missing_p = REQUIRED_PASSAGE_FIELDS - passage_fields
    if missing_p:
        raise SystemExit(
            f"schema mismatch in {path.name}: passages missing {sorted(missing_p)}"
        )

    print(f"  schema OK: {len(top)} top-level fields, passages has {sorted(passage_fields)}")
    return {"rows": pf.metadata.num_rows, "row_groups": pf.num_row_groups}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-download even if cached")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    revision = resolve_revision()
    import os

    print(f"\n  dataset  {DATASET_REPO}")
    print(f"  revision {revision}")
    print(f"  auth     {'HF_TOKEN' if os.environ.get('HF_TOKEN') else 'anonymous'}")

    for remote in SOURCE_FILES:
        print(f"\n  {remote}")
        local = hf_hub_download(
            repo_id=DATASET_REPO,
            repo_type="dataset",
            filename=remote,
            revision=revision,
            local_dir=RAW_DIR,
            force_download=args.force,
        )
        path = Path(local)
        size_mb = path.stat().st_size / 1_048_576
        print(f"  cached at {path}  ({size_mb:.0f} MB)")
        stats = verify_schema(path)
        print(f"  rows {stats['rows']:,} in {stats['row_groups']} row groups")

    print("\n  ready. next: python scripts/01_freeze_slice.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
