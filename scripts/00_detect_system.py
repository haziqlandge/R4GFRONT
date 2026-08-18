"""Detect this machine, write LOCAL_SYSTEM_INFO.md, make it ready. Run this FIRST,
before anything else in PREREQUISITES.md.

    python scripts/00_detect_system.py

Why this exists: the project runs across three heterogeneous machines (Devices.md)
and every one of them has cost someone time by not matching what the docs assumed
- a GT 710 that looks like a GPU and is not, an sm_120 card that needs a newer CUDA
than the obvious install gives you, thread counts tuned on one box that mislead on
another. This script looks at the ACTUAL machine instead of assuming, and writes
what it finds down so nobody re-derives it by hand or by folklore.

Deliberately stdlib only. This is the first thing anyone runs, possibly before
`pip install -r requirements-dev.txt`, so it must not depend on anything that
install would provide.

Two output files, both gitignored - see the note in each file's own header for why:

  LOCAL_SYSTEM_INFO.md       - what this machine IS. Overwritten every run, so it
                                never goes stale. Read-only in spirit: this script
                                is the only thing that writes it.
  LOCAL_SYSTEM_ADDITIONS.md  - what this script DID to this machine. Appended, not
                                overwritten - a running log, because "what changed
                                and when" is exactly what you want after something
                                breaks three days later.

This script only ever CREATES directories and files. It never edits tracked repo
code, never touches git config, never installs packages. If it recommends
something (a role, a thread count), that is advisory prose in LOCAL_SYSTEM_INFO.md,
not a value written into config.py - Devices.md is the authority on machine roles,
and ISSUES.md I6 is the standing reminder that a locally-measured thread count does
not transfer between boxes without being re-measured on the real workload.
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Windows' default console codepage is not UTF-8. The .md files are written with
# explicit UTF-8 and are unaffected; only terminal print() needs this, and only on
# Windows - reconfigure() raises on stdout that has already been detached, so this
# stays defensive.
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

REPO_ROOT = Path(__file__).resolve().parents[1]
INFO_PATH = REPO_ROOT / "LOCAL_SYSTEM_INFO.md"
ADDITIONS_PATH = REPO_ROOT / "LOCAL_SYSTEM_ADDITIONS.md"


# ---------------------------------------------------------------------------
# Detection. Every function degrades to "unknown" rather than raising - a
# partial report on an unusual machine is more useful than a crash.
# ---------------------------------------------------------------------------


def _run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def cpu_info() -> dict[str, object]:
    logical = os.cpu_count() or 0
    physical = logical
    if sys.platform == "win32":
        out = _run(["wmic", "cpu", "get", "NumberOfCores", "/value"])
        if out:
            for line in out.splitlines():
                if line.startswith("NumberOfCores="):
                    try:
                        physical = int(line.split("=", 1)[1])
                    except ValueError:
                        pass
    else:
        out = _run(["lscpu"])
        if out:
            for line in out.splitlines():
                if line.strip().startswith("Core(s) per socket"):
                    try:
                        cores = int(line.split(":")[1].strip())
                        sockets_line = [
                            ln for ln in out.splitlines() if ln.strip().startswith("Socket(s)")
                        ]
                        sockets = int(sockets_line[0].split(":")[1].strip()) if sockets_line else 1
                        physical = cores * sockets
                    except (ValueError, IndexError):
                        pass
    return {
        "model": platform.processor() or "unknown",
        "logical_cores": logical,
        "physical_cores": physical,
    }


def ram_info() -> dict[str, object]:
    if sys.platform == "win32":
        class MEMSTATUS(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMSTATUS()
        stat.dwLength = ctypes.sizeof(MEMSTATUS)
        try:
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return {"total_gb": round(stat.ullTotalPhys / (1024**3), 1)}
        except Exception:
            return {"total_gb": "unknown"}
    else:
        try:
            with open("/proc/meminfo") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return {"total_gb": round(kb / 1_048_576, 1)}
        except OSError:
            pass
        return {"total_gb": "unknown"}


def gpu_info() -> list[dict[str, str]]:
    """nvidia-smi if present. Absence is a normal, expected result - BENCH has no
    usable GPU (Devices.md 1) and this must not treat that as an error.

    `compute_cap` is not a valid --query-gpu field on every driver version (it
    silently makes the whole query fail on some, exit code 2, zero rows - which
    looks identical to "no GPU" and would have hidden BENCH's own GT 710 from its
    own report). Query without it, and look up compute capability from the name
    afterward instead of trusting the driver to report it.
    """
    gpus: list[dict[str, str]] = []
    out = _run([
        "nvidia-smi",
        "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader",
    ])
    if not out:
        return gpus
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            gpus.append({
                "name": parts[0], "memory": parts[1], "driver": parts[2],
                "compute_capability": _compute_capability_hint(parts[0]),
            })
    return gpus


# Devices.md 1: three specific cards this project cares about. Anything else
# gets an honest "unknown - check https://developer.nvidia.com/cuda-gpus".
_KNOWN_COMPUTE_CAPS: dict[str, str] = {
    "GT 710": "sm_35 (Kepler - below the floor of any current CUDA wheel, Devices.md 1)",
    "3060 Ti": "sm_86 (Ampere)",
    "5070 Ti": "sm_120 (Blackwell - needs CUDA 12.8+, ISSUES.md I14)",
}


def _compute_capability_hint(gpu_name: str) -> str:
    for needle, cap in _KNOWN_COMPUTE_CAPS.items():
        if needle in gpu_name:
            return cap
    return "unknown - check https://developer.nvidia.com/cuda-gpus"


def disk_free_gb() -> float:
    return round(shutil.disk_usage(REPO_ROOT).free / (1024**3), 1)


def torch_cuda_status() -> str:
    """Does not import torch as a side effect on a box that has not installed it -
    that would be a silent dependency this script does not declare."""
    try:
        import torch
    except ImportError:
        return "torch not installed"
    try:
        if torch.cuda.is_available():
            return f"torch {torch.__version__}, CUDA {torch.version.cuda}, {torch.cuda.get_device_name(0)}"
        return f"torch {torch.__version__}, CUDA not available"
    except Exception as exc:  # noqa: BLE001 - diagnostic script, report and move on
        return f"torch present, query failed: {exc}"


def suggest_role(gpus: list[dict[str, str]], ram_gb: object) -> str:
    """Advisory only. Devices.md is the source of truth for which box is which -
    this is a sanity check a teammate can compare against it, not an assignment."""
    if not gpus:
        return "BENCH-like (no usable NVIDIA GPU detected)"
    try:
        mem_gb = float(gpus[0]["memory"].replace("MiB", "").strip()) / 1024
    except (ValueError, KeyError):
        return "GPU present, could not read VRAM - compare manually against Devices.md"
    if mem_gb >= 14:
        return f"LLM-like ({gpus[0]['name']}, ~{mem_gb:.0f} GB VRAM)"
    if mem_gb >= 6:
        return f"EMBED-like ({gpus[0]['name']}, ~{mem_gb:.0f} GB VRAM)"
    return f"GPU present but small ({gpus[0]['name']}, ~{mem_gb:.0f} GB) - likely BENCH-like in practice"


# ---------------------------------------------------------------------------
# Local additions - things this script creates. Idempotent: re-running never
# duplicates a directory or re-logs a no-op.
# ---------------------------------------------------------------------------

DIRS_NEEDED = [
    "artifacts", "artifacts/raw", "artifacts/onnx", "artifacts/indexes",
    "artifacts/propositions", "bench/results",
]


def ensure_directories() -> list[str]:
    created = []
    for rel in DIRS_NEEDED:
        p = REPO_ROOT / rel
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            created.append(rel)
    return created


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------


def write_info(
    cpu: dict[str, object],
    ram: dict[str, object],
    gpus: list[dict[str, str]],
    disk_gb: float,
    torch_status: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    gpu_block = (
        "\n".join(
            f"| {g['name']} | {g['memory']} | driver {g['driver']} | compute {g['compute_capability']} |"
            for g in gpus
        )
        if gpus
        else "| none detected | - | - | - |"
    )
    role = suggest_role(gpus, ram.get("total_gb"))

    content = f"""# LOCAL_SYSTEM_INFO.md

