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
        from hololab.node.process_control import issue_confirmation

        detail_cache: dict[int, dict[str, Any]] = {}
        for row in rows:
            if len(row) != 4:
                raise ValueError("Invalid process response")
            uuid, raw_pid, name, memory = row
            pid = int(raw_pid)
            if pid not in detail_cache:
                detail_cache[pid] = process_details(pid)
            attribution = owner(pid, owners)
            token = (
                issue_confirmation(pid, detail_cache[pid]["cmdline"])
                if attribution["owner"] == "external"
                else None
            )
            result["processes"].append(
                {
                    "gpu_uuid": uuid.strip(),
                    "pid": pid,
                    "name": Path(name.strip()).name,
                    "used_mb": number(memory),
                    **attribution,
                    "termination_token": token,
                    **detail_cache[pid],
                }
            )
        result["processes"].sort(
            key=lambda p: p["used_mb"] if p["used_mb"] is not None else -1, reverse=True
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        result["error"] = f"GPU process accounting unavailable ({type(exc).__name__})"
    return result


def read_host_processes() -> dict[int, dict[str, Any]]:
    rows = {}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            stat = (proc / "stat").read_text()
            tail = stat.rsplit(")", 1)[1].split()
            rows[int(proc.name)] = {
                "pid": int(proc.name),
                "name": stat.split("(", 1)[1].rsplit(")", 1)[0],
                "born": int(tail[19]),
                "ticks": int(tail[11]) + int(tail[12]),
                "used_mb": int(tail[21]) * os.sysconf("SC_PAGE_SIZE") / 1024**2,
            }
        except (OSError, ValueError, IndexError):
            continue  # Exited or inaccessible; never invent zero usage.
    return rows


def read_host_cpu() -> tuple[int, int]:
    values = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
    return sum(values), values[3] + values[4]  # Exclude guest ticks already in user/nice.


def cpu_delta(first: dict[str, Any], second: dict[str, Any], total_ticks: int) -> float | None:
    if first["born"] != second["born"] or total_ticks <= 0:
        return None
    return max(0.0, min(100.0, 100 * (second["ticks"] - first["ticks"]) / total_ticks))


def sample_host(metric: str, active: dict[int, tuple[int, dict[str, str]]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "metric": metric,
        "ts": time.time(),
        "processes": [],
        "gpus": [],
        "error": None,
    }
    try:
        if metric == "cpu":
            first = read_host_processes()
            a = read_host_cpu()
            start = time.monotonic()
            time.sleep(0.3)
            second = read_host_processes()
            b = read_host_cpu()
            result["sample_window_s"] = time.monotonic() - start
            delta = b[0] - a[0]
            if delta <= 0:
                raise ValueError("CPU counter did not advance")
            result["total_percent"] = max(0.0, min(100.0, 100 * (1 - (b[1] - a[1]) / delta)))
            rows = []
            for pid, p in second.items():
                value = cpu_delta(first[pid], p, delta) if pid in first else None
                if value is not None:
                    rows.append({**p, "usage_percent": value})
        else:
            from hololab.node.metrics import sample_memory

            used, total = sample_memory()
            if used is None or total is None:
                raise ValueError("Memory counters unavailable")
            result.update(total_used_mb=used * 1024, total_capacity_mb=total * 1024)
            rows = list(read_host_processes().values())
        if not rows:
            raise ValueError("No readable process counters")
        field = "usage_percent" if metric == "cpu" else "used_mb"
        rows.sort(key=lambda p: p[field], reverse=True)
        for p in rows:
            pid = p["pid"]
            # Verify birth time before attaching metadata/attribution.
            try:
                if process_identity(pid)[1] != p["born"]:
                    continue
                details = process_details(pid)
                attribution = owner(pid, dict(active))
                if process_identity(pid)[1] != p["born"]:
                    continue
            except (OSError, ValueError, IndexError):
                continue
            result["processes"].append(
                {
                    "pid": pid,
                    "name": p["name"],
                    field: p[field],
                    **attribution,
                    **details,
                    "termination_token": None,
                }
            )
    except (OSError, ValueError, IndexError) as exc:
        result["error"] = f"{metric.upper()} process accounting unavailable ({type(exc).__name__})"
    result["ts"] = time.time()
    return result


def parse_pmon(text: str) -> list[dict[str, Any]]:
    columns: list[str] | None = None
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if parts[:2] == ["#", "gpu"]:
            columns = parts[1:]
            continue
        if not parts or parts[0] == "#":
            continue
        if columns is None or "pid" not in columns or "sm" not in columns:
            raise ValueError("Unrecognized pmon format")
        values = dict(zip(columns, parts, strict=False))
        if values.get("pid") == "-":
            continue
        rows.append(
            {
                "index": int(values["gpu"]),
                "pid": int(values["pid"]),
                "usage_percent": number(values.get("sm", "-")),
                "memory_activity_percent": number(values.get("mem", "-")),
            }
        )
    if columns is None:
        raise ValueError("Missing pmon header")
    return rows


def sample_resource(metric: str, active: dict[int, tuple[int, dict[str, str]]]) -> dict[str, Any]:
    if metric in ("cpu", "mem"):
        return sample_host(metric, active)
    if metric not in ("vram", "gpu"):
        return {
            "metric": metric,
            "error": "Unsupported resource metric",
            "processes": [],
            "gpus": [],
        }
    result = sample_vram(active)
    if metric == "vram":
        return result
    result["metric"] = "gpu"
    if result["error"]:
        return result
    try:
        totals = query("index,utilization.gpu", "gpu")
        for g in result["gpus"]:
            g["util_percent"] = next(
                (number(r[1]) for r in totals if int(r[0]) == g["index"]), None
            )
        probe = subprocess.run(
            ["nvidia-smi", "pmon", "-c", "1"], capture_output=True, text=True, check=True, timeout=3
        )
        values = {(p["index"], p["pid"]): p for p in parse_pmon(probe.stdout)}
        for p in result["processes"]:
            index = next(g["index"] for g in result["gpus"] if g["uuid"] == p["gpu_uuid"])
            p.update(
                {
                    k: v
                    for k, v in values.get((index, p["pid"]), {"usage_percent": None}).items()
                    if k not in ("pid", "index")
                }
            )
        result["processes"].sort(
            key=lambda p: p["usage_percent"] if p["usage_percent"] is not None else -1, reverse=True
        )
        if any(p["usage_percent"] is None for p in result["processes"]):
            result["error"] = (
                "pmon did not report SM utilization for one or more compute processes (unsupported or process exited); unknown values are shown as —."
            )
    except (
        OSError,
        subprocess.SubprocessError,
        ValueError,
        IndexError,
        KeyError,
        StopIteration,
    ) as exc:
        result["error"] = (
            f"GPU utilization unavailable: nvidia-smi pmon failed ({type(exc).__name__})"
        )
    return result
