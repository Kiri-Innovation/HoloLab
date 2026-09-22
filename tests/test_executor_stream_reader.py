"""Stream-reader regression tests for :mod:`hololab.node.executor`.

Reproduces the tqdm/LimitOverrunError incident (stg run stuck in ``running``,
compute subprocess orphaned) and locks the chunked-read + split-on-\\r+\\n
behaviour so it can never regress into the "wait for \\n and blow up at
64 KiB" state again.

Drives :func:`drain_stream` with a hand-built :class:`asyncio.StreamReader`
so we exercise the exact loop the executor uses at runtime without needing
a conda env or a real subprocess.
"""

from __future__ import annotations

import asyncio
from collections import deque

import pytest

from hololab.node.executor import _MAX_UNBROKEN_BYTES, drain_stream


def _drive(data: bytes, *, close: bool = True) -> tuple[deque[str], list[tuple[str, str]]]:
    """Feed ``data`` into a StreamReader and run drain_stream to completion.

    Returns ``(tail, emitted)`` — tail is the deque the drain populates
    (mirrors the executor's per-stream ring buffer), emitted is every
    ``(stream_name, line)`` tuple passed to the emit callback.
    """
    tail: deque[str] = deque(maxlen=200_000)
    emitted: list[tuple[str, str]] = []

    def emit(name: str, tail_arg: deque[str], line: str) -> None:
        # Keep tail wiring identical to executor's real usage — drain
        # writes into the tail via the emit callback.
        tail_arg.append(line)
        emitted.append((name, line))

    loop = asyncio.new_event_loop()
    try:
        reader = asyncio.StreamReader(limit=1024, loop=loop)
        reader.feed_data(data)
        if close:
            reader.feed_eof()
        loop.run_until_complete(drain_stream(reader, name="stderr", tail=tail, emit=emit))
    finally:
        loop.close()
    return tail, emitted


# ---------------------------------------------------------------------------
# The bug: tqdm-style \r-only progress bar > 64 KiB
# ---------------------------------------------------------------------------


def test_tqdm_style_long_cr_stream_does_not_crash() -> None:
    """Feed 1000 ``\\r``-terminated updates (each 256 bytes → 256 KiB
    total) with NO newlines, then a final ``\\ndone\\n``. Pre-fix
    ``readline()`` blew up with LimitOverrunError past 64 KiB and killed
    the whole task; now every ``\\r``-delimited segment is emitted."""

    pad = b"x" * 240
    payload = b"".join(f"tick {i:04d} ".encode() + pad + b"\r" for i in range(1000))
    payload += b"\ndone\n"
    assert len(payload) > 200 * 1024, "payload must exceed asyncio's 64 KiB readline limit"

    _tail, emitted = _drive(payload)

    lines = [line for _stream, line in emitted]
    tick_lines = [ln for ln in lines if ln.startswith("tick ")]
    assert len(tick_lines) == 1000, f"expected 1000 tick lines, got {len(tick_lines)}"
    assert tick_lines[0].startswith("tick 0000")
    assert tick_lines[-1].startswith("tick 0999")
    # \n splitting still works alongside \r splitting.
    assert "done" in lines


def test_mixed_cr_and_lf_split_correctly() -> None:
    """A byte sequence with both ``\\r`` updates and a trailing ``\\n``
    splits into the redraw progress items PLUS the terminal message —
    tqdm's \\r-redraw + final \\n is faithfully captured."""

    _tail, emitted = _drive(b"a\rbb\rccc\rddone\n")
    lines = [line for _s, line in emitted]
    assert lines == ["a", "bb", "ccc", "ddone"]


def test_crlf_windows_style_splits_cleanly() -> None:
    """Windows-style ``\\r\\n`` line endings produce one line per pair —
    the ``\\r`` and ``\\n`` each get treated as a separator, but the
    ``\\n`` immediately after finds an empty segment (``b""``) which
    also gets emitted. Locks that behaviour so callers can spot it.
    """

    _tail, emitted = _drive(b"one\r\ntwo\r\n")
    lines = [line for _s, line in emitted]
    # "one", "" (from the \n after \r), "two", "" — the empty
    # segments are the price of treating both bytes as separators;
    # in practice log tails ignore them.
    assert lines == ["one", "", "two", ""]


# ---------------------------------------------------------------------------
# Safety cap: pathological single line without any separator
# ---------------------------------------------------------------------------


def test_unbroken_stream_past_cap_still_gets_flushed() -> None:
    """A pathological single "line" bigger than ``_MAX_UNBROKEN_BYTES``
    (no \\r, no \\n) must NOT buffer indefinitely — the reader flushes
    a chunk and moves on so the subprocess never blocks on write."""

    size = int(_MAX_UNBROKEN_BYTES * 1.5)
    payload = b"A" * size + b"\nend\n"

    _tail, emitted = _drive(payload)

    lines = [line for _s, line in emitted]
    # Every 'A' byte is accounted for across the flushed chunks — no
    # bytes were silently dropped, just re-fragmented.
    total_a = sum(ln.count("A") for ln in lines)
    assert total_a == size
    assert "end" in lines


# ---------------------------------------------------------------------------
# Baseline: normal \n-terminated output still works
# ---------------------------------------------------------------------------


