"""Cancel-path regression tests for :mod:`hololab.node.executor`.

Two things must hold:

1. Cancel targets **only** the specific job's process group.
2. If the shell has already exited (and its PID might have been recycled
   into another job's pgroup) the cancel path refuses to signal — that's
   how job A's cancel used to murder job B's PID 317847.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
from unittest.mock import MagicMock

import pytest

from hololab.node.executor import _cancel_process, _pgid_still_ours

# ---------------------------------------------------------------------------
# _pgid_still_ours — the /proc-based verification helper
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only test")
def test_pgid_verified_for_own_child() -> None:
    """When we spawn a child with setsid, its own PID leads its own
    pgroup and it's parented by us — the verifier must say True."""

    proc = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 5"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        assert _pgid_still_ours(proc.pid, os.getpid()) is True
    finally:
        proc.kill()
        proc.wait(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only test")
def test_pgid_rejected_when_process_dead() -> None:
    """After the target exits and is reaped, the verifier must NOT
    confirm the pgid — otherwise a recycled PID would slip through."""

    proc = subprocess.Popen(
        ["/bin/sh", "-c", "true"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    proc.wait(timeout=5)
    # Whatever PID recycling has (or hasn't) done, that PID no longer
    # points to a process parented by us with our-owned pgroup.
    assert _pgid_still_ours(proc.pid, os.getpid()) is False


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only test")
def test_pgid_rejected_when_parent_mismatch() -> None:
    """A process parented by init (or any pid != ours) must be refused,
    even if it's currently the leader of its own pgroup."""

    proc = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 5"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        # Deliberately pass a wrong parent pid.
        assert _pgid_still_ours(proc.pid, our_pid=999_999) is False
    finally:
        proc.kill()
        proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# _cancel_process — end-to-end behaviour
# ---------------------------------------------------------------------------


class _FakeProc:
    """Enough of asyncio.subprocess.Process to exercise _cancel_process
    without spinning a real event loop for the recycled-PID scenario."""

    def __init__(self, *, pid: int, returncode: int | None) -> None:
        self.pid = pid
        self.returncode = returncode


def test_cancel_skipped_when_process_already_finished() -> None:
    """proc.returncode is not None → do not signal anything. Guards the
    "shell exited between the cancel snapshot and _cancel_process" window."""

    proc = _FakeProc(pid=999_999_999, returncode=0)
    # If _cancel_process tried to signal PID 999_999_999 we'd get a
    # ProcessLookupError bubble up in test flakes — but the function
    # should short-circuit on returncode instead.
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        _cancel_process(proc)  # type: ignore[arg-type]
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only test")
def test_cancel_kills_only_own_process_group() -> None:
    """Two independent setsid-spawned children. Cancelling one via
    _cancel_process must not kill the other."""

    victim = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 10"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    bystander = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 10"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        # Mimic the asyncio.subprocess.Process shape enough for cancel.
        fake = _FakeProc(pid=victim.pid, returncode=None)

        async def _run() -> None:
            await _cancel_process(fake)  # type: ignore[arg-type]
            # Grace timer is scheduled but we won't wait for it — SIGTERM
            # to the shell is enough to make it exit.
            await asyncio.sleep(0.3)

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_run())

        # Victim must be dead soon; bystander must still be alive.
        victim.wait(timeout=5)
        assert victim.returncode is not None
        assert bystander.poll() is None
    finally:
        for p in (victim, bystander):
            if p.poll() is None:
                p.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    p.wait(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only test")
def test_cancel_refuses_when_pid_no_longer_ours() -> None:
    """If ``proc.pid`` currently points at a process the verifier can't
    prove is ours (e.g. because the original was reaped + recycled),
    _cancel_process must NOT signal — that's the whole reason the fix
    exists. We simulate the recycled scenario by pointing at a live
    bystander that we didn't spawn as our child of the same test PID."""

    bystander = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 10"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        # Even though bystander IS our child, we pretend it isn't by
        # patching _pgid_still_ours to return False. That's the same
        # branch the recycled-PID scenario would hit at runtime.
        from hololab.node import executor as executor_mod

        original = executor_mod._pgid_still_ours
        try:
            executor_mod._pgid_still_ours = MagicMock(return_value=False)
            fake = _FakeProc(pid=bystander.pid, returncode=None)

            async def _run() -> None:
                await _cancel_process(fake)  # type: ignore[arg-type]

            asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_run())
        finally:
            executor_mod._pgid_still_ours = original

        # Verifier said "not ours" → cancel must have declined to kill.
        assert bystander.poll() is None
    finally:
        # SIGKILL directly (bypassing our helper) to end the bystander.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(bystander.pid, signal.SIGKILL)
        bystander.wait(timeout=5)
