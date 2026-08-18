"""Shared serialization helpers for the build scripts.

At repo root rather than under scripts/ so both the numbered build scripts and
the test suite can import it without path juggling.
"""

from __future__ import annotations

from typing import Any, Sequence

import pyarrow as pa


def chunks_to_table(chunks: Sequence[Any]) -> pa.Table:
    """Serialize Chunk models to an Arrow table, safely.

    pyarrow cannot write a struct column whose fields are all unknown, and that
    is exactly what `meta` is for every strategy except C5: `meta={}` on every
    row gives `struct<>` with no children and the write raises
    ArrowNotImplementedError - after the expensive embedding work is finished.

    So drop `meta` entirely when no row populates it, and keep it when any row
    does. C5's payload is the one thing that must survive, and it does.
    """
    rows: list[dict[str, Any]] = [c.model_dump() for c in chunks]
    if not rows:
        return pa.table({})

    if not any(r.get("meta") for r in rows):
        for r in rows:
            r.pop("meta", None)

    return pa.Table.from_pylist(rows)
