import asyncio
import os
from unittest.mock import AsyncMock

from hololab.node.config import NodeConfig, write_node_config
from hololab.node.runtime import NodeRuntime, _load_packs
from tests.test_pack_dirs import _write_manifest


def _runtime(tmp_path, cfg, *, overrides=None):
    path = tmp_path / "config.yaml"
    write_node_config(cfg, path)
    runtime = NodeRuntime(cfg, config_path=path, config_overrides=overrides)
    runtime._restart_file_server = AsyncMock()
    runtime._refresh_environment_evidence = AsyncMock()
    runtime._send = AsyncMock()
    runtime._safe_send = AsyncMock()
    runtime._packs = _load_packs(cfg.pack_scan_dirs)
    return runtime, path


async def _cancel_pack_watcher(runtime):
    if runtime._watch_task is not None:
        runtime._watch_task.cancel()
        await asyncio.gather(runtime._watch_task, return_exceptions=True)
        runtime._watch_task = None


def test_manual_reload_rereads_pack_dirs_and_changes_count(tmp_path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    installed = tmp_path / "installed"
    for index in range(3):
        _write_manifest(new_root / f"rig-{index}", f"rig-{index}", "1.0.0")
    cfg = NodeConfig(pack_dirs=[old_root], installed_packs=installed)
    runtime, path = _runtime(tmp_path, cfg)
    write_node_config(cfg.model_copy(update={"pack_dirs": [old_root, new_root]}), path)

    async def exercise():
        before = len(runtime._packs)
        sync = await runtime.sync_config_from_disk(trigger="manual-pack-reload")
        result = await runtime._reload_packs()
        await _cancel_pack_watcher(runtime)
        return before, sync, result

    before, sync, result = asyncio.run(exercise())
    assert result["count"] == before + 3
    assert sync["pack_roots_changed"] is True
    assert sync["restart_required_fields"] == []


def test_invalid_yaml_keeps_live_config_then_recovers(tmp_path):
    cfg = NodeConfig(pack_dirs=[tmp_path / "old"], installed_packs=tmp_path / "installed")
    runtime, path = _runtime(tmp_path, cfg)
    path.write_text("pack_dirs: [unterminated\n")

    first = asyncio.run(runtime.sync_config_from_disk(trigger="test"))
    assert first["ok"] is False
    assert runtime._config.pack_dirs == cfg.pack_dirs
    assert runtime._last_config_reload_error

    new_root = tmp_path / "new"
    write_node_config(cfg.model_copy(update={"pack_dirs": [new_root]}), path)

    async def recover():
        result = await runtime.sync_config_from_disk(trigger="test")
        await _cancel_pack_watcher(runtime)
        return result

    recovered = asyncio.run(recover())
    assert recovered["ok"] is True
    assert runtime._config.pack_dirs == [new_root]
    assert runtime._last_config_reload_error is None


def test_cli_workspace_override_wins_disk_reload(tmp_path):
    cli_root = tmp_path / "cli-workspace"
    cfg = NodeConfig(workspace_root=tmp_path / "yaml-before")
    runtime, path = _runtime(
        tmp_path,
        cfg.model_copy(update={"workspace_root": cli_root}),
        overrides={"workspace_root": cli_root},
    )
    write_node_config(cfg.model_copy(update={"workspace_root": tmp_path / "yaml-after"}), path)

    result = asyncio.run(runtime.sync_config_from_disk(trigger="test"))
    assert result["ok"] is True
    assert runtime._config.workspace_root == cli_root
    runtime._restart_file_server.assert_not_awaited()


def test_restart_required_field_stays_live_and_is_reported(tmp_path):
    cfg = NodeConfig(file_server_port=8829)
    runtime, path = _runtime(tmp_path, cfg)
    write_node_config(cfg.model_copy(update={"file_server_port": 9929}), path)

    result = asyncio.run(runtime.sync_config_from_disk(trigger="test"))
    assert result["ok"] is True
    assert runtime._config.file_server_port == 8829
    assert result["restart_required_fields"] == ["file_server_port"]
    assert runtime._effective_config_view()["restart_required_fields"] == ["file_server_port"]


def test_live_apply_does_not_touch_active_jobs(tmp_path):
    cfg = NodeConfig(pack_dirs=[tmp_path / "old"], installed_packs=tmp_path / "installed")
    runtime, path = _runtime(tmp_path, cfg)
    write_node_config(cfg.model_copy(update={"pack_dirs": [tmp_path / "new"]}), path)

    async def exercise():
        running = asyncio.create_task(asyncio.Event().wait())
        runtime._jobs["long-job"] = running
        try:
            result = await runtime.sync_config_from_disk(trigger="test")
            assert runtime._jobs["long-job"] is running
            assert not running.done()
            return result
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            await _cancel_pack_watcher(runtime)

    assert asyncio.run(exercise())["ok"] is True


def test_config_watcher_applies_plain_file_write_without_api(tmp_path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    _write_manifest(new_root / "one", "watched", "1.0.0")
    cfg = NodeConfig(pack_dirs=[old_root], installed_packs=tmp_path / "installed")
    runtime, path = _runtime(tmp_path, cfg)

    async def exercise():
        watcher = asyncio.create_task(runtime._config_watch_loop())
        try:
            await asyncio.sleep(0.2)
            write_node_config(cfg.model_copy(update={"pack_dirs": [new_root]}), path)
            async with asyncio.timeout(5):
                while not any(
                    call.args[0] == "node_config_updated"
                    for call in runtime._safe_send.await_args_list
                ):
                    await asyncio.sleep(0.05)
            assert runtime._config.pack_dirs == [new_root]
            assert ("watched", "1.0.0") in runtime._packs
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            await _cancel_pack_watcher(runtime)

    asyncio.run(exercise())


def test_config_watcher_sees_atomic_replace_and_recovers_after_invalid_yaml(tmp_path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    cfg = NodeConfig(pack_dirs=[old_root], installed_packs=tmp_path / "installed")
    runtime, path = _runtime(tmp_path, cfg)

    async def exercise():
        watcher = asyncio.create_task(runtime._config_watch_loop())
        try:
            await asyncio.sleep(0.2)
            replacement = tmp_path / "config.yaml.tmp"
            replacement.write_text("pack_dirs: [unterminated\n")
            os.replace(replacement, path)
            async with asyncio.timeout(5):
                while runtime._last_config_reload_error is None:
                    await asyncio.sleep(0.05)
            assert runtime._config.pack_dirs == [old_root]

            write_node_config(cfg.model_copy(update={"pack_dirs": [new_root]}), replacement)
            os.replace(replacement, path)
            async with asyncio.timeout(5):
                while runtime._last_config_reload_error is not None:
                    await asyncio.sleep(0.05)
            assert runtime._config.pack_dirs == [new_root]
            assert runtime._last_config_reload_error is None
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            await _cancel_pack_watcher(runtime)

    asyncio.run(exercise())
