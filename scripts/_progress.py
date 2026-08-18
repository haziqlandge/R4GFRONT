"""Live progress for long index builds. Shared by the 02* build scripts.

Index builds run unattended for 30 to 75 minutes. The only question anyone asks
while one is running is "is this working, and how long is left?" - this answers
both without needing to attach a debugger or guess from disk activity.

One design note that is not cosmetic: **ETA is computed from a recent window, not
from the average.** Builds here use length-sorted batching (02_build_indexes.py),
so short texts embed first and throughput falls steadily as sequences get longer.
An average-rate ETA is therefore optimistic for the entire second half of every
build - precisely when someone is deciding whether to keep waiting.
"""

from __future__ import annotations

import ctypes
import sys


def rss_gb() -> float:
    """Resident memory. Best-effort; returns 0.0 if it cannot be read, because a
    progress line must never be the thing that crashes a 70-minute build."""
    try:
        if sys.platform == "win32":
            import ctypes.wintypes as wt

            class PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", wt.DWORD),
                    ("PageFaultCount", wt.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            c = PMC()
            c.cb = ctypes.sizeof(c)
            ctypes.windll.psapi.GetProcessMemoryInfo(  # type: ignore[attr-defined]
                ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb  # type: ignore[attr-defined]
            )
            return float(c.WorkingSetSize) / (1024**3)
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / (1024**2)
    except Exception:
        pass
    return 0.0


def _fmt_duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    return f"{seconds / 60:.1f} min"


class Progress:
    """Throttled progress lines with a recent-window rate."""

    def __init__(self, total: int, label: str, min_interval_s: float = 5.0) -> None:
        self.total = max(total, 0)
        self.label = label
        self.min_interval_s = min_interval_s
        self._last_report_s = -1e9
        self._window_done = 0
        self._window_elapsed = 0.0

    # -- throttle -----------------------------------------------------------

    def should_report(self, done: int, elapsed_s: float) -> bool:
        """True at most once per interval - and always on the final item, so the
        completion line is never swallowed."""
        if done >= self.total:
            self._last_report_s = elapsed_s
            return True
        if elapsed_s - self._last_report_s >= self.min_interval_s:
            self._last_report_s = elapsed_s
            return True
        return False

    # -- rendering ----------------------------------------------------------

    def line(
        self,
        done: int,
        elapsed_s: float,
        extra: dict[str, str] | None = None,
    ) -> str:
        pct = (done / self.total * 100.0) if self.total else 0.0
        avg_rate = done / elapsed_s if elapsed_s > 0 else 0.0

        # Recent-window rate: what has happened since the previous call.
        d_done = done - self._window_done
        d_time = elapsed_s - self._window_elapsed
        recent_rate = (d_done / d_time) if d_time > 0 and d_done > 0 else avg_rate
        self._window_done, self._window_elapsed = done, elapsed_s

        remaining = max(self.total - done, 0)
        eta_s = remaining / recent_rate if recent_rate > 0 else 0.0

        parts = [
            f"    {self.label:<9}",
            f"{done:>9,}/{self.total:<9,}",
            f"{pct:>5.1f}%",
            f"{avg_rate:>7.1f}/s",
            f"elapsed {_fmt_duration(elapsed_s):>8}",
            f"eta {_fmt_duration(eta_s):>8}",
        ]
        if extra:
            parts.extend(f"{k} {v}" for k, v in extra.items())
        return "  ".join(parts)

    def report(
        self, done: int, elapsed_s: float, extra: dict[str, str] | None = None
    ) -> None:
        if self.should_report(done, elapsed_s):
            print(self.line(done, elapsed_s, extra), flush=True)
