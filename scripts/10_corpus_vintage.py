"""How old is this corpus, actually? Measured, because we are about to say it on screen.

`ISSUES.md` I26 and the whole guardrail story rest on the system being faithful
to its corpus rather than to the present day. The demo answers "what is the
population of India" with 1.21 billion and "what is the price of bitcoin" with
$1,242, and both are correct *quotations* of a corpus that stopped being current
years ago.

Saying so on screen turns the most embarrassing thing about the demo into the
most honest thing about it - but only if the date is right. A council review
of this proposal recommended the line "extracted verbatim from a 2016 MS
MARCO snapshot". **Nothing in this repo establishes 2016**, and the corpus
plainly contains material from later than that: it describes Venkaiah Naidu as
Vice-President "since 11 August 2017" and Trump as the "45th and current
President". Printing an unverified date on a page whose pitch is that every
figure names its source is exactly the failure `DONT-FORGET.md` 9 is about.

So this counts. Method: every four-digit year from 1950 to 2029 mentioned in the
English half of the corpus. Not a publication date - passages carry no metadata -
but a content-coverage profile, which is the honest thing to describe anyway.
A corpus written in year N discusses N and the years before it, and mentions
later years only as predictions.

    python scripts/10_corpus_vintage.py
"""

from __future__ import annotations

import collections
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

import pyarrow.parquet as pq

ROOT: Final[Path] = Path(__file__).resolve().parents[1]
PASSAGES: Final[Path] = ROOT / "artifacts" / "passages.parquet"
RESULTS: Final[Path] = ROOT / "bench" / "results"

YEAR: Final[re.Pattern[str]] = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")


def main() -> int:
    table = pq.read_table(PASSAGES, columns=["text", "language"])
    texts = table.column("text").to_pylist()
    langs = table.column("language").to_pylist()

    counts: collections.Counter[int] = collections.Counter()
    english = 0
    for text, lang in zip(texts, langs):
        if lang != "en":
            continue
        english += 1
        for y in YEAR.findall(text):
            counts[int(y)] += 1

    peak_year, peak_n = counts.most_common(1)[0]

    # The "cliff": the last year before mentions collapse. A year mentioned a
    # tenth as often as the peak is being predicted, not reported.
    threshold = peak_n * 0.10
    covered = sorted(y for y, n in counts.items() if n >= threshold)
    last_covered = covered[-1] if covered else peak_year

    print(f"english passages scanned   {english:,}")
    print(f"distinct years mentioned   {len(counts)}")
    print(f"peak year                  {peak_year}  ({peak_n:,} mentions)")
    print(f"last well-covered year     {last_covered}")
    print()
    print("mentions by year, 2008 onward:")
    for y in range(2008, 2030):
        n = counts.get(y, 0)
        bar = "#" * int(n / peak_n * 50)
        mark = "  <- peak" if y == peak_year else ("  <- cliff" if y == last_covered else "")
        print(f"  {y}  {n:>6}  {bar}{mark}")

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "what": "content-coverage profile of the frozen corpus, by year mentioned",
        "method": (
            "every four-digit year 1950-2029 matched in the English half of "
            "passages.parquet. Not a publication date: the corpus carries no "
            "date metadata. A corpus written in year N discusses N and earlier, "
            "and mentions later years only as predictions."
        ),
        "english_passages": english,
        "peak_year": peak_year,
        "peak_mentions": peak_n,
        "last_well_covered_year": last_covered,
        "cliff_rule": "years with at least 10% of the peak year's mentions",
        "mentions_by_year": {str(y): counts[y] for y in sorted(counts)},
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    path = RESULTS / f"{stamp}-corpus-vintage.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
