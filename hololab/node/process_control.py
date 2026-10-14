"""Confirmed, narrowly scoped termination of external resource-lens processes."""

from __future__ import annotations

import contextlib
import ctypes
import os
import platform
import secrets
import select
import signal
import threading
import time
from pathlib import Path
from typing import Any

from hololab.node import resource_lens as lens

_CONFIRMATIONS: dict[str, tuple[int, int, str, float, str]] = {}
_LOCK = threading.Lock()


# Exact executable / comm / argv[0] names. Prefixes below are explicit families,
# not regexes. Do not block every child of systemd/sshd (that would block users).
SYSTEM_PROCESS_NAMES = frozenset(
    {
        "init",
        "tini",
        "kthreadd",
        "systemd",
        "systemd-journald",
        "systemd-logind",
        "systemd-udevd",
        "systemd-networkd",
        "systemd-resolved",
        "sshd",
        "docker",
        "dockerd",
        "containerd",
        "containerd-shim",
        "nginx",
        "flops-core",
    }
)
SYSTEM_PROCESS_PREFIXES = ("flops-headless", "containerd-shim-")


def critical_name(name: str) -> bool:
    return name in SYSTEM_PROCESS_NAMES or any(name.startswith(p) for p in SYSTEM_PROCESS_PREFIXES)


def system_process_reason(pid: int, proc_root: Path = Path("/proc")) -> str | None:
    if pid <= 300:
        return "系统关键进程，禁止终止：system PID (≤ 300)"  # noqa: RUF001
    proc = proc_root / str(pid)
    tail = (proc / "stat").read_text().rsplit(")", 1)[1].split()
    argv = (proc / "cmdline").read_bytes().split(b"\0")
    if int(tail[6]) & 0x00200000:  # Linux PF_KTHREAD; display brackets are not authoritative.
        return "系统关键进程，禁止终止：内核线程"  # noqa: RUF001
    names = {
        (proc / "comm").read_text().strip(),
        Path(os.readlink(proc / "exe")).name,
        Path(argv[0].decode(errors="replace")).name,
    }
    for name in sorted(names):
        if critical_name(name):
            return f"系统关键进程，禁止终止：{name}"  # noqa: RUF001
    if not argv[0]:
        return "无法读取完整命令行，禁止终止"  # noqa: RUF001
    return None


def termination_offer(
    pid: int, cmdline: str | None, attribution: dict[str, Any], metric: str
) -> dict[str, Any]:
    reason = None
    token = None
    try:
        if attribution.get("owner") == "job":
            reason = "HoloLab 任务，请使用停止任务"  # noqa: RUF001
        elif attribution.get("owner") != "external":
            reason = "归属无法核实，禁止终止"  # noqa: RUF001
        else:
            reason = system_process_reason(pid)
            if not reason and protected_process(pid, {}):
                reason = "HoloLab 进程树，禁止终止"  # noqa: RUF001
            if not reason:
                token = issue_confirmation(pid, cmdline, metric)
                if not token:
                    reason = "进程信息已变化或命令行不可读，请刷新"  # noqa: RUF001
    except (OSError, ValueError, IndexError):
        reason = "进程信息不可读或进程已退出，禁止终止"  # noqa: RUF001
    return {"termination_token": token, "termination_reason": reason}


def _pidfd_syscall(number: int, *args: int) -> int:
    # Some standalone Python builds omit pidfd wrappers despite kernel support.
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "aarch64"}:
        raise OSError("Safe termination requires Linux pidfd support")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    result = libc.syscall(ctypes.c_long(number), *(ctypes.c_long(a) for a in args))
    if result < 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))
    return int(result)


def open_pidfd(pid: int) -> int:
    return os.pidfd_open(pid) if hasattr(os, "pidfd_open") else _pidfd_syscall(434, pid, 0)


def send_pidfd(fd: int, sig: int) -> None:
    if hasattr(signal, "pidfd_send_signal"):
        signal.pidfd_send_signal(fd, sig)
    else:
        _pidfd_syscall(424, fd, sig, 0, 0)


def issue_confirmation(pid: int, cmdline: str | None, metric: str = "vram") -> str | None:
    if pid <= 300 or not cmdline or metric not in ("vram", "gpu", "cpu", "mem"):
        return None
    try:
        born = lens.process_identity(pid)[1]
        if read_cmdline(pid) != cmdline:
            return None
    except (OSError, ValueError, IndexError):
        return None
    with _LOCK:
        now = time.monotonic()
        for token, value in list(_CONFIRMATIONS.items()):
            if value[3] < now:
                _CONFIRMATIONS.pop(token, None)
        if len(_CONFIRMATIONS) >= 1024:
            _CONFIRMATIONS.pop(next(iter(_CONFIRMATIONS)))
        token = secrets.token_urlsafe(24)
        _CONFIRMATIONS[token] = (pid, born, cmdline, now + 300, metric)
    return token


def read_cmdline(pid: int) -> str:
    return (
        Path(f"/proc/{pid}/cmdline")
        .read_bytes()
        .rstrip(b"\0")
        .replace(b"\0", b" ")
        .decode(errors="replace")
    )


