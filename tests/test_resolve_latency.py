"""Regressions for the synchronous catalog work that starved reference resolution."""

from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiosqlite
import pytest

from hololab.gateway.app import _resolve_handle_tags
from hololab.gateway.registry import NodeRegistry
from hololab.manifest import load_manifest
from hololab.persistence.diagnostics import current, instrument_reader, phase


async def test_concrete_handle_tags_skip_database_and_catalog():
    registry = SimpleNamespace(catalog_json=Mock(side_effect=AssertionError("catalog read")))
    jobs = SimpleNamespace(get=AsyncMock(side_effect=AssertionError("DB read")))
    tags = ["image"]
    handle = SimpleNamespace(tags=tags, job_id="job", output_port_name="out")
    result = await _resolve_handle_tags(handle, registry=registry, jobs_store=jobs, workflows=None)
    assert result == tags
    assert result is not tags


def test_manifest_cache_reuses_parse_but_observes_edits_and_live_sessions(tmp_path, monkeypatch):
    path = tmp_path / "manifest.yaml"
    source = """apiVersion: hololab.dev/v1
kind: Algorithm
name: demo
version: 0.1.0
outputs:
  out:
    tags: [image]
runtime:
  env: test
exec:
  shell: echo hello
"""
    path.write_text(source)
    registry = NodeRegistry(None)
    pack = SimpleNamespace(
        name="demo",
        version="0.1.0",
        manifest_hash="hash",
        manifest_path=str(path),
        source_dir=str(tmp_path),
    )
    registry._sessions["node"] = SimpleNamespace(node_id="node", packs=[pack], pack_dirs=[])
    loader = Mock(wraps=load_manifest)
    monkeypatch.setattr("hololab.manifest.load_manifest", loader)
    first = registry.catalog_json()
    # Public nested values must not mutate the cached manifest.
    first[0]["outputs"]["out"]["tags"].append("poison")
    assert registry.catalog_json()[0]["outputs"]["out"]["tags"] == ["image"]
    assert registry.get_output_port_spec("demo", "0.1.0", "out").tags == ["image"]
    assert loader.call_count == 1

    old = path.stat()
    path.write_text(source.replace("image", "video"))  # Same size, restored mtime.
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert registry.get_output_port_spec("demo", "0.1.0", "out").tags == ["video"]
    assert loader.call_count == 2
    path.unlink()
    assert registry.get_output_port_spec("demo", "0.1.0", "out") is None
    assert registry.catalog_json()[0]["outputs"] == {}
    registry._sessions.clear()
    assert registry.catalog_json() == []


async def test_reader_timings_distinguish_queue_and_worker_and_preserve_errors():
    async with aiosqlite.connect(":memory:") as conn:
        await conn.create_function("pause", 0, lambda: time.sleep(0.02))
        instrument_reader(conn)
        blocker = asyncio.create_task(conn._execute(time.sleep, 0.05))
        await asyncio.sleep(0.005)
        metrics = {}
        token = current.set(metrics)
        try:
            with phase("query"):
                async with conn.execute("SELECT pause()") as cursor:
                    assert await cursor.fetchone() == (None,)
            assert metrics["db_ops"] == 3
            assert metrics["db_queue_ms"] >= 10
            assert metrics["db_work_ms"] >= 15
            assert metrics["db_resume_ms"] >= 0
            assert metrics["phases_ms"]["query"] >= 20
            with pytest.raises(aiosqlite.OperationalError):
                await conn.execute("SELECT missing_column")
            assert metrics["db_ops"] == 4
        finally:
            current.reset(token)
            await blocker
        assert current.get() is None
