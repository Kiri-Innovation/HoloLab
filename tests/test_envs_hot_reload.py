"""Real temporary interpreters; the conda shim only supplies activation/argv routing."""

import asyncio
import os
import shlex
import sys
import venv
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.node.config import NodeConfig, load_node_config, write_node_config
from hololab.node.executor import run_subprocess
from hololab.node.runtime import NodeRuntime, _make_exec_plan
from hololab.protocol.messages import NodeConfigSetReq, PackReloadReq
from tests.test_pack_dirs import _write_manifest
from tests.test_scoped_packages import session


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLOLAB_HOME", str(tmp_path / "home"))
    prefixes = [tmp_path / "env-a", tmp_path / "env-b"]
    for prefix in prefixes:
        venv.EnvBuilder(with_pip=False).create(prefix)
    conda = tmp_path / "conda-shim"
    conda.write_text(
        f"#!{sys.executable}\n"
        + """import os, sys
assert sys.argv[1] == 'run' and sys.argv[2] == '-p'
prefix = sys.argv[3]
args = sys.argv[5:]
os.environ['PATH'] = prefix + '/bin:' + os.environ.get('PATH', '')
if args[:2] == ['bash', '-lc']: args[1] = '-c'
os.execvp(args[0], args)
"""
    )
    conda.chmod(0o755)
    packs = tmp_path / "packs"
    _write_manifest(packs / "demo", "demo", "1.0.0")
    manifest = packs / "demo/manifest.yaml"
    manifest.write_text(
        manifest.read_text().replace("env: kiri", "env: ftgspp") + '\nrequires: {python: ">=3"}\n'
    )
    cfg = NodeConfig(
        conda_bin=str(conda),
        envs={},
        pack_dirs=[packs],
        installed_packs=tmp_path / "installed",
        workspace_root=tmp_path / "workspace",
    )
    path = tmp_path / "config.yaml"
    write_node_config(cfg, path)
    node = NodeRuntime(cfg, config_path=path)
    from hololab.node.runtime import _load_packs

    node._packs = _load_packs(cfg.pack_scan_dirs)
    node._send = AsyncMock()
    node._safe_send = AsyncMock()
    node._restart_file_server = AsyncMock()
    pack = node._packs[("demo", "1.0.0")]
    node._packs = {("demo", "1.0.0"): pack}
    node._ws = object()  # allow environment_updated publication through fake transport
    return SimpleNamespace(node=node, path=path, a=str(prefixes[0]), b=str(prefixes[1]), pack=pack)


async def patch(rig, value):
    await rig.node._handle_config_set_req(NodeConfigSetReq(req_id="test", patch=value))
    response = rig.node._send.call_args.args[1]
    if rig.node._env_reload_task is not None:
        await rig.node._env_reload_task
    return response


@pytest.mark.asyncio
async def test_real_resolver_add_replace_delete_clear_and_frozen_snapshot(rig):
    node = rig.node
    assert (await node._environment_report(rig.pack.manifest))["status"] == "missing"
    assert (await patch(rig, {"envs": {"ftgspp": rig.a, "keep": rig.b}})).ok
    old_config, old_cache = node._config, node._env_cache
    report = await node._environment_report(rig.pack.manifest)
    assert report["prefix"] == rig.a and report["status"] == "satisfied"
    assert report["python"] == sys.version.split()[0]
    old_cache[rig.a] = {**os.environ, "PATH": rig.a + "/bin:" + os.environ["PATH"]}
    assert (await patch(rig, {"envs": {"ftgspp": rig.b}})).ok
    assert node._config.envs == {"ftgspp": rig.b}
    assert rig.a not in node._env_cache and rig.a in old_cache
    assert (await node._environment_report(rig.pack.manifest))["prefix"] == rig.b
    frozen = await node._environment_report(
        rig.pack.manifest, config_snapshot=old_config, env_cache_snapshot=old_cache
    )
    assert frozen["prefix"] == rig.a and frozen["status"] == "satisfied"
    assert (await patch(rig, {"envs": {}})).ok
    assert node._config.envs == {}
    assert (await node._environment_report(rig.pack.manifest))["status"] == "missing"
    assert "envs" not in node._restart_required_fields
    assert all(report["prefix"] is None for report in node._environment_evidence.values())
    node._restart_file_server.assert_not_awaited()
    updates = [
        call.args[1] for call in node._safe_send.await_args_list if call.args[0] == "packs_updated"
    ]
    assert updates[-1].envs == {}