def local_identity() -> dict[str, Any]:
    try:
        return {
            "pid": os.getpid(),
            "start_ticks": lens.process_identity(os.getpid())[1],
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        }
    except (OSError, ValueError, IndexError):
        return {}


def ancestors(pid: int) -> list[int]:
    result: list[int] = []
    while pid > 1 and pid not in result:
        result.append(pid)
        pid = lens.process_identity(pid)[0]
    return result


def protected_process(pid: int, gateway: dict[str, Any]) -> bool:
    # Protect this runtime, its ancestors, and all of its descendants.
    if pid in ancestors(os.getpid()):
        return True
    roots = {os.getpid()}
    identity = local_identity()
    if gateway.get("boot_id") and gateway.get("boot_id") == identity.get("boot_id"):
        gp = int(gateway["pid"])
        with contextlib.suppress(ProcessLookupError, FileNotFoundError):
            if lens.process_identity(gp)[1] == gateway.get("start_ticks"):
                roots.add(gp)
    for ancestor in ancestors(pid):
        if ancestor in roots:
            return True
        argv = Path(f"/proc/{ancestor}/cmdline").read_bytes().decode(errors="replace").split("\0")
        # Also cover separate CLI runtimes, uvicorn workers and fileservers.
        if any(
            arg == "hololab" or arg.startswith("hololab.") or Path(arg).name == "hololab"
            for arg in argv
            if arg
        ):
            return True
    return False


def terminate_external(
    pid: int, token: str, gateway: dict[str, Any] | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {"pid": pid, "status": "failed", "reason": None, "cmdline": None}

    def refuse(reason: str) -> dict[str, Any]:
        result["reason"] = reason
        return result

    if pid <= 300:
        return refuse("Refused: system PID (≤ 300)")
    try:
        # Independent hard checks even if callers forge or omit a confirmation.
        attribution = lens.owner(pid, dict(lens.ACTIVE))
        if attribution["owner"] == "job":
            return refuse("Refused: HoloLab Job process; use Stop task")
        if protected_process(pid, gateway or {}):
            return refuse("Refused: HoloLab process tree")
        reason = system_process_reason(pid)
        if reason:
            return refuse(reason)
        if attribution["owner"] != "external":
            return refuse("Refused: process ownership cannot be verified")
        with _LOCK:
            confirmed = _CONFIRMATIONS.pop(token, None)
        if not confirmed or confirmed[0] != pid or confirmed[3] < time.monotonic():
            return refuse("Confirmation expired or invalid; refresh the lens and confirm again")
        result["cmdline"] = confirmed[2]
        fd = open_pidfd(pid)
        try:
            poll = select.poll()
            poll.register(fd, select.POLLIN)
            if poll.poll(0):
                return refuse("Process no longer exists")
            if lens.process_identity(pid)[1] != confirmed[1] or read_cmdline(pid) != confirmed[2]:
                return refuse("Process changed since confirmation; refresh the lens")
            metric = confirmed[4]
            if metric in ("gpu", "vram"):
                rows = lens.query("gpu_uuid,pid,process_name,used_gpu_memory", "compute-apps")
                if not any(len(row) == 4 and row[1].strip() == str(pid) for row in rows):
                    return refuse("Refused: PID is not in the current GPU process list")
            elif metric in ("cpu", "mem"):
                current = lens.read_host_processes().get(pid)
                if not current or current["born"] != confirmed[1]:
                    return refuse("Refused: PID is no longer in the current host process list")
            else:
                return refuse("Refused: invalid confirmation scope")
            # Recheck after the driver query; use pidfd, never a recycled numeric PID.
            if lens.owner(pid, dict(lens.ACTIVE))["owner"] != "external" or protected_process(
                pid, gateway or {}
            ):
                return refuse("Refused: process now belongs to HoloLab or ownership is unknown")
            if lens.process_identity(pid)[1] != confirmed[1] or read_cmdline(pid) != confirmed[2]:
                return refuse("Process changed since confirmation; refresh the lens")
            reason = system_process_reason(pid)
            if reason:
                return refuse(reason)
            send_pidfd(fd, signal.SIGTERM)
            if poll.poll(3000):
                result["status"] = "terminated"
            else:
                reason = system_process_reason(pid)
                if reason:
                    return refuse(f"SIGTERM sent; escalation refused: {reason}")
                if lens.owner(pid, dict(lens.ACTIVE))["owner"] != "external" or protected_process(
                    pid, gateway or {}
                ):
                    return refuse(
                        "SIGTERM sent; escalation refused: process now belongs to HoloLab"
                    )
                send_pidfd(fd, signal.SIGKILL)
                if poll.poll(2000):
                    result["status"] = "force-killed"
                else:
                    return refuse("SIGKILL sent, but process exit is not yet confirmed")
            return result
        finally:
            os.close(fd)
    except (ProcessLookupError, FileNotFoundError):
        return refuse("Process no longer exists")
    except PermissionError:
        return refuse("Permission denied while inspecting or terminating process")
    except Exception as exc:
        return refuse(f"Termination failed ({type(exc).__name__}); no automatic retry")
