from unittest.mock import patch

from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.node.config import NodeConfig
from hololab.node.packs import LoadedPack, scan_multi_packs
from hololab.node.runtime import NodeRuntime
from tests.test_pack_dirs import _write_manifest
from tests.test_scoped_packages import session


def test_locations_preserve_priority_deduplicate_and_do_not_hash_content(tmp_path):
    first, second = tmp_path / 'installed', tmp_path / 'source'
    for root in (first, second):
        _write_manifest(root / 'demo', 'demo', '1.0.0')
    manifest = second / 'demo/manifest.yaml'
    manifest.write_text(manifest.read_text().replace('test pack', 'different'))
    alias = tmp_path / 'alias'
    alias.symlink_to(first, target_is_directory=True)
    with patch.object(LoadedPack, 'content', property(lambda self: (_ for _ in ()).throw(AssertionError('content walk')))):
        packs = scan_multi_packs([first, second, alias, first / 'demo/manifest.yaml'])
        assert len(packs) == 1
        locations = packs[0].locations()
    assert packs[0].source_dir == first
    assert [p['effective'] for p in locations] == [True, False]
    assert len({p['manifest_hash'] for p in locations}) == 2
    assert all(p['code_sha256'] is None for p in locations)
    assert locations[1]['pack_dir'] == str(second / 'demo')
    assert len(scan_multi_packs([second])[0].locations()) == 1
    assert scan_multi_packs([second, first])[0].source_dir == second


def test_inventory_catalog_locations_are_per_node(tmp_path):
    first, second = tmp_path / 'installed', tmp_path / 'source'
    for root in (first, second):
        _write_manifest(root / 'demo', 'demo', '1.0.0')
    runtime = NodeRuntime(NodeConfig(pack_dirs=[second], install_dir=first))
    runtime._packs = {(p.manifest.name, p.manifest.version): p for p in scan_multi_packs([first, second])}
    node = session([])
    node.packs = runtime._pack_inventory()
    with TestClient(create_app(db_path=tmp_path / 'gateway.sqlite')) as client:
        client.app.state.registry._sessions['n'] = node
        row = client.get('/api/pack-catalog').json()[0]
        assert row['source_dir'] == str(first)
        assert row['content']['root_abs'] == str(first / 'demo')
        assert [p['effective'] for p in row['locations_by_node']['n']] == [True, False]
        node.packs[0].locations = []  # old node remains compatible
        assert client.get('/api/pack-catalog').json()[0]['locations_by_node'] == {'n': []}
