from pathlib import Path

from hololab.type_registry import TypeRegistry


def test_read_batch_scans_once_and_next_operation_sees_edits(tmp_path, monkeypatch):
    import pytest
    import hololab.type_registry as types
    from hololab.manifest.data_contract import retired_tags

    directory = tmp_path / "types" / "custom"
    directory.mkdir(parents=True)
    manifest = directory / "manifest.yaml"
    manifest.write_text("name: custom\nretired_tags: [old]\n")
    registry = TypeRegistry([tmp_path], bundled=False)
    monkeypatch.setattr(types, "registry", registry)
    scans = []
    original = Path.glob

    def glob(path, pattern, **kwargs):
        if path == tmp_path / "types" and pattern == "**/*":
            scans.append(pattern)
        return original(path, pattern, **kwargs)

    monkeypatch.setattr(Path, "glob", glob)
    with registry.read_batch():
        for _ in range(100):
            assert retired_tags() == {"old"}
            with registry.read_batch():
                assert registry.get("custom")
    assert len(scans) == 1
    manifest.write_text("name: custom\nretired_tags: [new-tag]\n")
    with pytest.raises(RuntimeError), registry.read_batch():
        assert retired_tags() == {"new-tag"}
        raise RuntimeError("failed operation")
    assert len(scans) == 2
    assert registry.get("custom")
    assert len(scans) == 3  # An exception must not leave refresh suppressed.


def test_bundled_hooks_are_type_owned():
    registry = TypeRegistry()
    for definition in registry.definitions.values():
        for hook in ("summary", "check_declaration", "validate_leaf"):
            if target := definition.declaration.get(hook):
                assert target.split(":")[0].endswith(".py")
                assert (definition.directory / target.split(":")[0]).is_file()


def test_scan_typeset_order_and_reload(tmp_path):
    directory = tmp_path / "types" / "anything"
    directory.mkdir(parents=True)
    manifest = directory / "manifest.yaml"
    manifest.write_text("kind: TypeSet\ntypes:\n- name: alpha\n- name: beta\n")
    registry = TypeRegistry([tmp_path], bundled=False)
    assert list(registry.definitions) == ["alpha", "beta"]
    old = registry.generation
    manifest.write_text("kind: Type\nname: gamma\n")
    registry.reload()
    assert registry.generation != old
    assert list(registry.definitions) == ["gamma"]
    manifest.unlink()
    registry.reload()
    assert not registry.definitions


def test_summary_interface_errors_and_timeout(tmp_path):
    directory = tmp_path / "types" / "custom"
    directory.mkdir(parents=True)
    (directory / "manifest.yaml").write_text("name: custom\nsummary: summary.py:summary\n")
    source = directory / "summary.py"
    source.write_text(
        'def summary(path, storage):\n return {"counts": {"n": 3}, "storage": storage}\n'
    )
    registry = TypeRegistry([tmp_path], bundled=False)
    assert registry.get("custom").summary(Path("/missing"), "file") == {
        "counts": {"n": 3},
        "storage": "file",
    }
    source.write_text('def summary(path, storage):\n raise RuntimeError("unreadable")\n')
    assert registry.get("custom").summary(Path("/missing"), "file") is None
    source.write_text("def summary(path, storage):\n while True: pass\n")
    import time

    start = time.monotonic()
    assert registry.get("custom").summary(Path("/missing"), "file") is None
    assert time.monotonic() - start < 3


def test_live_consumer_and_cache_follow_source_edit(tmp_path, monkeypatch):
    import hololab.type_registry as types
    from hololab.gateway.handle_summary import HandleSummaryCache
    from hololab.gateway.handles import Handle
    from hololab.gateway.tag_viewers import infer_preview_for_output

    directory = tmp_path / "types" / "custom"
    directory.mkdir(parents=True)
    manifest = directory / "manifest.yaml"
    manifest.write_text("name: custom\nsummary: summary.py:summary\npreview: {viewer: text}\n")
    source = directory / "summary.py"
    source.write_text(
        'def summary(path, storage):\n return {"counts": {"count": 3, "kind": "items"}}\n'
    )
    monkeypatch.setattr(types, "registry", TypeRegistry([tmp_path], bundled=False))
    handle = Handle("hot", "node", storage="file", path=str(tmp_path / "value"), tags=["custom"])
    cache = HandleSummaryCache()
    assert cache.get_or_compute(handle, dim_labels=[])["internal_count"] == 3
    source.write_text(source.read_text().replace("3", "7"))
    assert cache.get_or_compute(handle, dim_labels=[])["internal_count"] == 7
    assert infer_preview_for_output(None, ["custom"]).viewer == "text"
    manifest.write_text(manifest.read_text().replace("viewer: text", "viewer: image"))
    assert infer_preview_for_output(None, ["custom"]).viewer == "image"


