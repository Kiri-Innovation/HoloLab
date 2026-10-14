"""On-demand GPU process accounting; never part of the periodic sampler."""

from __future__ import annotations

import contextlib
import csv
import io
import math
import os
import socket
import subprocess
import sys
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


def listening_ports(proc: Path) -> list[str] | None:
    """Match this PID's socket FDs to LISTEN rows in its network namespace."""
    try:
        inodes = set()
        for fd in (proc / "fd").iterdir():
            try:
                target = os.readlink(fd)
            except FileNotFoundError:  # FD closed during collection.
                continue
            if target.startswith("socket:["):
                inodes.add(target[8:-1])
        ports = set()
        for table, family in (("tcp", socket.AF_INET), ("tcp6", socket.AF_INET6)):
            for line in (proc / "net" / table).read_text().splitlines()[1:]:
                fields = line.split()
                if len(fields) < 10 or fields[3] != "0A" or fields[9] not in inodes:
                    continue
                address, port = fields[1].split(":")
                packed = b"".join(
                    int(address[i : i + 8], 16).to_bytes(4, sys.byteorder)
                    for i in range(0, len(address), 8)
                )
                host = socket.inet_ntop(family, packed)
                ports.add(
                    f"[{host}]:{int(port, 16)}"
                    if family == socket.AF_INET6
                    else f"{host}:{int(port, 16)}"
                )
        return sorted(ports)
    except (OSError, ValueError, OverflowError):
        return None


def process_details(pid: int, proc_root: Path = Path("/proc")) -> dict[str, Any]:
    """Best-effort fields; never substitute an empty value for a failed read."""
    empty = dict.fromkeys(
        ("cmdline", "cwd", "user", "started_ts", "elapsed_s", "ppid", "listen_ports")
    )
    details = empty.copy()
    proc = proc_root / str(pid)
    try:
        before = (proc / "stat").read_text().rsplit(")", 1)[1].split()
        born = int(before[19])
        details["ppid"] = int(before[1])
    except (OSError, ValueError, IndexError):
        return details
    with contextlib.suppress(OSError):
        details["cmdline"] = (proc / "cmdline").read_bytes().rstrip(b"\0").replace(
            b"\0", b" "
        ).decode(errors="replace") or None
    with contextlib.suppress(OSError):
        details["cwd"] = os.readlink(proc / "cwd")
    with contextlib.suppress(OSError, KeyError, ImportError):
        import pwd

        details["user"] = pwd.getpwuid(proc.stat().st_uid).pw_name
    with contextlib.suppress(OSError, ValueError, IndexError):
        age_at_boot = born / os.sysconf("SC_CLK_TCK")
        uptime = float((proc_root / "uptime").read_text().split()[0])
        details["elapsed_s"] = max(0, uptime - age_at_boot)
    with contextlib.suppress(OSError, ValueError, IndexError):
        for line in (proc_root / "stat").read_text().splitlines():
            if line.startswith("btime "):
                details["started_ts"] = int(line.split()[1]) + born / os.sysconf("SC_CLK_TCK")
                break
    details["listen_ports"] = listening_ports(proc)
    # A disappearing/reused PID must not mix two processes' metadata.
    try:
        after = (proc / "stat").read_text().rsplit(")", 1)[1].split()
        if int(after[19]) != born:
            return empty
    except (OSError, ValueError, IndexError):
        return empty
    return details


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
        detail_cache: dict[int, dict[str, Any]] = {}
        for row in rows:
            if len(row) != 4:
                raise ValueError("Invalid process response")
            uuid, raw_pid, name, memory = row
            pid = int(raw_pid)
            if pid not in detail_cache:
                detail_cache[pid] = process_details(pid)
            result["processes"].append(
                {
                    "gpu_uuid": uuid.strip(),
                    "pid": pid,
                    "name": Path(name.strip()).name,
                    "used_mb": number(memory),
                    **owner(pid, owners),
                    **detail_cache[pid],
                }
            )
        result["processes"].sort(
            key=lambda p: p["used_mb"] if p["used_mb"] is not None else -1, reverse=True
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        result["error"] = f"GPU process accounting unavailable ({type(exc).__name__})"
    return result