@pytest.mark.asyncio
async def test_manual_reload_setup_style_yaml_and_omitted_patch(rig):
    # Identical read/merge/write channel to pack_setup.apply_plans, without installing.
    raw = yaml.safe_load(rig.path.read_text())
    raw.setdefault("envs", {}).setdefault("ftgspp", rig.a)
    raw["file_server_port"] = 19999  # disk-only restart field must survive PATCH
    rig.path.write_text(yaml.safe_dump(raw, sort_keys=False))
    await rig.node._pack_reload_reply(PackReloadReq(req_id="manual"))
    await rig.node._env_reload_task
    assert rig.node._config.envs == {"ftgspp": rig.a}
    assert (await rig.node._environment_report(rig.pack.manifest))["status"] == "satisfied"
    assert (await patch(rig, {"node_name": "renamed"})).ok
    assert rig.node._config.envs == {"ftgspp": rig.a}
    assert load_node_config(rig.path).file_server_port == 19999


@pytest.mark.parametrize(
    "invalid", [None, [], {"ftgspp": None}, {"": "/env"}, {"ftgspp": ""}, {"ftgspp": 42}]
)
@pytest.mark.asyncio
async def test_invalid_candidate_rolls_back_both_channels(rig, invalid):
    assert (await patch(rig, {"envs": {"ftgspp": rig.a}})).ok
    before = rig.path.read_bytes()
    cfg = rig.node._config
    assert not (await patch(rig, {"envs": invalid, "node_name": "must-not-apply"})).ok
    assert rig.node._config is cfg and rig.path.read_bytes() == before
    raw = yaml.safe_load(before)
    raw.update(envs=invalid, node_name="must-not-apply")
    rig.path.write_text(yaml.safe_dump(raw))
    result = await rig.node.sync_config_from_disk(trigger="test")
    assert not result["ok"] and rig.node._config is cfg
    rig.path.write_bytes(before)
    assert (await rig.node.sync_config_from_disk(trigger="test"))["ok"]


@pytest.mark.parametrize("atomic", [False, True])
@pytest.mark.asyncio
async def test_watcher_real_interpreter_and_mapping_removal(rig, atomic):
    node = rig.node
    watcher = asyncio.create_task(node._config_watch_loop())
    try:
        await asyncio.sleep(0.25)
        for mapping in ({"ftgspp": rig.a}, {"ftgspp": rig.b}, {}):
            raw = yaml.safe_load(rig.path.read_text())
            raw["envs"] = mapping
            dest = rig.path.with_suffix(".replacement") if atomic else rig.path
            dest.write_text(yaml.safe_dump(raw))
            if atomic:
                os.replace(dest, rig.path)
            async with asyncio.timeout(8):
                while node._config.envs != mapping:
                    await asyncio.sleep(0.05)
            report = await node._environment_report(rig.pack.manifest)
            assert report["prefix"] == mapping.get("ftgspp")
            assert report["status"] == ("satisfied" if mapping else "missing")
        assert any(c.args[0] == "node_config_updated" for c in node._safe_send.await_args_list)
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        if node._env_reload_task:
            await node._env_reload_task


@pytest.mark.asyncio
async def test_running_real_subprocess_and_deferred_plan_keep_old_environment(rig, tmp_path):
    await patch(rig, {"envs": {"ftgspp": rig.a}})
    node = rig.node
    old_config, old_cache = node._config, node._env_cache
    started, release, result = [tmp_path / name for name in ("started", "release", "result")]
    script = "from pathlib import Path; import sys,time; " + f"Path({str(started)!r}).touch(); "
    script += f"\nwhile not Path({str(release)!r}).exists(): time.sleep(.02)\nPath({str(result)!r}).write_text(sys.prefix)"
    rendered = SimpleNamespace(shell="python -c " + shlex.quote(script), working_dir=tmp_path)
    plan = _make_exec_plan(
        rendered,
        old_config,
        old_config.envs["ftgspp"],
        rig.pack,
        cached_env=old_cache.get(rig.a),
        cpu_mask=None,
    )
    running = asyncio.create_task(
        run_subprocess(
            plan, on_log=lambda *_: None, on_progress=lambda *_: None, cancel_event=asyncio.Event()
        )
    )
    try:
        async with asyncio.timeout(8):
            while not started.exists():
                await asyncio.sleep(0.03)
        await patch(rig, {"envs": {"ftgspp": rig.b}})
        assert not running.done()
        release.touch()
        assert (await asyncio.wait_for(running, 8)).exit_code == 0
        assert result.read_text() == rig.a
        # The same frozen inputs used by _run_job after its semaphore wait.
        deferred = _make_exec_plan(
            rendered,
            old_config,
            old_config.envs["ftgspp"],
            rig.pack,
            cached_env=old_cache.get(rig.a),
            cpu_mask=None,
        )
        assert deferred.conda_prefix == rig.a
        newer = _make_exec_plan(
            rendered,
            node._config,
            node._config.envs["ftgspp"],
            rig.pack,
            cached_env=node._env_cache.get(rig.b),
            cpu_mask=None,
        )
        assert (
            await run_subprocess(
                newer,
                on_log=lambda *_: None,
                on_progress=lambda *_: None,
                cancel_event=asyncio.Event(),
            )
        ).exit_code == 0
        assert result.read_text() == rig.b
    finally:
        release.touch()
        await asyncio.gather(running, return_exceptions=True)


