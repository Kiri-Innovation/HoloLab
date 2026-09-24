"""Scratch-directory lifecycle: auto-purge on success, retain on
fail/cancel with a bounded background sweep.

The regressions we're protecting against:

* Failed jobs leaving 40+GB behind (real incident) which then filled
  disk and cascaded the next run into failure.
* Successful jobs leaving anything behind — the outputs are already
  registered as handles, scratch is by-definition transient.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hololab.node import runtime as runtime_mod
from hololab.node.runtime import NodeRuntime


def _fake_config(workspace_root: Path) -> MagicMock:
    """Just enough of NodeConfig for the sweep + purge helpers to work.

    ``staging_root`` is set to ``None`` explicitly (matches the default
    NodeConfig): otherwise ``MagicMock``'s auto-child would satisfy the
    ``is not None`` check and ``rendered_staging_dir`` would try to use
    a bogus path as the tmpfs root.
    """

    cfg = MagicMock()
    cfg.workspace_root = workspace_root
    cfg.staging_root = None
    return cfg


def _make_scratch(
    workspace_root: Path, job_id: str, *, age_hours: float, size_bytes: int = 512
) -> Path:
    """Create ``scratch/{job_id}`` with one file and backdated mtime."""

    d = workspace_root / "scratch" / job_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "payload.bin").write_bytes(b"x" * size_bytes)
    if age_hours > 0:
        past = time.time() - age_hours * 3600.0
        os.utime(d, (past, past))
    return d


def test_sweep_removes_aged_scratch(tmp_path: Path) -> None:
    r = NodeRuntime.__new__(NodeRuntime)  # skip __init__
    r._config = _fake_config(tmp_path)
    r._jobs = {}

    stale = _make_scratch(tmp_path, "stale-job", age_hours=100)
    fresh = _make_scratch(tmp_path, "fresh-job", age_hours=1)

    r._sweep_scratch_once()

    assert not stale.exists(), "scratch older than retention must be removed"
    assert fresh.exists(), "scratch inside retention window must be preserved"


def test_sweep_skips_running_job(tmp_path: Path) -> None:
    r = NodeRuntime.__new__(NodeRuntime)
    r._config = _fake_config(tmp_path)
    # ``_jobs`` is keyed by job_id; presence means the job is currently
    # running. A running job's scratch must never be deleted even if the
    # timestamp is stale (mtime updates can lag behind the shell).
    running_id = "running-job"
    r._jobs = {running_id: MagicMock()}

    living = _make_scratch(tmp_path, running_id, age_hours=100)

    r._sweep_scratch_once()
    assert living.exists()


def test_purge_now_deletes_scratch(tmp_path: Path) -> None:
    """Purge is scheduled synchronously, executed on the loop's thread pool."""

    async def _drive() -> None:
        r = NodeRuntime.__new__(NodeRuntime)
        r._config = _fake_config(tmp_path)
        r._jobs = {}
        r._purge_tasks = set()

        target = _make_scratch(tmp_path, "success-job", age_hours=0)

        r._purge_scratch_now("success-job")
        # ``_purge_scratch_now`` must not block on the ``rmtree`` — it
        # returns immediately after scheduling. Drain the background
        # task before asserting.
        assert r._purge_tasks, "expected a scheduled purge task"
        await asyncio.gather(*list(r._purge_tasks))
        assert not target.exists()
        # Task removes itself from the tracking set via done_callback.
        assert not r._purge_tasks

    asyncio.run(_drive())


def test_purge_now_is_idempotent_for_missing_scratch(tmp_path: Path) -> None:
    """No paths to remove -> no task scheduled, no exception."""

    async def _drive() -> None:
        r = NodeRuntime.__new__(NodeRuntime)
        r._config = _fake_config(tmp_path)
        r._jobs = {}
        r._purge_tasks = set()

        r._purge_scratch_now("never-was")
        assert not r._purge_tasks

    asyncio.run(_drive())


def test_purge_now_returns_before_rmtree_finishes(tmp_path: Path) -> None:
    """Regression guard for the async purge: the caller must not block.

    Before the async refactor ``_purge_scratch_now`` synchronously walked
    the scratch tree, adding measurable post-done overhead per shard
    (~1 s on the STG 100-shard fanout). After the refactor it schedules
    the ``rmtree`` on the default thread pool and returns immediately;
    this test locks that contract in by holding the executor's GIL slot
    with a blocking rmtree substitute and asserting the caller still
    returns while the task is still pending.
    """

    import shutil
    import threading

    async def _drive() -> None:
        r = NodeRuntime.__new__(NodeRuntime)
        r._config = _fake_config(tmp_path)
        r._jobs = {}
        r._purge_tasks = set()

        _make_scratch(tmp_path, "slow-job", age_hours=0)

        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        finish_evt = threading.Event()
        real_rmtree = shutil.rmtree

        def _slow_rmtree(path: str) -> None:
            # Runs on the executor thread — signal back via
            # ``call_soon_threadsafe`` and block on a plain
            # ``threading.Event`` rather than a per-loop primitive.
            loop.call_soon_threadsafe(started.set)
            finish_evt.wait(timeout=5.0)
            real_rmtree(path)

        try:
            shutil.rmtree = _slow_rmtree  # type: ignore[assignment]
            r._purge_scratch_now("slow-job")
            # If the caller had waited synchronously we'd have already
            # blocked on ``finish_evt``. Instead we're still here.
            await asyncio.wait_for(started.wait(), timeout=2.0)
            assert r._purge_tasks, "task should still be in flight"
            finish_evt.set()
            await asyncio.gather(*list(r._purge_tasks))
        finally:
            shutil.rmtree = real_rmtree  # type: ignore[assignment]

    asyncio.run(_drive())


def test_retention_hours_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOLOLAB_SCRATCH_RETENTION_HOURS", "1.5")
    assert runtime_mod._scratch_retention_hours() == 1.5

    # Invalid → default.
    monkeypatch.setenv("HOLOLAB_SCRATCH_RETENTION_HOURS", "not-a-number")
    assert runtime_mod._scratch_retention_hours() == runtime_mod._SCRATCH_RETENTION_HOURS_DEFAULT

    # Zero clamps up to 1 (minimum), so it never becomes an "eager purge"
    # accidentally.
    monkeypatch.setenv("HOLOLAB_SCRATCH_RETENTION_HOURS", "0")
    assert runtime_mod._scratch_retention_hours() == 1.0

    # Absurdly large clamps down to 30 days.
    monkeypatch.setenv("HOLOLAB_SCRATCH_RETENTION_HOURS", "9999999")
    assert runtime_mod._scratch_retention_hours() == 24.0 * 30
