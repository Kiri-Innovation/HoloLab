"""Subprocess executor — the sacred boundary.

Every job runs as ``conda run -p <prefix> bash -c "<rendered shell>"``. The
node runtime NEVER imports algorithm code (see docs/architecture.md#execution-boundary).

Environment inheritance is deliberately clean: we pass an empty ``env`` to
``subprocess.Popen`` and let ``conda run`` populate ``PATH``, ``LD_LIBRARY_PATH``,
``CUDA_HOME`` etc. inside the target env. If a specific env var must survive,
declare it in the manifest ``exec.shell`` block itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
import sys
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path

from hololab.logging import get_logger

log = get_logger("node.executor")

# Grace period between SIGTERM and SIGKILL on cancel.
CANCEL_GRACE_SECONDS = 30.0

# Number of stdout/stderr tail lines kept for job_fail messages.
_TAIL_KEEP = 40

# Chunk size for stream draining. Small enough that progress bars flush
# per redraw, large enough to avoid syscall overhead in bulk log output.
_STREAM_CHUNK = 4096
# Emit a "line" even if we've seen no separator this long — protects
# against a genuinely runaway single line (e.g. a binary blob written
# to stdout) filling memory indefinitely. Sized well above any realistic
# tqdm redraw block; a single stanza past this gets flushed as one line.
_MAX_UNBROKEN_BYTES = 1024 * 1024  # 1 MiB


@dataclass
class ExecPlan:
    """Everything the executor needs to spawn one job."""

    shell: str
    conda_bin: str
    conda_prefix: str
    working_dir: Path
    progress_regex: str | None = None


@dataclass
class ExecResult:
    """Outcome of a subprocess run."""

    exit_code: int
    cancelled: bool = False
    stdout_tail: list[str] = field(default_factory=list)
    stderr_tail: list[str] = field(default_factory=list)


async def run_subprocess(
    plan: ExecPlan,
    *,
    on_log: Callable[[str, str], None],  # (stream, line)
    on_progress: Callable[[int, int], None],  # (current, total)
    cancel_event: asyncio.Event,
) -> ExecResult:
    """Run one job to completion. Returns exit code, tails, and cancel flag.

    ``on_log`` is called synchronously for each output line; callers must not
    block. ``on_progress`` is called with each successful progress-regex match.
    """

    # ``bash -c`` receives the rendered shell as a single argument. Manifest
    # authors write normal multi-line shell here.
    cmd = [
        plan.conda_bin,
        "run",
        "-p",
        plan.conda_prefix,
        "--no-capture-output",
        "bash",
        "-lc",
        plan.shell,
    ]

    log.info(
        "spawn",
        conda_prefix=plan.conda_prefix,
        working_dir=str(plan.working_dir),
    )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(plan.working_dir),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        # Clean env: no inheritance from node runtime. See module docstring.
        env=_clean_env(),
        # New process group so SIGTERM/SIGKILL can reach the shell's children.
        # Windows CTRL_BREAK_EVENT is deferred; see docs/architecture.md.
        start_new_session=(sys.platform != "win32"),
    )

    progress_re = re.compile(plan.progress_regex) if plan.progress_regex else None
    stdout_tail: deque[str] = deque(maxlen=_TAIL_KEEP)
    stderr_tail: deque[str] = deque(maxlen=_TAIL_KEEP)

    def _emit_line(name: str, tail: deque[str], line: str) -> None:
        tail.append(line)
        on_log(name, line)
        if progress_re is not None:
            m = progress_re.search(line)
            if m and len(m.groups()) >= 2:
                try:
                    cur = int(m.group(m.lastindex - 1 if m.lastindex else 1))
                    tot = int(m.group(m.lastindex if m.lastindex else 2))
                    on_progress(cur, tot)
                except (TypeError, ValueError):
                    pass

    async def _stream(reader: asyncio.StreamReader | None, name: str, tail: deque[str]) -> None:
        await drain_stream(
            reader,
            name=name,
            tail=tail,
            emit=lambda n, t, line: _emit_line(n, t, line),
        )

    stdout_task = asyncio.create_task(_stream(proc.stdout, "stdout", stdout_tail))
    stderr_task = asyncio.create_task(_stream(proc.stderr, "stderr", stderr_tail))
    cancel_task = asyncio.create_task(cancel_event.wait())

    cancelled = False
    try:
        wait_task = asyncio.create_task(proc.wait())
        done, _pending = await asyncio.wait(
            {wait_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
        )

        if cancel_task in done and wait_task not in done:
            cancelled = True
            await _cancel_process(proc)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(wait_task, timeout=CANCEL_GRACE_SECONDS + 5)
        else:
            cancel_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cancel_task
    finally:
        await stdout_task
        await stderr_task

    return ExecResult(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        cancelled=cancelled,
        stdout_tail=list(stdout_tail),
        stderr_tail=list(stderr_tail),
    )


async def drain_stream(
    reader: asyncio.StreamReader | None,
    *,
    name: str,
    tail: deque[str],
    emit: Callable[[str, deque[str], str], None],
) -> None:
    """Chunk-read a subprocess pipe, splitting on both ``\\n`` and ``\\r``.

    tqdm-style progress bars rewrite the current terminal line with ``\\r``
    (no ``\\n``), so many updates queue behind a single missing newline.
    Naïve ``reader.readline()`` waits for ``\\n`` and blows past its
    64 KiB default buffer with ``LimitOverrunError`` after ~1000 tqdm
    ticks — which used to silently kill the job (the exception propagated
    out of :func:`run_subprocess` and ``_run_job`` never sent a
    ``job_fail`` frame; the gateway then held the job in ``running``
    forever). Treating ``\\r`` as a separator keeps each progress
    redraw as its own logical line and never exceeds the cap.

    Never propagates exceptions: on error, drains the pipe silently so
    :meth:`asyncio.subprocess.Process.wait` doesn't hang on a full pipe
    buffer. Broken out as a module-level helper so tests can drive it
    with a hand-built :class:`asyncio.StreamReader`.
    """

    if reader is None:
        return
    buf = bytearray()
    try:
        while True:
            chunk = await reader.read(_STREAM_CHUNK)
            if not chunk:
                if buf:
                    emit(name, tail, bytes(buf).decode(errors="replace"))
                    buf.clear()
                return
            buf.extend(chunk)
            # Drain every complete \n- or \r-delimited segment.
            start = 0
            for i, b in enumerate(buf):
                if b == 0x0A or b == 0x0D:  # \n or \r
                    emit(name, tail, bytes(buf[start:i]).decode(errors="replace"))
                    start = i + 1
            if start:
                del buf[:start]
            # Safety cap: if a single unbroken segment grew past the
            # limit (binary output, no separators at all), flush it.
            if len(buf) > _MAX_UNBROKEN_BYTES:
                emit(name, tail, bytes(buf).decode(errors="replace"))
                buf.clear()
    except Exception as exc:
        log.warning("stream reader error", stream=name, error=str(exc))
        with contextlib.suppress(Exception):
            while True:
                remainder = await reader.read(_STREAM_CHUNK)
                if not remainder:
                    return


async def _cancel_process(proc: asyncio.subprocess.Process) -> None:
    """Send SIGTERM to the whole process group, escalate to SIGKILL after grace.

    PID recycling guard: once the shell has exited AND been reaped, the OS
    may re-hand its PID to a completely unrelated new process — possibly
    another job's descendant. Naively calling ``killpg(getpgid(proc.pid))``
    at that point signals the *new* process's group and murders whichever
    other job happens to now be sitting at that PID. That's the failure
    mode from the ``d44a1522`` cancel → ``1a0b8888``'s PID 317847 kill
    incident. Before every kill we re-verify via ``/proc/{pgid}/stat``
    that the pid is (a) still alive, (b) still leader of its own pgroup,
    and (c) still parented by this runtime. If any check fails, we skip
    the signal entirely rather than risk a wrong-victim kill.
    """

    if proc.returncode is not None:
        # Process has already exited from asyncio's point of view. Don't
        # touch its (potentially recycled) PID.
        return

    if sys.platform == "win32":
        # TODO: implement CTRL_BREAK_EVENT for Windows. Deferred; see architecture.md.
        proc.terminate()
        return

    # ``start_new_session=True`` at spawn time makes the child both its
    # session leader and process group leader, so pgid == proc.pid. We
    # capture pid once (rather than calling getpgid, which would also
    # follow PID recycling to the wrong group) and re-verify below.
    pgid = proc.pid
    our_pid = os.getpid()

    if not _pgid_still_ours(pgid, our_pid):
        log.warning(
            "cancel skipped: pgid no longer owned by this runtime (likely reaped + recycled)",
            pgid=pgid,
        )
        return

    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return

    async def _kill_after_grace() -> None:
        await asyncio.sleep(CANCEL_GRACE_SECONDS)
        # Re-verify before the escalation too — the grace window is long
        # enough for the shell to exit + be reaped + PID recycled.
        if not _pgid_still_ours(pgid, our_pid):
            log.warning(
                "sigkill escalation skipped: pgid no longer owned by this runtime",
                pgid=pgid,
            )
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)

    # Held on the process so the task isn't garbage-collected mid-sleep.
    proc._hololab_kill_task = asyncio.create_task(_kill_after_grace())  # type: ignore[attr-defined]


def _pgid_still_ours(pgid: int, our_pid: int) -> bool:
    """Return True iff PID ``pgid`` is a live process that still leads its
    own process group and is parented by ``our_pid``.

    Reads ``/proc/{pgid}/stat`` and inspects the ``pgrp`` (field 5) and
    ``ppid`` (field 4) fields — see ``proc(5)``. Any of {process gone,
    pgrp reassigned, reparented to something other than us} returns
    False. Non-Linux platforms don't have ``/proc``; on those we just
    check that a process at that PID exists and skip pgroup verification
    (best effort — the recycling window is much shorter on macOS/BSD).
    """

    if sys.platform != "linux":
        try:
            os.kill(pgid, 0)  # exists?
            return True
        except (ProcessLookupError, PermissionError):
            return False

    try:
        with open(f"/proc/{pgid}/stat", "rb") as f:
            raw = f.read()
    except (FileNotFoundError, ProcessLookupError):
        return False
    # Layout: ``pid (comm) state ppid pgrp ...``. The ``comm`` field can
    # contain spaces and parentheses, so we split on the LAST ``)`` and
    # parse the fields that follow.
    idx = raw.rfind(b")")
    if idx < 0:
        return False
    tail = raw[idx + 1 :].split()
    if len(tail) < 4:
        return False
    try:
        # tail[0] = state, tail[1] = ppid, tail[2] = pgrp.
        ppid = int(tail[1])
        pgrp = int(tail[2])
    except ValueError:
        return False
    if pgrp != pgid:
        return False  # PID was recycled into an unrelated pgroup.
    # Reparented to a non-us pid (or PID was recycled under a different
    # parent) — also refuse the kill.
    return ppid == our_pid


def _clean_env() -> dict[str, str]:
    """Return a minimal env suitable as base for ``conda run``.

    ``conda run`` needs a small handful of vars to bootstrap itself (PATH so
    it can find its own tools, HOME for conda's own state). We deliberately
    do NOT propagate ``PYTHONPATH``, ``LD_LIBRARY_PATH``, ``CUDA_*`` etc.:
    ``conda run`` sets those inside the target env.
    """

    keep = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TZ", "TMPDIR", "SHELL"}
    return {k: v for k, v in os.environ.items() if k in keep}


# ---------------------------------------------------------------------------
# Progress helper for callers that want to iterate over emitted events
# ---------------------------------------------------------------------------


async def drain_progress(events: asyncio.Queue[tuple[int, int]]) -> AsyncIterator[tuple[int, int]]:
    """Async iterator convenience over a progress queue."""

    while True:
        try:
            yield await events.get()
        except asyncio.CancelledError:
            break
