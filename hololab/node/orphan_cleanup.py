"""Kill leftover subprocess trees that outlived a previous daemon session.

When the node daemon dies (crash, ``systemctl restart``, SIGKILL, etc.),
subprocesses it had spawned via ``start_new_session=True`` become
orphans: reparented to init/systemd, still running, still holding CUDA
context / disk locks / etc. On the next daemon startup they'll leak
resources and race with newly-dispatched shards writing to the same
paths (that was the "重启后手动 pkill -f colmap" pain the operator hit
after a cancel storm).

We can't just ``pkill -f colmap``: that would target far more than our
workspace. Instead we scope by ``/proc/{pid}/cwd`` — a process whose
current working directory is inside our workspace root is definitely
ours (job working dirs live at ``workspace_root/w/<wf>/j/<job>/``). This
avoids collateral damage on shared boxes where other tools run under
their own working dirs.

Non-Linux platforms have no ``/proc``; we no-op there. macOS/BSD gets
best-effort ``ps``-based cleanup in a follow-up if it ever becomes a
real need — right now the daemon is Linux-only in practice.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import time
from collections.abc import Iterable
from pathlib import Path

from hololab.logging import get_logger

log = get_logger("node.orphan-cleanup")

# SIGTERM → grace → SIGKILL window. Short (compared to the executor's
# 30 s runtime cancel grace) because these are leftover processes with
# no live daemon to negotiate with — we just want them gone before the
# new daemon starts accepting jobs.
_TERM_TO_KILL_GRACE_S = 3.0


def cleanup_workspace_orphans(
    workspace_roots: Iterable[Path | str],
    *,
    our_pid: int | None = None,
) -> list[int]:
    """SIGTERM every non-self process whose cwd is inside ``workspace_roots``.

    Returns the list of PIDs signalled (useful for tests + startup log).
    A short grace period is followed by SIGKILL for stragglers. Best
    effort: permission errors, races (process exited between listing and
    signalling), or non-Linux hosts are logged at debug and skipped.
    """

    if sys.platform != "linux":
        return []

    resolved_roots = _resolved_roots(workspace_roots)
    if not resolved_roots:
        return []

    self_pid = our_pid if our_pid is not None else os.getpid()

    victims: list[int] = []
    for pid in _iter_proc_pids():
        if pid == self_pid:
            continue
        cwd = _read_proc_cwd(pid)
        if cwd is None:
            continue
        if not _path_under_any(cwd, resolved_roots):
            continue
        # Signal the whole process group — the direct pid is usually a
        # ``conda run`` / ``bash`` shell; its children (colmap etc.) are
        # in the same pgroup thanks to the executor's ``start_new_session``.
        pgid = _safe_getpgid(pid)
        target = pgid if pgid is not None else pid
        try:
            os.killpg(target, signal.SIGTERM) if pgid is not None else os.kill(pid, signal.SIGTERM)
            victims.append(pid)
            log.info(
                "orphan cleanup: SIGTERM",
                pid=pid,
                pgid=pgid,
                cwd=str(cwd),
            )
        except (ProcessLookupError, PermissionError) as exc:
            log.debug("orphan cleanup skip", pid=pid, error=str(exc))

    if not victims:
        return []

    # Grace period, then SIGKILL anything still alive. We poll rather
    # than blocking-wait because these aren't our children — os.wait
    # can't reap init's children.
    deadline = time.monotonic() + _TERM_TO_KILL_GRACE_S
    while time.monotonic() < deadline:
        if not any(_pid_alive(pid) for pid in victims):
            break
        time.sleep(0.1)

    for pid in victims:
        if not _pid_alive(pid):
            continue
        pgid = _safe_getpgid(pid)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            if pgid is not None:
                os.killpg(pgid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
            log.info("orphan cleanup: SIGKILL escalation", pid=pid, pgid=pgid)

    return victims


def _resolved_roots(roots: Iterable[Path | str]) -> list[Path]:
    out: list[Path] = []
    for r in roots:
        try:
            p = Path(r).resolve()
        except OSError:
            continue
        # Refuse dangerously-broad roots ("/", "/root", etc.) — a bug
        # elsewhere that let workspace_root default to "/" must not
        # translate into "kill every process on the box".
        if p == Path("/") or len(p.parts) < 3:
            log.warning("orphan cleanup: refusing overly-broad workspace root", root=str(p))
            continue
        out.append(p)
    return out


def _iter_proc_pids() -> Iterable[int]:
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    return (int(e) for e in entries if e.isdigit())


def _read_proc_cwd(pid: int) -> Path | None:
    try:
        target = os.readlink(f"/proc/{pid}/cwd")
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None
    try:
        return Path(target).resolve()
    except OSError:
        return None


def _path_under_any(path: Path, roots: Iterable[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _safe_getpgid(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