**This file is machine-generated and gitignored.** It describes only the machine it
was run on. Regenerate with `python scripts/00_detect_system.py` — it overwrites
this file every run, so it is always current for THIS box and never a stale copy
of someone else's.

Generated {now}.

## Specs

| | |
|---|---|
| Hostname | {platform.node()} |
| OS | {platform.system()} {platform.release()} ({platform.version()}) |
| Python | {sys.version.split()[0]} at `{sys.executable}` |
| CPU | {cpu['model']} |
| Cores | {cpu['physical_cores']} physical / {cpu['logical_cores']} logical |
| RAM | {ram['total_gb']} GB |
| Disk free (repo root) | {disk_gb} GB |
| Torch / CUDA | {torch_status} |

## GPU(s)

| Name | VRAM | Driver | Compute capability |
|---|---|---|---|
{gpu_block}

## Suggested role: {role}

**Advisory only.** [`Devices.md`](Devices.md) is the authority on which box is
BENCH, EMBED or LLM — this is a sanity check to compare against it, not an
assignment. If they disagree, trust `Devices.md` and say so in the team channel;
a machine that does not match its documented role invalidates comparisons across
boxes ([`ISSUES.md`](ISSUES.md) I13, I14).

**Do not hand-tune `ONNX_THREADS_BUILD` or `ONNX_THREADS_SERVING` from the numbers
above.** Thread counts must be measured against the real workload on this specific
box, never assumed from core count and never copied from another machine's
measurement — `ISSUES.md` I6 records a synthetic benchmark that gave a directionally
wrong answer for exactly this reason.
"""
    INFO_PATH.write_text(content, encoding="utf-8")


def log_additions(created_dirs: list[str]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    header = f"## Run at {now}\n\n"
    if created_dirs:
        body = "Created directories (did not exist):\n" + "".join(f"- `{d}/`\n" for d in created_dirs)
    else:
        body = "Nothing to create — all expected directories already existed.\n"
    entry = header + body + "\n---\n\n"

    if not ADDITIONS_PATH.exists():
        preamble = (
            "# LOCAL_SYSTEM_ADDITIONS.md\n\n"
            "**This file is machine-generated and gitignored.** It is an append-only "
            "log of what `scripts/00_detect_system.py` has changed on THIS machine. "
            "It never edits tracked repo files, never touches git config, never "
            "installs packages — it only creates the local directories every script "
            "in this repo expects to exist. Newest entry at the bottom.\n\n---\n\n"
        )
        ADDITIONS_PATH.write_text(preamble + entry, encoding="utf-8")
    else:
        with ADDITIONS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(entry)


def main() -> int:
    print("")
    print("  detecting this machine...")

    cpu = cpu_info()
    ram = ram_info()
    gpus = gpu_info()
    disk_gb = disk_free_gb()
    torch_status = torch_cuda_status()
    created_dirs = ensure_directories()

    write_info(cpu, ram, gpus, disk_gb, torch_status)
    log_additions(created_dirs)

    print(f"  CPU        {cpu['model']}  ({cpu['physical_cores']}p/{cpu['logical_cores']}l)")
    print(f"  RAM        {ram['total_gb']} GB")
    print(f"  GPU        {gpus[0]['name'] if gpus else 'none detected'}")
    print(f"  disk free  {disk_gb} GB")
    print(f"  torch      {torch_status}")
    if created_dirs:
        print(f"  created    {', '.join(created_dirs)}")
    else:
        print("  created    (nothing — directories already existed)")
    print("")
    print(f"  wrote {INFO_PATH.name} and appended to {ADDITIONS_PATH.name}")
    print("  Compare the suggested role against Devices.md before starting work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
