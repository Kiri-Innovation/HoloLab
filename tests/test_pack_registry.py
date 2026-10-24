import json

import pytest

from hololab.content_scope import capture
from hololab.pack_publish import publish
from hololab.pack_registry import Index, RegistryError, SourceUnavailable, find_release, read_index
from tests.test_content_scope import package


def test_static_source_and_absence(tmp_path):
    (tmp_path / "index.json").write_text(Index(authority="central").model_dump_json())
    assert read_index(tmp_path.as_uri()).authority == "central"
    with pytest.raises(RegistryError, match="找不到"):
        find_release("demo@0.1.0", [tmp_path.as_uri()])
    with pytest.raises(SourceUnavailable, match="源不可达"):
        read_index((tmp_path / "absent").as_uri())
    (tmp_path / "index.json").write_text(json.dumps({"format": 99, "authority": "x"}))
    with pytest.raises(ValueError):
        read_index(tmp_path.as_uri())


def test_publish_preserves_dev_identity_and_rejects_overwrites(tmp_path):
    root, manifest, baselines = package(tmp_path)
    manifest.write_text(manifest.read_text().replace("1.0.0", "0.1.1+"))
    original = manifest.read_bytes()
    identity = capture(manifest, baselines)
    source = (tmp_path / "registry").as_uri()
    release = publish(manifest, source, "test")
    assert release.name == "kiri/demo" and release.version == "0.1.1"
    assert release.code_sha256 == identity["sha256"]
    assert manifest.read_bytes() == original
    assert read_index(source).releases["kiri/demo@0.1.1"] == release
    with pytest.raises(RegistryError, match="不允许重复"):
        publish(manifest, source, "test")
    (root / "stg_state.py").write_text("changed")
    with pytest.raises(RegistryError, match="不可变版本冲突"):
        publish(manifest, source, "test")


def test_install_checks_archive_and_content_and_is_local_first(tmp_path):
    from hololab.node.packs import scan_packs
    from hololab.pack_install import install, resolve
    from hololab.pack_registry import HashMismatch

    _, manifest, _ = package(tmp_path)
    source = (tmp_path / "registry").as_uri()
    release = publish(manifest, source, "test")
    destination = tmp_path / "installed"
    result = install("kiri/demo@1.0.0", [source], destination, [])
    assert result["code_sha256"] == release.code_sha256
    assert len(scan_packs(destination)) == 1
    offline = resolve("demo@1.0.0", [(tmp_path / "offline").as_uri()], [destination])
    assert offline["installed"] and offline["hash_verified"]
    index = tmp_path / "registry/index.json"
    value = json.loads(index.read_text())
    value["releases"]["kiri/demo@1.0.0"]["code_sha256"] = "0" * 64
    index.write_text(json.dumps(value))
    with pytest.raises(HashMismatch, match="哈希不匹配"):
        install("demo@1.0.0", [source], tmp_path / "clean", [])
    assert not (tmp_path / "clean/kiri/demo@1.0.0").exists()


@pytest.mark.asyncio
async def test_yank_and_dev_notes_never_modify_snapshot(tmp_path):
    from hololab.gateway.workflows import GraphNode, WorkflowGraph, WorkflowStore
    from hololab.pack_install import install, resolve
    from hololab.pack_publish import set_yank
    from hololab.persistence.db import open_database
    from hololab.publication_notes import annotate, notes_for

    _, manifest, baselines = package(tmp_path)
    manifest.write_text(manifest.read_text().replace("1.0.0", "0.1.1+"))
    identity = capture(manifest, baselines)
    source = (tmp_path / "registry").as_uri()
    released = publish(manifest, source, "tester")
    installed = tmp_path / "installed"
    install("demo@0.1.1", [source], installed, [])
    yanked = set_yank("demo@0.1.1", source, True, "regression")
    assert set_yank("demo@0.1.1", source, True, "regression") == yanked
    with pytest.raises(RegistryError, match="拒绝新安装"):
        install("demo@0.1.1", [source], tmp_path / "clean", [])
    assert resolve("demo@0.1.1", [source], [installed], released.code_sha256)["installed"]
    assert find_release("demo@0.1.1", [source], released.code_sha256)[1].yanked
    db = await open_database(tmp_path / "snapshots.sqlite")
    try:
        store = WorkflowStore(db)
        graph = WorkflowGraph(
            nodes=[
                GraphNode(
                    id="dev",
                    algorithm_name="demo",
                    algorithm_version="0.1.1+",
                    code_identity=identity,
                )
            ]
        )
        draft = await store.save_draft(workflow_id=None, name="dev", graph=graph)
        snap = await store.create_snapshot(workflow_id=draft.workflow_id, graph=graph)
        async with db.read() as conn:
            before = await (await conn.execute("SELECT graph_json FROM snapshots")).fetchall()
        assert await annotate(db, released, source) == 1
        assert await annotate(db, released, source) == 0
        notes = await notes_for(db, snap.snapshot_id)
        assert notes["dev"][0]["version"] == "0.1.1"
        assert notes["dev"][0]["code_sha256"] == identity["sha256"]
        async with db.read() as conn:
            assert (
                before == await (await conn.execute("SELECT graph_json FROM snapshots")).fetchall()
            )
    finally:
        await db.close()
    unyanked = set_yank("demo@0.1.1", source, False)
    assert not unyanked.yanked
    assert unyanked.code_sha256 == released.code_sha256
    assert set_yank("demo@0.1.1", source, False) == unyanked


