"""Env-cache spawn-path regression tests for :mod:`hololab.node.executor`.

Two invariants must hold, so that a lightweight fan-out doesn't
regress back to the ~2 s/shard conda-run tax:

1. When ``ExecPlan.cached_env`` is a dict, the executor spawns
   ``bash -c "<shell>"`` and passes that dict as ``env=`` — the
   conda binary must not appear in argv.
2. When ``ExecPlan.cached_env`` is ``None`` (env snapshot failed at
   startup, or the operator has ``use_env_cache: false``), the
   executor falls back to
   ``conda run -p <prefix> --no-capture-output bash -lc "..."``
   with the ``_clean_env()`` allow-list — matching the historical
   behavior.

Drives :func:`run_subprocess` with a patched
``asyncio.create_subprocess_exec`` so we can assert on argv and env
without needing a real conda env in the test harness. A separate
end-to-end test then runs a real subprocess with a marker env var to
confirm the fast path actually propagates the cached env to the
child (``LD_LIBRARY_PATH`` / ``CUDA_HOME`` regression coverage).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

from hololab.node.executor import ExecPlan, _clean_env, run_subprocess

# ---------------------------------------------------------------------------
# Argv / env assertions via a patched create_subprocess_exec
# ---------------------------------------------------------------------------


class _StubProc:
    """Minimal stand-in for asyncio.subprocess.Process.

    ``run_subprocess`` reads stdout/stderr until EOF, then awaits the
    process. The stub feeds empty streams and reports a successful
    exit so the executor's happy path runs through cleanly without a
    real shell.
    """

    def __init__(self, exit_code: int = 0) -> None:
        loop = asyncio.get_event_loop()
        # Empty streams — feed EOF immediately so drain_stream exits.
        self.stdout = asyncio.StreamReader(loop=loop)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader(loop=loop)
        self.stderr.feed_eof()
        self._exit_code = exit_code

    async def wait(self) -> int:
        return self._exit_code

    @property
    def returncode(self) -> int:
        return self._exit_code

    def terminate(self) -> None:  # pragma: no cover - not exercised in happy path
        pass

    def kill(self) -> None:  # pragma: no cover - not exercised in happy path
        pass


def _run_and_capture(plan: ExecPlan) -> dict[str, Any]:
    """Invoke run_subprocess with create_subprocess_exec patched.

    Returns the captured ``args`` (positional argv) and ``kwargs``
    (cwd, env, etc.) so tests can assert on the exact spawn call.
    """

    captured: dict[str, Any] = {}
    orig = asyncio.create_subprocess_exec

    async def spy(*args: Any, **kwargs: Any) -> _StubProc:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _StubProc()

    async def drive() -> None:
        asyncio.create_subprocess_exec = spy  # type: ignore[assignment]
        try:
            cancel_event = asyncio.Event()
            await run_subprocess(
                plan,
                on_log=lambda _s, _l: None,
                on_progress=lambda _c, _t: None,
                cancel_event=cancel_event,
            )
        finally:
            asyncio.create_subprocess_exec = orig  # type: ignore[assignment]

    asyncio.run(drive())
    return captured


def _make_plan(*, cached_env: dict[str, str] | None) -> ExecPlan:
    return ExecPlan(
        shell="echo hello-from-shard",
        conda_bin="/fake/conda",
        conda_prefix="/fake/envs/kiri",
        working_dir=Path("/tmp"),
        progress_regex=None,
        cached_env=cached_env,
    )


def test_cached_env_hit_bypasses_conda_run() -> None:
    """With ``cached_env`` set, argv must be ``["bash", "-c", <shell>]``
    and ``env=`` must be the cached dict — no ``conda run`` anywhere."""

    cached = {"PATH": "/fake/envs/kiri/bin:/usr/bin", "SHARD_MARKER": "yes"}
    plan = _make_plan(cached_env=cached)

    cap = _run_and_capture(plan)

    argv = list(cap["args"])
    assert argv == ["bash", "-c", "echo hello-from-shard"], (
        f"expected bash -c fast-path spawn, got {argv!r}"
    )
    # Nothing conda-related should have leaked into argv.
    assert not any("conda" in a for a in argv), argv
    assert cap["kwargs"]["env"] is cached, "cached env dict must be passed through by identity"


def test_cached_env_miss_falls_back_to_conda_run() -> None:
    """With ``cached_env=None``, argv must be the historical
    ``conda run … bash -lc "..."`` form and ``env=`` must be the
    ``_clean_env()`` allow-list (no full daemon env inheritance)."""

    plan = _make_plan(cached_env=None)

    cap = _run_and_capture(plan)

    argv = list(cap["args"])
    assert argv == [
        "/fake/conda",
        "run",
        "-p",
        "/fake/envs/kiri",
        "--no-capture-output",
        "bash",
        "-lc",
        "echo hello-from-shard",
    ], f"expected conda-run fallback spawn, got {argv!r}"

    env = cap["kwargs"]["env"]
    expected = _clean_env()
    assert env == expected, (
        "fallback path must use the clean-env allow-list, not the full daemon env"
    )
    # Belt-and-braces: LD_LIBRARY_PATH / CUDA_* must not have leaked.
    assert "LD_LIBRARY_PATH" not in env
    assert not any(k.startswith("CUDA_") for k in env)


def test_cached_env_default_is_none_preserves_legacy_call_sites() -> None:
    """ExecPlan without ``cached_env`` at all (positional or keyword)
    keeps ``None`` — so any pre-existing call site that omits the
    field still lands on the fallback path, not a crash."""

    plan = ExecPlan(
        shell="true",
        conda_bin="/fake/conda",
        conda_prefix="/fake/envs/kiri",
        working_dir=Path("/tmp"),
    )
    assert plan.cached_env is None


# ---------------------------------------------------------------------------
# End-to-end: cached env actually reaches the child process
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only test")
def test_cached_env_reaches_child_process(tmp_path: Path) -> None:
    """Spawn a real ``bash`` with a marker env var in ``cached_env``
    and confirm the child sees it. Regression guard for the case
    where a future refactor accidentally drops ``env=`` on the fast
    path and the daemon's own environ leaks through instead.

    Uses ``/tmp`` writes rather than stdout capture because the
    executor buffers stdout via ``asyncio.StreamReader`` and we don't
    want to reproduce that plumbing in the test.
    """

    marker_path = tmp_path / "marker.txt"
    marker_value = "cached-env-worked-b7d9f0"
    cached = {
        # PATH minimal — enough to find ``bash`` and coreutils.
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "HOLOLAB_TEST_MARKER": marker_value,
    }

    async def drive() -> None:
        plan = ExecPlan(
            shell=f'printf "%s" "$HOLOLAB_TEST_MARKER" > {marker_path}',
            conda_bin="/nonexistent/conda",  # must NOT be invoked on this path
            conda_prefix="/nonexistent/env",
            working_dir=tmp_path,
            cached_env=cached,
        )
        result = await run_subprocess(
            plan,
            on_log=lambda _s, _l: None,
            on_progress=lambda _c, _t: None,
            cancel_event=asyncio.Event(),
        )
        assert result.exit_code == 0, (result.exit_code, result.stderr_tail)

    asyncio.run(drive())

    assert marker_path.read_text() == marker_value, (
        "cached env dict did not reach the child process"
    )
