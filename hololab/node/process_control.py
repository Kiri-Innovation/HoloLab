"""Confirmed, narrowly scoped termination of external GPU processes."""

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

_CONFIRMATIONS: dict[str, tuple[int, int, str, float]] = {}
_LOCK = threading.Lock()


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


def issue_confirmation(pid: int, cmdline: str | None) -> str | None:
    if pid <= 300 or not cmdline:
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
        _CONFIRMATIONS[token] = (pid, born, cmdline, now + 300)
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
            rows = lens.query("gpu_uuid,pid,process_name,used_gpu_memory", "compute-apps")
            if not any(len(row) == 4 and row[1].strip() == str(pid) for row in rows):
                return refuse("Refused: PID is not in the current GPU process list")
            # Recheck after the driver query; use pidfd, never a recycled numeric PID.
            if lens.owner(pid, dict(lens.ACTIVE))["owner"] != "external" or protected_process(
                pid, gateway or {}
            ):
                return refuse("Refused: process now belongs to HoloLab or ownership is unknown")
            if lens.process_identity(pid)[1] != confirmed[1] or read_cmdline(pid) != confirmed[2]:
                return refuse("Process changed since confirmation; refresh the lens")
            send_pidfd(fd, signal.SIGTERM)
            if poll.poll(3000):
                result["status"] = "terminated"
            else:
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