def test_cli_end_to_end_real_process_and_offline_yank(tmp_path):
    import asyncio
    import os
    import sys
    from unittest.mock import AsyncMock

    import yaml
    from typer.testing import CliRunner

    from hololab.cli import app
    from hololab.node.config import NodeConfig
    from hololab.node.packs import scan_packs
    from hololab.node.runtime import NodeRuntime
    from hololab.protocol.messages import JobAssign

    root, manifest, _ = package(tmp_path)
    data = yaml.safe_load(manifest.read_text())
    data.update(name="kiri/registry-echo", version="0.0.1+")
    data["exec"]["shell"] = 'python "{{ package_root }}/stg_state.py" "{{ outputs.out }}"'
    manifest.write_text(yaml.safe_dump(data))
    (root / "stg_state.py").write_text(
        "import pathlib, sys\np = pathlib.Path(sys.argv[1]); p.mkdir(parents=True, exist_ok=True)\n"
        "(p / 'result.txt').write_text('REGISTRY_E2E_OK')\n"
    )
    source = (tmp_path / "registry").as_uri()
    destination = tmp_path / "installed"
    config = tmp_path / "node.yaml"
    config.write_text(yaml.safe_dump({"pack_dirs": [str(destination)], "pack_sources": [source]}))
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "pack",
            "publish",
            str(manifest.parent),
            "--source",
            source,
            "--publisher",
            "e2e",
            "--snapshot-db",
            str(tmp_path / "absent.sqlite"),
        ],
    )
    assert result.exit_code == 0, result.output
    released = read_index(source).releases["kiri/registry-echo@0.0.1"]
    result = runner.invoke(
        app, ["pack", "install", "kiri/registry-echo@0.0.1", "--config", str(config)]
    )
    assert result.exit_code == 0, result.output
    pack = scan_packs(destination)[0]

    async def run(job):
        node = NodeRuntime(
            NodeConfig(
                workspace_root=tmp_path / job,
                envs={"test": sys.prefix},
                pack_sources=[(tmp_path / "offline").as_uri()],
            )
        )
        node._node_id = "registry-test"
        node._packs = {(pack.manifest.name, pack.manifest.version): pack}
        node._env_cache[sys.prefix] = dict(os.environ)
        node._send = AsyncMock()
        node._safe_send = AsyncMock()
        node._send_job_fail = AsyncMock()
        await node._run_job(
            JobAssign(
                job_id=job,
                workflow_id="w",
                algorithm_name=pack.manifest.name,
                algorithm_version=pack.manifest.version,
            ),
            asyncio.Event(),
        )
        node._send_job_fail.assert_not_called()
        assert any(c.args[0] == "job_done" for c in node._send.call_args_list)
        results = list((tmp_path / job).rglob("result.txt"))
        assert len(results) == 1 and results[0].read_text() == "REGISTRY_E2E_OK"

    asyncio.run(run("before-yank"))
    result = runner.invoke(app, ["pack", "yank", "kiri/registry-echo@0.0.1", "--source", source])
    assert result.exit_code == 0, result.output
    asyncio.run(run("after-yank"))
    print(
        f"REGISTRY_E2E hash={released.code_sha256} archive_bytes={released.archive_bytes} real_jobs=2"
    )


def test_http_mirror_and_archive_traversal_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    import io
    import tarfile
    import threading
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    from hololab.pack_install import extract_verified, install

    _, manifest, _ = package(tmp_path)
    registry = tmp_path / "registry"
    publish(manifest, registry.as_uri(), "test")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(registry))
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        result = install(
            "demo@1.0.0", [f"http://127.0.0.1:{server.server_port}/"], tmp_path / "installed", []
        )
        assert result["hash_verified"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("../escaped")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ValueError):
        extract_verified(archive, tmp_path / "content", 1)
    assert not (tmp_path / "escaped").exists()


def test_status_refresh_and_local_tampering_hard_stop(tmp_path):
    from pathlib import Path

    from hololab.pack_install import install, resolve, status
    from hololab.pack_publish import set_yank
    from hololab.pack_registry import HashMismatch

    _, manifest, _ = package(tmp_path)
    source = (tmp_path / "registry").as_uri()
    publish(manifest, source, "test")
    destination = tmp_path / "installed"
    installed = install("demo@1.0.0", [source], destination, [])
    assert status("demo@1.0.0", [source], [destination])["registry_status"] == "published"
    set_yank("demo@1.0.0", source, True)
    assert status("demo@1.0.0", [source], [destination])["registry_status"] == "yanked"
    assert status("demo@1.0.0", [], [destination])["registry_status"] == "unknown"
    (Path(installed["manifest"]).parents[2] / "stg_state.py").write_text("print('tampered')")
    with pytest.raises(HashMismatch, match="必须停止"):
        resolve("demo@1.0.0", [], [destination])


def test_status_endpoint_requires_new_node_and_forwards_result(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from hololab.gateway.app import create_app
    from hololab.node.packs import scan_packs
    from tests.test_scoped_packages import session

    _, manifest, _ = package(tmp_path)

    async def reply(*args, **kwargs):
        assert kwargs["pending_key"] == "pending_pack_status"
        return {"result": {"state": "found", "registry_status": "yanked"}}

    monkeypatch.setattr("hololab.gateway.app._await_node_reply", reply)
    with TestClient(create_app(db_path=tmp_path / "gateway.sqlite")) as client:
        node = session(scan_packs(manifest))
        client.app.state.registry._sessions["n"] = node
        url = "/api/nodes/n/pack-status?name=demo&version=1.0.0"
        assert client.get(url).status_code == 409
        node.registry_v1 = True
        assert client.get(url).json()["registry_status"] == "yanked"
