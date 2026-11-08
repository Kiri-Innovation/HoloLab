from pathlib import Path

from hololab.type_registry import TypeRegistry


def test_bundled_mirrors_legacy_tables():
    from hololab.gateway.tag_probes import _REGISTRY
    from hololab.gateway.tag_viewers import TAG_VIEWER_REGISTRY
    registry = TypeRegistry()
    for name, fn in _REGISTRY.items():
        assert registry.get(name).declaration['probe'].endswith(':' + fn.__name__)
    for name, preview in TAG_VIEWER_REGISTRY.items():
        assert registry.get(name).declaration['preview'] == preview.model_dump(exclude_none=True)


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
