"""Ownership-aware lifecycle helpers for the standalone file server.

The file server intentionally has its own session.  That keeps terminal
signals away from it, but means a SIGKILLed node can leave it behind.  The
pidfile is therefore an ownership record, not merely an optimisation.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import time
from pathlib import Path
from typing import Any

from hololab.paths import node_data_dir


class FileServerPortInUse(RuntimeError):
    """The requested listener belongs to something we must not kill."""


def pidfile_path(port: int) -> Path:
    """Return the per-HOLOLAB_HOME, per-port ownership record path."""

    return node_data_dir() / f"file-server-{port}.pid"


def _start_ticks(pid: int) -> int | None:
    """Linux process start ticks, used to reject a recycled PID."""

    try:
        # ``comm`` may contain spaces or ')', hence split after its final ')'.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return int(fields[19])  # field 22, after pid + comm
    except (FileNotFoundError, IndexError, ValueError):
        return None


def _cmdline(pid: int) -> list[str] | None:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0")[:-1]
    except FileNotFoundError:
        return None


def _is_alive(pid: int) -> bool:
    """Treat a zombie as exited even while its original parent reaps it."""

    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        return state != "Z"
    except (FileNotFoundError, IndexError):
        return False


def _arg(argv: list[str], name: str) -> str | None:
    try:
        return argv[argv.index(name) + 1]
    except (ValueError, IndexError):
        return None


def _is_fileserver(argv: list[str] | None, port: int) -> bool:
    return bool(
        argv
        and "hololab.node.fileserver_main" in argv
        and _arg(argv, "--port") == str(port)
    )


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def write_pidfile(path: Path, *, port: int, host: str, workspace_root: Path) -> None:
    """Atomically record this file-server process' identity."""

    path.parent.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    record = {
        "pid": pid,
        "port": port,
        "host": host,
        "workspace_root": str(workspace_root.resolve()),
        "started_at": time.time(),
        "start_ticks": _start_ticks(pid),
    }
    temporary = path.with_name(f".{path.name}.{pid}.tmp")
    temporary.write_text(json.dumps(record, sort_keys=True) + "\n")
    os.replace(temporary, path)


def remove_pidfile(path: Path, *, pid: int) -> None:
    """Remove the record only when it still belongs to ``pid``."""

    record = _read_record(path)
    if record is not None and record.get("pid") == pid:
        with contextlib.suppress(OSError):
            path.unlink()


def _terminate(pid: int, *, timeout: float = 6.0) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_alive(pid):
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _record_is_owner(record: dict[str, Any], *, port: int) -> bool:
    pid = record.get("pid")
    if not isinstance(pid, int) or record.get("port") != port:
        return False
    argv = _cmdline(pid)
    if not _is_fileserver(argv, port):
        return False
    ticks = record.get("start_ticks")
    return ticks is None or ticks == _start_ticks(pid)


def _legacy_owner_pid(port: int, workspace_root: Path) -> int | None:
    """Find pre-pidfile file servers only when their workspace matches ours."""

    try:
        proc_entries = list(Path("/proc").iterdir())
    except OSError:
        return None
    wanted_root = str(workspace_root.resolve())
    for entry in proc_entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        argv = _cmdline(pid)
        if _is_fileserver(argv, port) and _arg(argv, "--workspace-root") == wanted_root:
            return pid
    return None


def owned_file_server_pid(*, port: int, workspace_root: Path, pidfile: Path) -> int | None:
    """Return a verified owner PID without signalling it.

    This is intentionally exposed for the top-level CLI's one-release
    compatibility path: an old service has only this child pidfile, from
    which its direct ``hololab start`` ancestor can be safely identified.
    """
    record = _read_record(pidfile)
    if record is not None and _record_is_owner(record, port=port):
        return int(record["pid"])
    return _legacy_owner_pid(port, workspace_root)


def reclaim_file_server(*, port: int, workspace_root: Path, pidfile: Path) -> int | None:
    """Stop a prior owned server, or fail safely if the port is foreign.

    The legacy scan deliberately requires the same workspace root.  A random
    service, or another HoloLab instance configured for another workspace,
    is never killed merely for using the same port.
    """

    pid = owned_file_server_pid(port=port, workspace_root=workspace_root, pidfile=pidfile)
    if pid is not None:
        _terminate(pid)
        remove_pidfile(pidfile, pid=pid)
        return pid
    return None


def assert_port_available(*, host: str, port: int) -> None:
    """Fail before spawn rather than hiding EADDRINUSE in a watchdog warning."""

    try:
        with socket.create_connection((host, port), timeout=0.1):
            raise FileServerPortInUse(
                f"file server port {host}:{port} is in use by a process that is not "
                "the owned HoloLab file server; refusing to kill it"
            )
    except ConnectionRefusedError:
        return
    except OSError as exc:
        # A bind test works even when a listener only accepts IPv4/IPv6 on an
        # address family that connect() cannot choose.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
        except OSError as bind_exc:
            raise FileServerPortInUse(
                f"file server port {host}:{port} is already in use; refusing to kill "
                f"an unverified owner ({bind_exc})"
            ) from exc


def stop_owned_file_server(*, port: int, workspace_root: Path, pidfile: Path) -> bool:
    """Official stop path; returns whether a known server was stopped."""

    return reclaim_file_server(port=port, workspace_root=workspace_root, pidfile=pidfile) is not None