def test_http_patch_routes_to_real_node_handler_and_updates_gateway(rig, tmp_path, monkeypatch):
    # In-process transport only: actual HTTP route, config handler, disk and resolver.
    async def transport(app, selected, kind, payload, req_id):
        assert kind == "node_config_set_req"
        await rig.node._handle_config_set_req(payload)
        response = rig.node._send.call_args.args[1].model_dump()
        if rig.node._env_reload_task:
            await rig.node._env_reload_task
        return response

    monkeypatch.setattr("hololab.gateway.app._await_config_reply", transport)
    selected = session([rig.pack])
    with TestClient(create_app(db_path=tmp_path / "gateway.sqlite")) as client:
        client.app.state.registry._sessions["n"] = selected
        response = client.patch("/api/nodes/n/config", json={"patch": {"envs": {"ftgspp": rig.a}}})
        assert response.status_code == 200, response.text
        assert response.json()["config"]["envs"] == selected.envs == {"ftgspp": rig.a}
        assert load_node_config(rig.path).envs == selected.envs
        assert (
            client.patch("/api/nodes/n/config", json={"patch": {"envs": None}}).status_code == 400
        )
        assert selected.envs == {"ftgspp": rig.a}
        assert client.patch("/api/nodes/n/config", json={"patch": {"envs": {}}}).status_code == 200
        assert selected.envs == {}


@pytest.mark.asyncio
async def test_probe_crossing_reload_returns_current_evidence(rig):
    import threading

    await patch(rig, {"envs": {"ftgspp": rig.a}})
    checker = rig.node._capability_checker
    check = checker.check
    entered, release = threading.Event(), threading.Event()

    def paused(manifest, envs, *args, **kwargs):
        if envs.get("ftgspp") == rig.a and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return check(manifest, envs, *args, **kwargs)

    checker.check = paused
    old_probe = asyncio.create_task(rig.node._environment_report(rig.pack.manifest, fresh=True))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert (await patch(rig, {"envs": {"ftgspp": rig.b}})).ok
        release.set()
        assert (await old_probe)["prefix"] == rig.b
        assert rig.node._environment_evidence[("demo", "1.0.0")]["prefix"] == rig.b
    finally:
        release.set()
        await asyncio.gather(old_probe, return_exceptions=True)


@pytest.mark.asyncio
async def test_patch_write_failure_preserves_live_mapping(rig, monkeypatch):
    await patch(rig, {"envs": {"ftgspp": rig.a}})
    before, cfg = rig.path.read_bytes(), rig.node._config

    def fail(*args):
        raise OSError("test write denied")

    monkeypatch.setattr("hololab.node.runtime.write_node_config", fail)
    response = await patch(rig, {"envs": {"ftgspp": rig.b}})
    assert not response.ok and "test write denied" in response.error
    assert rig.node._config is cfg and rig.path.read_bytes() == before


@pytest.mark.asyncio
async def test_unrelated_patch_preserves_setup_mapping_not_yet_watched(rig):
    raw = yaml.safe_load(rig.path.read_text())
    raw["envs"] = {"ftgspp": rig.a}
    rig.path.write_text(yaml.safe_dump(raw))
    assert (await patch(rig, {"node_name": "keep-setup"})).ok
    assert rig.node._config.envs == load_node_config(rig.path).envs == {"ftgspp": rig.a}


@pytest.mark.asyncio
async def test_slow_refresh_does_not_block_patch_and_replacement_cancels_stale_refresh(rig):
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_refresh(**kwargs):
        entered.set()
        await release.wait()

    rig.node._refresh_environment_evidence = slow_refresh
    await asyncio.wait_for(
        rig.node._handle_config_set_req(
            NodeConfigSetReq(req_id="fast", patch={"envs": {"ftgspp": rig.a}})
        ),
        timeout=1,
    )
    await entered.wait()
    old_task = rig.node._env_reload_task
    assert rig.node._config.envs == {"ftgspp": rig.a} and not old_task.done()
    await rig.node._handle_config_set_req(NodeConfigSetReq(req_id="new", patch={"envs": {}}))
    await asyncio.gather(old_task, return_exceptions=True)
    assert old_task.cancelled()
    release.set()
    await rig.node._env_reload_task
    assert rig.node._config.envs == {}
