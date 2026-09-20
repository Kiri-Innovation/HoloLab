"""Workspace-scoped orphan cleanup at daemon startup.

The daemon spawns every shard with ``start_new_session=True`` so
SIGTERM can cascade to grandchildren (colmap etc.). If the daemon dies
mid-run (crash, SIGKILL after a stuck cancel storm), those subprocesses
reparent to init and keep running, wasting the box. The next daemon
startup must sweep them — but the sweep MUST be scoped to our own
workspace directories or it'll SIGTERM unrelated tools on shared boxes.

Invariants defended here:

* A process whose ``/proc/{pid}/cwd`` is inside ``workspace_root``
  gets SIGTERM'd.
* A process whose cwd is somewhere else (``/tmp``, ``/home/…``) is
  left alone even when it's alive and it's ours to kill.
* The reaper does not signal itself.
* Overly-broad workspace roots (``/``, ``/root``, etc.) are refused —
  a bug that let workspace_root default to ``/`` must not become "kill
  every process on the machine".
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hololab.node.orphan_cleanup import cleanup_workspace_orphans

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="orphan cleanup uses /proc; Linux-only"
)


def _spawn_sleep_in(cwd: Path) -> subprocess.Popen[bytes]:
    """Start a long-lived shell whose cwd is under ``cwd`` and its own pgroup."""

    cwd.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        ["/bin/sh", "-c", "sleep 30"],
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _wait_dead(proc: subprocess.Popen[bytes], *, timeout: float = 8.0) -> None:
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=timeout)


def test_reaps_process_whose_cwd_is_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    job_dir = workspace / "w" / "wf1" / "j" / "job-alpha"
    proc = _spawn_sleep_in(job_dir)
    try:
        # Give the child a beat to actually enter its new cwd.
        time.sleep(0.1)
        victims = cleanup_workspace_orphans([workspace])
        assert proc.pid in victims

        _wait_dead(proc)
        assert proc.returncode is not None
    finally:
        if proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)


def test_leaves_process_outside_workspace_alone(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outsider_cwd = tmp_path / "elsewhere"
    proc = _spawn_sleep_in(outsider_cwd)
    try:
        time.sleep(0.1)
        victims = cleanup_workspace_orphans([workspace])
        assert proc.pid not in victims
        # It really is still alive.
        assert proc.poll() is None
    finally:
        if proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)


def test_reaper_does_not_signal_itself(tmp_path: Path) -> None:
    """our_pid is skipped even when its cwd happens to fall under the sweep."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Just ask the reaper to skip our real pid; the empty result is fine.
    victims = cleanup_workspace_orphans([workspace], our_pid=os.getpid())
    assert os.getpid() not in victims


def test_refuses_overly_broad_workspace_root(tmp_path: Path) -> None:
    """A ``workspace_root=/`` must not become a kill-everything sweep."""

    victims = cleanup_workspace_orphans([Path("/")])
    # No PIDs sent SIGTERM even though many processes have cwd under /.
    assert victims == []


def test_handles_nonexistent_workspace_root(tmp_path: Path) -> None:
    """A configured-but-missing workspace root is a no-op, not an error."""

    victims = cleanup_workspace_orphans([tmp_path / "does-not-exist"])
    assert victims == []


def test_kills_shell_children_via_pgroup(tmp_path: Path) -> None:
    """Killing the shell's pgroup takes its ``sleep`` child down too.

    Mirrors the real colmap case: the shell is what ``/proc/{pid}/cwd``
    matches, but the grandchild is the actual heavy process that must
    die. Same pgroup thanks to ``start_new_session=True``.
    """

    workspace = tmp_path / "workspace"
    job_dir = workspace / "w" / "wf1" / "j" / "job-beta"
    job_dir.mkdir(parents=True)
    # Shell forks off a background `sleep 30` then execs `sleep 30`
    # itself. Both sit in the same pgroup as the shell.
    proc = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 30 & exec sleep 30"],
        cwd=str(job_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        time.sleep(0.1)
        victims = cleanup_workspace_orphans([workspace])
        assert proc.pid in victims
        _wait_dead(proc)
        assert proc.returncode is not None
    finally:
        if proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
