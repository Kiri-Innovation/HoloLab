"""CPU-pinning slot pool + ``_compute_cpu_masks`` split.

Locks in the observable behavior of the ``NodeConfig.cpu_pinning``
opt-in:

  * Contiguous CPU splits keep L2/L3 cache locality (the round-robin
    alternative would risk cross-socket allocation).
  * The last slot swallows any leftover cores when ``nproc % cap != 0``
    so no CPU sits idle just because the split is uneven.
  * ``cap > nproc`` disables pinning entirely — asking for 8 slots on
    a 4-core box would force sharing and defeat the point.
  * The ``asyncio.Queue`` slot pool is a strict acquire/release ring:
    the mask a task released last is what the next task pulls (order
    doesn't matter, count does).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from hololab.node.runtime import NodeRuntime, _compute_cpu_masks


def test_compute_cpu_masks_even_split() -> None:
    with patch("os.cpu_count", return_value=16):
        assert _compute_cpu_masks(8) == [
            "0-1",
            "2-3",
            "4-5",
            "6-7",
            "8-9",
            "10-11",
            "12-13",
            "14-15",
        ]
        assert _compute_cpu_masks(4) == ["0-3", "4-7", "8-11", "12-15"]
        assert _compute_cpu_masks(1) == ["0-15"]


def test_compute_cpu_masks_uneven_last_slot_swallows_leftovers() -> None:
    with patch("os.cpu_count", return_value=12):
        # 12 / 8 = 1 per slot with 4 leftover; last slot absorbs them.
        assert _compute_cpu_masks(8) == ["0", "1", "2", "3", "4", "5", "6", "7-11"]


def test_compute_cpu_masks_disabled_conditions() -> None:
    with patch("os.cpu_count", return_value=16):
        assert _compute_cpu_masks(0) == []
        assert _compute_cpu_masks(-1) == []
    with patch("os.cpu_count", return_value=4):
        # Cap > nproc: refuse to pin (every slot would share cores).
        assert _compute_cpu_masks(8) == []
    with patch("os.cpu_count", return_value=0):
        assert _compute_cpu_masks(8) == []


def test_slot_pool_acquire_release_roundtrip() -> None:
    """Acquire drains the pool; release refills it. Serialised."""

    async def _drive() -> None:
        r = NodeRuntime.__new__(NodeRuntime)
        r._cpu_masks = ["0-1", "2-3"]
        r._cpu_slots = None

        m1 = await r._acquire_cpu_slot()
        m2 = await r._acquire_cpu_slot()
        assert m1 is not None and m2 is not None
        assert {m1, m2} == {"0-1", "2-3"}

        # Pool empty — a third acquire would block. Instead release
        # and re-acquire to prove the ring cycles.
        r._release_cpu_slot(m1)
        m3 = await asyncio.wait_for(r._acquire_cpu_slot(), timeout=1.0)
        assert m3 == m1

        r._release_cpu_slot(m2)
        r._release_cpu_slot(m3)

    asyncio.run(_drive())


def test_slot_pool_disabled_returns_none() -> None:
    """Empty ``_cpu_masks`` means pinning is off — never blocks."""

    async def _drive() -> None:
        r = NodeRuntime.__new__(NodeRuntime)
        r._cpu_masks = []
        r._cpu_slots = None

        assert await r._acquire_cpu_slot() is None
        # Release of ``None`` is a no-op.
        r._release_cpu_slot(None)
        r._release_cpu_slot("0-1")  # ignored (no queue)

    asyncio.run(_drive())


def test_exec_plan_carries_cpu_mask_to_taskset_prefix(monkeypatch) -> None:
    """When ExecPlan.cpu_mask is set the executor prepends ``taskset -c``.

    Uses a fake ``asyncio.create_subprocess_exec`` to capture the
    argv without spawning anything real.
    """

    from hololab.node import executor as executor_mod

    captured: dict[str, list[str]] = {}

    class _FakeProc:
        returncode = 0

        async def wait(self) -> int:
            return 0

        @property
        def stdout(self):
            return None

        @property
        def stderr(self):
            return None

        @property
        def pid(self):
            return 1

    async def _fake_spawn(*args: str, **kwargs) -> _FakeProc:
        captured["argv"] = list(args)
        return _FakeProc()

    monkeypatch.setattr(executor_mod.asyncio, "create_subprocess_exec", _fake_spawn)

    plan = executor_mod.ExecPlan(
        shell="echo hi",
        conda_bin="conda",
        conda_prefix="/env",
        working_dir=MagicMock(),
        cached_env={"PATH": "/usr/bin"},
        cpu_mask="4-5",
    )

    async def _run() -> None:
        await executor_mod.run_subprocess(
            plan,
            on_log=lambda _s, _l: None,
            on_progress=lambda _c, _t: None,
            cancel_event=asyncio.Event(),
        )

    asyncio.run(_run())

    # ``taskset -c 4-5`` sits in front of ``bash -c "echo hi"``.
    assert captured["argv"][:3] == ["taskset", "-c", "4-5"]
    assert captured["argv"][3:] == ["bash", "-c", "echo hi"]


def test_exec_plan_without_cpu_mask_skips_taskset(monkeypatch) -> None:
    """Default path (no pinning) must not add ``taskset``."""

    from hololab.node import executor as executor_mod

    captured: dict[str, list[str]] = {}

    class _FakeProc:
        returncode = 0

        async def wait(self) -> int:
            return 0

        @property
        def stdout(self):
            return None

        @property
        def stderr(self):
            return None

        @property
        def pid(self):
            return 1

    async def _fake_spawn(*args: str, **kwargs) -> _FakeProc:
        captured["argv"] = list(args)
        return _FakeProc()

    monkeypatch.setattr(executor_mod.asyncio, "create_subprocess_exec", _fake_spawn)

    plan = executor_mod.ExecPlan(
        shell="echo hi",
        conda_bin="conda",
        conda_prefix="/env",
        working_dir=MagicMock(),
        cached_env={"PATH": "/usr/bin"},
        cpu_mask=None,
    )

    async def _run() -> None:
        await executor_mod.run_subprocess(
            plan,
            on_log=lambda _s, _l: None,
            on_progress=lambda _c, _t: None,
            cancel_event=asyncio.Event(),
        )

    asyncio.run(_run())

    assert captured["argv"] == ["bash", "-c", "echo hi"]
