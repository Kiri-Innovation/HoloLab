from pathlib import Path

from hololab.type_registry import TypeRegistry


def test_bundled_hooks_are_type_owned():
    registry = TypeRegistry()
    for definition in registry.definitions.values():
        for hook in ('summary', 'check_declaration', 'validate_leaf'):
            if target := definition.declaration.get(hook):
                assert target.split(':')[0].endswith('.py')
                assert (definition.directory / target.split(':')[0]).is_file()


def test_scan_typeset_order_and_reload(tmp_path):
    directory = tmp_path / 'types' / 'anything'
    directory.mkdir(parents=True)
    manifest = directory / 'manifest.yaml'
    manifest.write_text('kind: TypeSet\ntypes:\n- name: alpha\n- name: beta\n')
    registry = TypeRegistry([tmp_path], bundled=False)
    assert list(registry.definitions) == ['alpha', 'beta']
    old = registry.generation
    manifest.write_text('kind: Type\nname: gamma\n')
    registry.reload()
    assert registry.generation != old
    assert list(registry.definitions) == ['gamma']
    manifest.unlink()
    registry.reload()
    assert not registry.definitions


def test_summary_interface_errors_and_timeout(tmp_path):
    directory = tmp_path / 'types' / 'custom'
    directory.mkdir(parents=True)
    (directory / 'manifest.yaml').write_text('name: custom\nsummary: summary.py:summary\n')
    source = directory / 'summary.py'
    source.write_text('def summary(path, storage):\n return {"counts": {"n": 3}, "storage": storage}\n')
    registry = TypeRegistry([tmp_path], bundled=False)
    assert registry.get('custom').summary(Path('/missing'), 'file') == {'counts': {'n': 3}, 'storage': 'file'}
    source.write_text('def summary(path, storage):\n raise RuntimeError("unreadable")\n')
    assert registry.get('custom').summary(Path('/missing'), 'file') is None
    source.write_text('def summary(path, storage):\n while True: pass\n')
    import time
    start = time.monotonic()
    assert registry.get('custom').summary(Path('/missing'), 'file') is None
    assert time.monotonic() - start < 3


def test_live_consumer_and_cache_follow_source_edit(tmp_path, monkeypatch):
    import hololab.type_registry as types
    from hololab.gateway.handles import Handle
    from hololab.gateway.handle_summary import HandleSummaryCache
    from hololab.gateway.tag_viewers import infer_preview_for_output
    directory = tmp_path / 'types' / 'custom'
    directory.mkdir(parents=True)
    manifest = directory / 'manifest.yaml'
    manifest.write_text('name: custom\nsummary: summary.py:summary\npreview: {viewer: text}\n')
    source = directory / 'summary.py'
    source.write_text('def summary(path, storage):\n return {"counts": {"count": 3, "kind": "items"}}\n')
    monkeypatch.setattr(types, 'registry', TypeRegistry([tmp_path], bundled=False))
    handle = Handle('hot', 'node', storage='file', path=str(tmp_path / 'value'), tags=['custom'])
    cache = HandleSummaryCache()
    assert cache.get_or_compute(handle, dim_labels=[])['internal_count'] == 3
    source.write_text(source.read_text().replace('3', '7'))
    assert cache.get_or_compute(handle, dim_labels=[])['internal_count'] == 7
    assert infer_preview_for_output(None, ['custom']).viewer == 'text'
    manifest.write_text(manifest.read_text().replace('viewer: text', 'viewer: image'))
    assert infer_preview_for_output(None, ['custom']).viewer == 'image'


def test_type_assets_and_catalog_hot_reload(tmp_path, monkeypatch):
    import hololab.type_registry as types
    from fastapi.testclient import TestClient
    from hololab.gateway.app import create_app
    from hololab.gateway.tag_viewers import infer_preview_for_output
    directory = tmp_path / 'types' / 'custom'
    (directory / 'viewer').mkdir(parents=True)
    manifest = directory / 'manifest.yaml'
    manifest.write_text('name: custom\npreview: {viewer: iframe, entry: viewer/index.html}\n')
    html = directory / 'viewer/index.html'
    html.write_text('<html>before</html>')
    monkeypatch.setattr(types, 'registry', TypeRegistry([tmp_path], bundled=False))
    with TestClient(create_app(db_path=tmp_path / 'gateway.sqlite')) as client:
        before = client.get('/api/types').json()['generation']
        entry = infer_preview_for_output(None, ['custom']).entry
        assert client.get(entry).text == '<html>before</html>'
        html.write_text('<html>after</html>')
        assert client.get(entry).text == '<html>after</html>'
        assert client.get('/api/types').json()['generation'] != before
        assert client.get('/api/types/custom/assets/%2e%2e/secret').status_code == 404


async def test_existing_pack_watcher_publishes_type_edits(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    import hololab.type_registry as types
    from hololab.node.config import NodeConfig
    from hololab.node.runtime import NodeRuntime, _load_packs
    from hololab.gateway.tag_viewers import infer_preview_for_output
    monkeypatch.setattr(types, 'registry', TypeRegistry([tmp_path]))
    directory = tmp_path / 'types/custom'
    directory.mkdir(parents=True)
    manifest = directory / 'manifest.yaml'
    manifest.write_text('name: custom\npreview: {viewer: text}\n')
    runtime = NodeRuntime(NodeConfig(pack_dirs=[tmp_path], install_dir=tmp_path / 'installed'))
    runtime._packs = _load_packs(runtime._config.pack_scan_dirs)
    changed = asyncio.Event()
    observed = []
    async def send(kind, payload):
        if kind == 'packs_updated':
            observed.append(infer_preview_for_output(None, ['custom']).viewer)
            changed.set()
    runtime._send = send
    runtime._refresh_environment_evidence = AsyncMock()
    task = asyncio.create_task(runtime._pack_watch_loop())
    try:
        await asyncio.sleep(.5)
        assert infer_preview_for_output(None, ['custom']).viewer == 'text'
        manifest.write_text('name: custom\npreview: {viewer: image}\n')
        await asyncio.wait_for(changed.wait(), 8)
        assert observed[-1] == 'image'
    finally:
        task.cancel()
        import contextlib
        with contextlib.suppress(asyncio.CancelledError):
            await task
