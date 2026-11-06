"""Camera view contracts: exact records, selection, compatibility and conflicts."""
import importlib.util
import json
import shutil
import struct
from pathlib import Path

import colmap4d
from colmap4d import colmap_io as cio
import pytest
import yaml
from hololab.manifest.schema import Manifest

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / 'community_packs/colmap4d-cams-to-array@0.1.0'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


view = load('camera_view', PACK / 'convert.py')
pick = load('camera_pick', ROOT / 'hololab/builtin_packs/interval-pick@0.1.0/interval_pick.py')


def fixture(root):
    root.mkdir()
    cio.write_cameras_bin(root/'cameras.bin', {
        7: cio.Camera(7, 'PINHOLE', 640, 480, [500, 510, 320, 240]),
        12: cio.Camera(12, 'SIMPLE_PINHOLE', 640, 480, [500, 320, 240])})
    cio.write_images_bin(root/'images.bin', {
        9: cio.Image(9, 'nine.png', 7, [1,0,0,0], [1,2,3], [(1.,2.)], [123]),
        2: cio.Image(2, 'two.png', 7, [1,0,0,0], [4,5,6]),
        5: cio.Image(5, 'five.png', 7, [1,0,0,0], [7,8,9])})
    colmap4d.write_times_bin(root/'times.bin', {5:2**63-1, 9:-2**63, 2:-2**63})
    (root/'time_meta.json').write_text('{"time_unit": "ns", "clock_domain": "test"}\n')
    # Deliberately unsorted physical table order, independent of library writers.
    for kind, order in [('cameras',[12,7]), ('images',[9,5,2]), ('times',[5,2,9])]:
        path = root/(kind+'.bin')
        rows = view.records(path.read_bytes(), kind)
        path.write_bytes(struct.pack('<Q',len(order))+b''.join(rows[i] for i in order))


def test_schema():
    for direction in ('to', 'from'):
        pack = ROOT / f'community_packs/colmap4d-cams-{direction}-array@0.1.0'
        m = Manifest.model_validate(yaml.safe_load((pack/'manifest.yaml').read_text()))
        array = m.outputs['cameras'] if direction == 'to' else m.inputs['cameras']
        assert array.arrayed and array.dim_labels == ['camera'] and array.tags == ['colmap4d-single-cam']
    assert (PACK/'convert.py').read_bytes() == (ROOT/'community_packs/colmap4d-cams-from-array@0.1.0/convert.py').read_bytes()


def test_exact_roundtrip_selection_and_timed_leaves(tmp_path):
    source = tmp_path/'source'
    fixture(source)
    array, restored = tmp_path/'array', tmp_path/'restored'
    assert view.to_array(source, array) == {'images':3}
    evidence = json.loads((array/view.SIDECAR).read_text())
    assert [v['image_id'] for v in evidence['entries'].values()] == [2,9,5]
    for p in array.glob('*.json'):
        if not p.name.startswith('.'):
            data = json.loads(p.read_text())
            assert set(data) == {'model','width','height','params','qvec','tvec','timestamp_ns'}
            assert data['timestamp_ns'] == evidence['entries'][p.name]['timestamp_ns']
            assert type(data['timestamp_ns']) is int
    view.from_array(array, restored)
    for p in source.iterdir():
        assert p.read_bytes() == (restored/p.name).read_bytes()
    pick.pick(array, tmp_path/'picked', interval=2)
    # Repeated identities deduplicate even when links are renamed.
    (tmp_path/'picked/duplicate.json').symlink_to(next((tmp_path/'picked').iterdir()).resolve())
    view.from_array(tmp_path/'picked', tmp_path/'subset')
    assert set(cio.read_images_bin(tmp_path/'subset/images.bin')) == {2,5}
    assert colmap4d.read_times_bin(tmp_path/'subset/times.bin') == {2:-2**63, 5:2**63-1}
    shutil.rmtree(source)
    shutil.rmtree(array)
    assert set(cio.read_images_bin(tmp_path/'subset/images.bin')) == {2,5}


@pytest.mark.parametrize('mutation', ['leaf', 'missing', 'mixed', 'empty'])
def test_rejects_ambiguous_restore(tmp_path, mutation):
    fixture(tmp_path/'source')
    view.to_array(tmp_path/'source', tmp_path/'array')
    leaf = tmp_path/'array/p_0000000000000000.json'
    if mutation == 'leaf':
        leaf.write_text('{}')
    elif mutation == 'missing':
        (tmp_path/'array'/view.SIDECAR).unlink()
    elif mutation == 'mixed':
        fixture(tmp_path/'other')
        (tmp_path/'other/time_meta.json').write_text('{"time_unit":"ns","clock_domain":"other"}')
        view.to_array(tmp_path/'other', tmp_path/'other_array')
        (tmp_path/'array/foreign.json').symlink_to(tmp_path/'other_array/p_0000000000000000.json')
    else:
        for p in (tmp_path/'array').glob('p_*.json'):
            p.unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        view.from_array(tmp_path/'array', tmp_path/'out')
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('mutation', ['times', 'trailing', 'truncated'])
def test_rejects_invalid_component(tmp_path, mutation):
    fixture(tmp_path/'source')
    if mutation == 'times':
        colmap4d.write_times_bin(tmp_path/'source/times.bin', {9:0})
    else:
        p = tmp_path/'source/images.bin'
        p.write_bytes(p.read_bytes()+b'x' if mutation == 'trailing' else p.read_bytes()[:-1])
    with pytest.raises(ValueError):
        view.to_array(tmp_path/'source', tmp_path/'array')
    assert not (tmp_path/'array').exists()


def test_portable_array_and_archive_integrity(tmp_path):
    fixture(tmp_path/'source')
    view.to_array(tmp_path/'source', tmp_path/'array')
    shutil.copytree(tmp_path/'array', tmp_path/'copy')
    shutil.rmtree(tmp_path/'array')
    view.from_array(tmp_path/'copy', tmp_path/'restored')
    for p in (tmp_path/'source').iterdir():
        assert p.read_bytes() == (tmp_path/'restored'/p.name).read_bytes()
    archive = tmp_path/'copy/.camera-view/images.bin'
    raw = bytearray(archive.read_bytes())
    raw[-1] ^= 1
    archive.write_bytes(raw)
    with pytest.raises(ValueError, match='archive was modified'):
        view.from_array(tmp_path/'copy', tmp_path/'bad')


@pytest.mark.parametrize('bad', [True, 1.0, '1', None, -(2**63)-1, 2**63])
def test_invalid_leaf_timestamp(tmp_path, bad):
    fixture(tmp_path/'source')
    view.to_array(tmp_path/'source', tmp_path/'array')
    path = tmp_path/'array/p_0000000000000000.json'
    data = json.loads(path.read_text())
    data['timestamp_ns'] = bad
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='signed int64'):
        view.from_array(tmp_path/'array', tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_restore_reads_leaf_time_not_sidecar_snapshot(tmp_path):
    fixture(tmp_path/'source')
    view.to_array(tmp_path/'source', tmp_path/'array')
    path = tmp_path/'array'/view.SIDECAR
    evidence = json.loads(path.read_text())
    for entry in evidence['entries'].values():
        entry['timestamp_ns'] = 123
    evidence['tables']['times'] = [[iid, view.encode(struct.pack('<Iq', iid, 123))]
                                   for iid, raw in evidence['tables']['times']]
    path.write_text(json.dumps(evidence))
    view.from_array(tmp_path/'array', tmp_path/'out')
    assert (tmp_path/'source/times.bin').read_bytes() == (tmp_path/'out/times.bin').read_bytes()
