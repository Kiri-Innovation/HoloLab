"""Ownership records for the one-command HoloLab service.

The file server deliberately has a separate session, so a service record is
needed instead of process-group signalling.  Every PID is verified against
both its Linux start time and its complete command line before it is touched.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import signal
import time
from pathlib import Path
from typing import Any

from hololab.paths import node_data_dir


def service_record_path() -> Path:
    return node_data_dir() / "service.json"


def _ticks(pid: int) -> int | None:
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
    except (FileNotFoundError, IndexError, ValueError):
        return None


def _argv(pid: int) -> list[str] | None:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0")[:-1]
    except FileNotFoundError:
        return None


def _alive(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, IndexError):
        return False


def _identity(pid: int) -> dict[str, Any] | None:
    argv = _argv(pid)
    ticks = _ticks(pid)
    if not argv or ticks is None:
        return None
    return {"pid": pid, "start_ticks": ticks, "argv_sha256": hashlib.sha256(
        "\0".join(argv).encode()).hexdigest(), "argv": argv}


def _matches(identity: Any) -> bool:
    if not isinstance(identity, dict) or not isinstance(identity.get("pid"), int):
        return False
    pid = identity["pid"]
    current = _identity(pid)
    return bool(current and _alive(pid) and current["start_ticks"] == identity.get("start_ticks")
                and current["argv_sha256"] == identity.get("argv_sha256"))


def _read(path: Path | None = None) -> dict[str, Any] | None:
    try:
        value = json.loads((path or service_record_path()).read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _write(value: dict[str, Any], path: Path | None = None) -> None:
    target = path or service_record_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n")
    os.replace(temporary, target)


def _remove_if_ours(owner_pid: int, path: Path | None = None) -> None:
    target = path or service_record_path()
    record = _read(target)
    if record and record.get("parent", {}).get("pid") == owner_pid:
        with contextlib.suppress(OSError):
            target.unlink()


def _terminate(identity: Any, *, timeout: float = 6.0) -> bool:
    """Terminate a verified process; false means ownership was uncertain."""
    if not _matches(identity):
        return False
    pid = identity["pid"]
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    # Re-check immediately before escalation: no recycled PID may be hit.
    if _matches(identity):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
    return True


class ServiceAlreadyRunning(RuntimeError):
    pass


def begin_service(*, gateway_port: int, file_server_port: int) -> None:
    """Register this process and recover only a demonstrably stale record."""
    path = service_record_path()
    old = _read(path)
    if old:
        if _matches(old.get("parent")):
            raise ServiceAlreadyRunning("an owned HoloLab service is already running")
        # A SIGKILL cannot run the parent's finally block.  Its independently
        # sessioned children are nevertheless still exactly identified here.
        for child in old.get("children", []):
            _terminate(child)
        with contextlib.suppress(OSError):
            path.unlink()
    parent = _identity(os.getpid())
    if parent is None:  # pragma: no cover - Linux /proc is required in production
        raise RuntimeError("cannot establish HoloLab process identity")
    _write({"version": 1, "started_at": time.time(), "gateway_port": gateway_port,
            "file_server_port": file_server_port, "parent": parent, "children": []})


def register_child(pid: int) -> None:
    """Add a child spawned by the currently registered service."""
    record = _read()
    if not record or record.get("parent", {}).get("pid") != os.getpid():
        return
    identity = _identity(pid)
    if identity is None:
        return
    record["children"] = [child for child in record.get("children", [])
                          if child.get("pid") != pid] + [identity]
    _write(record)


def unregister_child(pid: int) -> None:
    record = _read()
    if not record or record.get("parent", {}).get("pid") != os.getpid():
        return
    record["children"] = [child for child in record.get("children", [])
                          if child.get("pid") != pid]
    _write(record)


def end_service() -> None:
    _remove_if_ours(os.getpid())


def stop_service() -> tuple[list[int], list[int]]:
    """Stop the recorded parent and children, returning (stopped, skipped)."""
    path = service_record_path()
    record = _read(path)
    if not record:
        return [], []
    stopped: list[int] = []
    skipped: list[int] = []
    # Ask the parent first: its normal signal/finally path is the graceful
    # shutdown path.  Then clean surviving independent children as recovery.
    parent = record.get("parent")
    if _matches(parent):
        if _terminate(parent):
            stopped.append(parent["pid"])
    elif (isinstance(parent, dict) and isinstance(parent.get("pid"), int)
          and _alive(parent["pid"])):
        skipped.append(parent["pid"])
    for child in record.get("children", []):
        if _matches(child):
            if _terminate(child):
                stopped.append(child["pid"])
        elif isinstance(child, dict) and isinstance(child.get("pid"), int) and _alive(child["pid"]):
            skipped.append(child["pid"])
    with contextlib.suppress(OSError):
        path.unlink()
    return stopped, skipped
