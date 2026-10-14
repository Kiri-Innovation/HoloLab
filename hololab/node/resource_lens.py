"""On-demand GPU process accounting; never part of the periodic sampler."""

from __future__ import annotations

import contextlib
import csv
import io
import math
import subprocess
import time
from pathlib import Path
from typing import Any

# Only currently executing children. Birth ticks guard against PID reuse.
ACTIVE: dict[int, tuple[int, dict[str, str]]] = {}


def process_identity(pid: int) -> tuple[int, int]:
    tail = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return int(tail[1]), int(tail[19])  # parent PID, starttime


def register(pid: int, job: dict[str, str]) -> None:
    with contextlib.suppress(OSError, ValueError, IndexError):
        ACTIVE[pid] = (process_identity(pid)[1], job)


def owner(pid: int, active: dict[int, tuple[int, dict[str, str]]]) -> dict[str, Any]:
    seen: set[int] = set()
    try:
        while pid > 1 and pid not in seen:
            seen.add(pid)
            parent, born = process_identity(pid)
            if pid in active and active[pid][0] == born:
                return {"owner": "job", **active[pid][1]}
            pid = parent
        return {"owner": "external"}
    except (OSError, ValueError, IndexError):
        return {"owner": "unknown"}


def query(fields: str, kind: str) -> list[list[str]]:
    result = subprocess.run(
        ["nvidia-smi", f"--query-{kind}={fields}", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        timeout=2,
        check=True,
    )
    return list(csv.reader(io.StringIO(result.stdout), skipinitialspace=True))


def number(raw: str) -> float | None:
    try:
        n = float(raw)
        return n if math.isfinite(n) and n >= 0 else None
    except ValueError:
        return None


def sample_vram(active: dict[int, tuple[int, dict[str, str]]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ts": time.time(),
        "metric": "vram",
        "gpus": [],
        "processes": [],
        "error": None,
    }
    try:
        for row in query("index,uuid,name,memory.used,memory.total", "gpu"):
            if len(row) != 5:
                raise ValueError("Invalid GPU response")
            index, uuid, name, used, total = row
            result["gpus"].append(
                {
                    "index": int(index),
                    "uuid": uuid.strip(),
                    "name": name.strip(),
                    "mem_used_mb": number(used),
                    "mem_total_mb": number(total),
                }
            )
        rows = query("gpu_uuid,pid,process_name,used_gpu_memory", "compute-apps")
        owners = dict(active)
        for row in rows:
            if len(row) != 4:
                raise ValueError("Invalid process response")
            uuid, raw_pid, name, memory = row
            pid = int(raw_pid)
            result["processes"].append(
                {
                    "gpu_uuid": uuid.strip(),
                    "pid": pid,
                    "name": Path(name.strip()).name,
                    "used_mb": number(memory),
                    **owner(pid, owners),
                }
            )
        result["processes"].sort(
            key=lambda p: p["used_mb"] if p["used_mb"] is not None else -1, reverse=True
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        result["error"] = f"GPU process accounting unavailable ({type(exc).__name__})"
    return result