def test_type_assets_and_catalog_hot_reload(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import hololab.type_registry as types
    from hololab.gateway.app import create_app
    from hololab.gateway.tag_viewers import infer_preview_for_output

    directory = tmp_path / "types" / "custom"
    (directory / "viewer").mkdir(parents=True)
    manifest = directory / "manifest.yaml"
    manifest.write_text("name: custom\npreview: {viewer: iframe, entry: viewer/index.html}\n")
    html = directory / "viewer/index.html"
    html.write_text("<html>before</html>")
    monkeypatch.setattr(types, "registry", TypeRegistry([tmp_path], bundled=False))
    with TestClient(create_app(db_path=tmp_path / "gateway.sqlite")) as client:
        before = client.get("/api/types").json()["generation"]
        entry = infer_preview_for_output(None, ["custom"]).entry
        assert client.get(entry).text == "<html>before</html>"
        html.write_text("<html>after</html>")
        assert client.get(entry).text == "<html>after</html>"
        assert client.get("/api/types").json()["generation"] != before
        assert client.get("/api/types/custom/assets/%2e%2e/secret").status_code == 404


async def test_existing_pack_watcher_publishes_type_edits(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    import hololab.type_registry as types
    from hololab.gateway.tag_viewers import infer_preview_for_output
    from hololab.node.config import NodeConfig
    from hololab.node.runtime import NodeRuntime, _load_packs

    monkeypatch.setattr(types, "registry", TypeRegistry([tmp_path]))
    directory = tmp_path / "types/custom"
    directory.mkdir(parents=True)
    manifest = directory / "manifest.yaml"
    manifest.write_text("name: custom\npreview: {viewer: text}\n")
    runtime = NodeRuntime(NodeConfig(pack_dirs=[tmp_path], install_dir=tmp_path / "installed"))
    runtime._packs = _load_packs(runtime._config.pack_scan_dirs)
    changed = asyncio.Event()
    observed = []

    async def send(kind, payload):
        if kind == "packs_updated":
            observed.append(infer_preview_for_output(None, ["custom"]).viewer)
            changed.set()

    runtime._send = send
    runtime._refresh_environment_evidence = AsyncMock()
    task = asyncio.create_task(runtime._pack_watch_loop())
    try:
        await asyncio.sleep(0.5)
        assert infer_preview_for_output(None, ["custom"]).viewer == "text"
        manifest.write_text("name: custom\npreview: {viewer: image}\n")
        await asyncio.wait_for(changed.wait(), 8)
        assert observed[-1] == "image"
    finally:
        task.cancel()
        import contextlib

        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_durable_facts_recompute_after_type_edit(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import hololab.type_registry as types
    from hololab.gateway.app import create_app
    from hololab.gateway.handle_facts import batch, persist
    from hololab.gateway.handles import Handle

    directory = tmp_path / "types/custom"
    directory.mkdir(parents=True)
    (directory / "manifest.yaml").write_text("name: custom\nsummary: summary.py:summary\n")
    source = directory / "summary.py"
    source.write_text(
        'def summary(path, storage):\n return {"counts": {"count": 3, "kind": "items"}}\n'
    )
    monkeypatch.setattr(types, "registry", TypeRegistry([tmp_path], bundled=False))
    value = tmp_path / "value"
    value.touch()
    handle = Handle("durable-hot", "node", storage="file", path=str(value), tags=["custom"])
    with TestClient(create_app(db_path=tmp_path / "gateway.sqlite")) as client:
        client.portal.call(client.app.state.handles.register, handle)
        client.portal.call(persist, client.app, handle, [])
        first = client.portal.call(batch, client.app, [handle.handle_id])[0]
        assert first["facts"]["internal_count"] == 3
        source.write_text(source.read_text().replace("3", "7"))
        assert client.portal.call(batch, client.app, [handle.handle_id])[0]["status"] == "pending"
        client.portal.call(persist, client.app, handle, [])
        second = client.portal.call(batch, client.app, [handle.handle_id])[0]
        assert second["facts"]["internal_count"] == 7
        assert "_type_generation" not in second["facts"]


def test_summary_helpers_reload_without_stale_bytecode(tmp_path):
    import os
    import py_compile

    directory = tmp_path / "types/custom"
    directory.mkdir(parents=True)
    (directory / "manifest.yaml").write_text("name: custom\nsummary: summary.py:summary\n")
    (directory / "summary.py").write_text(
        'from helper import number\ndef summary(path, storage):\n return {"number": number}\n'
    )
    helper = directory / "helper.py"
    helper.write_text("number = 1\n")
    py_compile.compile(str(helper))
    stamp = helper.stat()
    definition = TypeRegistry([tmp_path], bundled=False).get("custom")
    assert definition.summary(tmp_path, "dir") == {"number": 1}
    helper.write_text("number = 2\n")
    os.utime(helper, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert definition.summary(tmp_path, "dir") == {"number": 2}


def test_new_type_summary_needs_no_platform_dispatch_edit(tmp_path, monkeypatch):
    import hololab.type_registry as types
    from hololab.gateway.handle_summary import summarize_handle
    from hololab.gateway.handles import Handle

    directory = tmp_path / "types/custom"
    directory.mkdir(parents=True)
    (directory / "manifest.yaml").write_text("name: custom\nsummary: summary.py:summary\n")
    (directory / "summary.py").write_text(
        'def summary(path, storage):\n return {"kind": "custom", "fields": {"format": "mine"}}\n'
    )
    monkeypatch.setattr(types, "registry", TypeRegistry([tmp_path]))
    result = summarize_handle(Handle("custom", "n", path=str(tmp_path), tags=["custom"]))
    assert result["kind"] == "custom"
    assert result["fields"] == {"format": "mine"}


def test_declarative_content_dimensions_and_invalid_scan_entries(tmp_path, monkeypatch):
    import hololab.type_registry as types
    from hololab.gateway.tag_probes import content_dim_count_for, probe_content_dims

    directory = tmp_path / "types/custom"
    directory.mkdir(parents=True)
    (directory / "manifest.yaml").write_text(
        "kind: TypeSet\ntypes:\n- name: custom\n  content_dims: {count: 1}\n  summary: summary.py:summary\n- bad-entry\n"
    )
    (directory / "summary.py").write_text(
        'def summary(path, storage):\n return {"content_dims": [7]}\n'
    )
    bad = tmp_path / "types/bad"
    bad.mkdir()
    (bad / "manifest.yaml").write_text("kind: TypeSet\ntypes: null\n")
    monkeypatch.setattr(types, "registry", TypeRegistry([tmp_path], bundled=False))
    assert list(types.registry.definitions) == ["custom"]
    assert content_dim_count_for(["custom"]) == 1
    assert probe_content_dims(["custom"], tmp_path) == [7]


async def test_gateway_pack_update_uses_changed_type_roots(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    import hololab.type_registry as types
    from hololab.gateway.app import _dispatch_node_frame
    from hololab.gateway.tag_viewers import infer_preview_for_output
    from hololab.protocol.messages import PacksUpdated

    directory = tmp_path / "new-root/types/custom"
    directory.mkdir(parents=True)
    (directory / "manifest.yaml").write_text("name: custom\npreview: {viewer: text}\n")
    monkeypatch.setattr(types, "registry", TypeRegistry([], bundled=False))
    node = SimpleNamespace(node_id="n", node_name="node", protocol_v=1, pack_dirs=[])
    nodes = SimpleNamespace(all_sessions=lambda: [node], replace_packs=AsyncMock())
    hub = SimpleNamespace(broadcast=Mock())
    await _dispatch_node_frame(
        "packs_updated",
        PacksUpdated(type_roots=[str(tmp_path / "new-root")]),
        node,
        None,
        None,
        hub,
        nodes,
        None,
        None,
    )
    assert infer_preview_for_output(None, ["custom"]).viewer == "text"
    assert hub.broadcast.call_count == 1
