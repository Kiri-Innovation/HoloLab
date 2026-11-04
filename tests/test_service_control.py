"""Crash-safe ownership tests for the top-level ``hololab start`` service."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from hololab import service_control


def _child() -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)


def _gone(proc: subprocess.Popen[bytes]) -> bool:
    return proc.poll() is not None


def test_stop_service_reaps_registered_child_after_parent_crash(
    tmp_path: Path, monkeypatch
) -> None:
    """The persisted child identity survives the main process's SIGKILL."""
    monkeypatch.setenv("HOLOLAB_HOME", str(tmp_path / "home"))
    child = _child()
    try:
        identity = service_control._identity(child.pid)
        assert identity is not None
        # A non-existent parent models the killed service process.  ``stop``
        # must not need to signal it in order to clean its detached child.
        service_control._write({"parent": {"pid": 999_999_999}, "children": [identity]})
        stopped, skipped = service_control.stop_service()
        assert child.pid in stopped
        assert not skipped
        child.wait(timeout=3)
        assert _gone(child)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_stop_service_skips_recycled_or_unverified_pid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOLOLAB_HOME", str(tmp_path / "home"))
    child = _child()
    try:
        identity = service_control._identity(child.pid)
        assert identity is not None
        identity["argv_sha256"] = "not-the-command"
        service_control._write({"parent": {"pid": 999_999_999}, "children": [identity]})
        stopped, skipped = service_control.stop_service()
        assert not stopped
        assert skipped == [child.pid]
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()


def test_begin_service_reaps_stale_registered_child(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOLOLAB_HOME", str(tmp_path / "home"))
    child = _child()
    try:
        identity = service_control._identity(child.pid)
        assert identity is not None
        service_control._write({"parent": {"pid": 999_999_999}, "children": [identity]})
        service_control.begin_service(gateway_port=18828, file_server_port=18829)
        child.wait(timeout=3)
        assert _gone(child)
    finally:
        service_control.end_service()
        if child.poll() is None:
            child.kill()
            child.wait()