def test_plain_lf_lines_still_work_as_before() -> None:
    """Regression: the chunk-based reader must not break the normal case
    where a pack emits one \\n-terminated line at a time."""

    _tail, emitted = _drive(b"hello\nworld\n")
    lines = [line for _s, line in emitted]
    assert lines == ["hello", "world"]


def test_trailing_bytes_without_terminator_are_flushed_on_eof() -> None:
    """Bytes present at EOF without a trailing ``\\n``/``\\r`` still get
    emitted — otherwise a job whose last log line lacked a newline
    would lose that line entirely."""

    _tail, emitted = _drive(b"partial-last-line")
    lines = [line for _s, line in emitted]
    assert lines == ["partial-last-line"]


def test_empty_stream_produces_no_lines() -> None:
    """Immediate EOF: no lines emitted, no crash."""
    _tail, emitted = _drive(b"")
    assert emitted == []


# ---------------------------------------------------------------------------
# Silent-crash guard: reader that raises must not propagate
# ---------------------------------------------------------------------------


def test_reader_that_raises_is_swallowed_and_pipe_drained() -> None:
    """If the underlying stream reader raises an exception mid-drain,
    ``drain_stream`` must catch it and finish silently (drain the pipe
    of remaining bytes). Pre-fix the exception propagated to
    ``run_subprocess``'s ``await stderr_task`` and killed ``_run_job``
    without ever emitting a ``job_fail`` frame."""

    class _ExplodingReader:
        """Async reader that returns one good chunk then explodes.

        The exception must not escape ``drain_stream``, and any bytes
        the reader offers after the raise (via the second ``read`` call
        in the drain path) must be quietly consumed so the subprocess
        end doesn't back-pressure.
        """

        def __init__(self) -> None:
            self._calls = 0

        async def read(self, _n: int) -> bytes:
            self._calls += 1
            if self._calls == 1:
                return b"good line\n"
            if self._calls == 2:
                raise RuntimeError("boom")
            # Post-error drain path — return leftover then EOF.
            if self._calls == 3:
                return b"post-error remainder"
            return b""

    reader = _ExplodingReader()
    tail: deque[str] = deque(maxlen=100)
    emitted: list[tuple[str, str]] = []

    def emit(name: str, t: deque[str], line: str) -> None:
        t.append(line)
        emitted.append((name, line))

    # Must not raise.
    asyncio.new_event_loop().run_until_complete(
        drain_stream(reader, name="stderr", tail=tail, emit=emit)  # type: ignore[arg-type]
    )
    # The good chunk emitted before the error survives; anything after
    # the raise is drained but not emitted (we're past error recovery
    # so preserving partial log fidelity is not the goal).
    assert any(ln == "good line" for _s, ln in emitted)
    # Drain consumed the post-error remainder via read()
    # (calls: 1=good, 2=raise, 3=remainder, 4=EOF).
    assert reader._calls >= 3


# ---------------------------------------------------------------------------
# Real subprocess end-to-end (guarded — only runs if conda kiri env is
# present, so CI without conda skips cleanly instead of failing).
# ---------------------------------------------------------------------------

_KIRI_ENV = "/cloud/cloud-ssd1/envs/kiri"
_HAS_KIRI = __import__("pathlib").Path(_KIRI_ENV).is_dir()


@pytest.mark.skipif(
    not _HAS_KIRI, reason="kiri conda env not present; skipping end-to-end subprocess drive"
)
def test_end_to_end_subprocess_with_tqdm_stress(tmp_path):  # type: ignore[no-untyped-def]
    """End-to-end: spawn a real subprocess via :func:`run_subprocess`
    that emits an oversized ``\\r``-only tqdm barrage on stderr, then
    exits 0. Pre-fix this returned a task with an unretrieved exception
    and left the process orphaned; now it must complete cleanly."""

    from hololab.node.executor import ExecPlan, run_subprocess

    events: list[tuple[str, str]] = []
    cancel_event = asyncio.Event()

    async def _go() -> tuple[int, list[tuple[str, str]]]:
        plan = ExecPlan(
            shell=(
                'python -c "import sys\n'
                'pad = \\"x\\" * 240\n'
                "for i in range(1000):\n"
                '    sys.stderr.write(f\\"tick {i:04d} \\" + pad + \\"\\r\\")\n'
                'sys.stderr.write(\\"\\ndone\\n\\")\n'
                'sys.stderr.flush()"'
            ),
            conda_bin="/cloud/cloud-ssd1/conda/bin/conda",
            conda_prefix=_KIRI_ENV,
            working_dir=tmp_path,
        )
        result = await run_subprocess(
            plan,
            on_log=lambda s, line: events.append((s, line)),
            on_progress=lambda cur, tot: None,
            cancel_event=cancel_event,
        )
        return result.exit_code, events

    exit_code, evs = asyncio.new_event_loop().run_until_complete(_go())
    assert exit_code == 0
    stderr_lines = [line for stream, line in evs if stream == "stderr"]
    ticks = [ln for ln in stderr_lines if ln.startswith("tick ")]
    assert len(ticks) == 1000
    assert "done" in stderr_lines
