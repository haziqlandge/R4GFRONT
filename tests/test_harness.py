"""Tests for the Phase 0 measurement rig.

Rules.md section 6: tests for the harness, the chunkers and the guardrails.
The rig is tested first because every published number depends on it being right.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from rag_core.harness.trace import Span, Trace, new_trace_id, span  # noqa: E402


# ---------------------------------------------------------------------------
# Span
# ---------------------------------------------------------------------------


def test_span_ms_is_zero_until_closed() -> None:
    s = Span(name="embed_query", start_ns=1_000_000_000)
    assert s.ms == 0.0
    s.end_ns = 1_012_000_000
    assert s.ms == pytest.approx(12.0)


def test_span_close_sets_status_and_is_idempotent_on_end() -> None:
    s = Span(name="rerank", start_ns=0, end_ns=5_000_000)
    s.close(status="fallback", detail="RRF order used")
    assert s.end_ns == 5_000_000  # already closed, not overwritten
    assert s.status == "fallback"
    assert s.detail == "RRF order used"


def test_span_serialize_shape() -> None:
    s = Span(name="fuse", start_ns=0, end_ns=400_000, status="ok")
    assert s.serialize() == {"name": "fuse", "ms": 0.4, "status": "ok"}


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


def test_trace_ids_are_unique() -> None:
    assert len({new_trace_id() for _ in range(1000)}) == 1000


def test_span_context_manager_records_ok() -> None:
    trace = Trace()
    with span(trace, "input_guard"):
        pass
    trace.finish()
    assert len(trace.spans) == 1
    assert trace.spans[0].name == "input_guard"
    assert trace.spans[0].status == "ok"
    assert trace.spans[0].end_ns is not None


def test_span_context_manager_marks_failed_and_reraises() -> None:
    trace = Trace()
    with pytest.raises(ValueError):
        with span(trace, "embed_query"):
            raise ValueError("onnx session missing")
    assert trace.spans[0].status == "failed"
    assert trace.spans[0].detail == "ValueError"


def test_skipped_stage_is_visible_in_trace() -> None:
    """Latency.md 4.1: visible absence, not invisible absence."""
    trace = Trace()
    trace.add_skipped("rerank", detail="insufficient budget")
    trace.finish()
    stages = trace.serialize()["stages"]
    assert stages[0]["name"] == "rerank"
    assert stages[0]["status"] == "skipped"
    assert stages[0]["ms"] == 0.0


def test_remaining_budget_decreases_and_can_go_negative() -> None:
    trace = Trace(budget_ms=200.0)
    assert trace.remaining_ms <= 200.0
    tight = Trace(budget_ms=0.0)
    tight.finish()
    assert tight.remaining_ms <= 0.0
    assert tight.over_budget() is (tight.total_ms > 0.0)


def test_trace_serialize_matches_architecture_contract() -> None:
    """Architecture.md section 9. LatencyWaterfall.tsx consumes this shape."""
    trace = Trace(budget_ms=200.0)
    with span(trace, "dense_search"):
        pass
    trace.add_skipped("rerank")
    trace.finish()

    out = trace.serialize()
    assert set(out) == {"total_ms", "budget_ms", "stages"}
    assert out["budget_ms"] == 200.0
    assert [s["name"] for s in out["stages"]] == ["dense_search", "rerank"]
    for stage in out["stages"]:
        assert set(stage) >= {"name", "ms", "status"}


def test_total_ms_is_band_time_not_span_sum() -> None:
    """These differ, and the band total is the number that gets published."""
    trace = Trace()
    with span(trace, "a"):
        pass
    with span(trace, "b"):
        pass
    trace.finish()
    assert trace.total_ms >= trace.spans_ms


# ---------------------------------------------------------------------------
# Percentiles
# ---------------------------------------------------------------------------


def _bench_module():  # type: ignore[no-untyped-def]
    """Import 04_bench_latency.py, whose filename is not a valid module name.

    The module must be registered in sys.modules before exec_module: its
    @dataclass under `from __future__ import annotations` resolves type strings
    via sys.modules[cls.__module__], which is None for an unregistered module.
    """
    import importlib.util

    name = "bench_latency"
    if name in sys.modules:
        return sys.modules[name]

    path = Path(__file__).resolve().parents[1] / "scripts" / "04_bench_latency.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_summarize_against_known_array() -> None:
    """P100 must be the true maximum, not an interpolated 99.9th."""
    bench = _bench_module()
    samples = [float(x) for x in range(1, 101)]  # 1..100
    s = bench.summarize(samples)

    assert s["p100"] == 100.0
    assert s["min"] == 1.0
    assert s["mean"] == pytest.approx(50.5)
    assert s["p50"] == pytest.approx(
        float(np.percentile(samples, 50, method="nearest"))
    )
    assert s["p70"] == pytest.approx(
        float(np.percentile(samples, 70, method="nearest"))
    )
    assert s["p50"] < s["p70"] < s["p90"] < s["p99"] <= s["p100"]


def test_summarize_single_sample_has_zero_stddev() -> None:
    bench = _bench_module()
    s = bench.summarize([42.0])
    assert s["stddev"] == 0.0
    assert s["p50"] == s["p100"] == 42.0


@pytest.mark.asyncio
async def test_warmup_runs_are_discarded() -> None:
    bench = _bench_module()
    result = await bench.run_benchmark(bench.stub_pipeline, runs=3, warmup=2)
    assert len(result.samples_ms) == 3
    assert result.warmup == 2


@pytest.mark.asyncio
async def test_stub_pipeline_emits_every_declared_stage() -> None:
    bench = _bench_module()
    trace = await bench.stub_pipeline()
    assert [s.name for s in trace.spans] == list(bench.STUB_STAGE_MS)
    assert all(s.status == "ok" for s in trace.spans)


@pytest.mark.asyncio
async def test_stub_total_is_close_to_known_sum() -> None:
    """The rig check, as a test. Harness overhead must stay under 5 ms."""
    bench = _bench_module()
    result = await bench.run_benchmark(bench.stub_pipeline, runs=20, warmup=5)
    s = bench.summarize(result.samples_ms)
    assert abs(s["p50"] - bench.STUB_EXPECTED_MS) < 5.0
