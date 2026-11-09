"""Reference timeline semantics, metadata preservation and CLI interoperability."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import colmap4d
import pytest
from colmap4d import colmap_io as cio

from hololab.manifest import load_manifest

PACK = Path(__file__).parents[1] / 'community_packs/repeat-into-4d@0.2.0'
spec = importlib.util.spec_from_file_location('repeat_reference', PACK / 'repeat_into_4d.py')
repeat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repeat)
CAMERA = dict(model='PINHOLE', width=640, height=480, params=[500, 510, 320, 240],
              qvec=[1, 0, 0, 0], tvec=[1, 2, 3])


@pytest.fixture
def inputs(tmp_path):
    camera = tmp_path / 'camera.json'
    camera.write_text(json.dumps(CAMERA))
    ref = tmp_path / 'reference'
    ref.mkdir()
    # Reference geometry is deliberately absent: only the time layer is used.
    colmap4d.write_times_bin(ref / 'times.bin', {7: 2**63-1, 3: -2**63, 9: 42, 11: 42})
    (ref / 'time_meta.json').write_text(json.dumps(dict(
        colmap4d_spec='1.0', time_unit='ns', time_convention='mid_exposure',
        clock_domain='capture-clock', custom={'preserved': [1, 'x']})))
    return camera, ref


def test_manifest():
    m, _ = load_manifest(PACK / 'manifest.yaml')
    assert m.arrayable and m.batch_size == 1 and not m.params
    assert m.inputs['timestamp_cameras'].scalar
    assert m.inputs['timestamp_cameras'].tags == ['colmap4d-cams']
    assert not m.inputs['camera'].scalar
    assert m.inputs['camera'].tags == ['colmap-single-cam']


@pytest.mark.parametrize('text', [False, True])
def test_cli_timeline_and_geometry(inputs, tmp_path, text):
    camera, ref = inputs
    if text:
        colmap4d.write_times_txt(ref / 'times.txt', colmap4d.read_times_bin(ref / 'times.bin'))
        (ref / 'times.bin').unlink()
    out = tmp_path / 'out'
    subprocess.run([sys.executable, str(PACK / 'repeat_into_4d.py'), '--camera', str(camera),
                    '--timestamp-cameras', str(ref), '--output', str(out)], check=True)
    assert list(colmap4d.read_times_bin(out / 'times.bin').values()) == [-2**63, 42, 2**63-1]
    assert (out / 'time_meta.json').read_bytes() == (ref / 'time_meta.json').read_bytes()
    images = cio.read_images_bin(out / 'images.bin')
    assert len(images) == 3 and len({i.name for i in images.values()}) == 3
    assert all(i.tvec == CAMERA['tvec'] and i.qvec == CAMERA['qvec'] and not i.xys for i in images.values())
    assert cio.read_cameras_bin(out / 'cameras.bin')[1].params == CAMERA['params']


@pytest.mark.parametrize('bad', ['empty', 'missing_meta', 'missing_times', 'clock', 'unit', 'timed_camera', 'duplicate_id'])
def test_invalid_reference_before_output(inputs, tmp_path, bad):
    camera, ref = inputs
    if bad == 'empty':
        colmap4d.write_times_bin(ref / 'times.bin', {})
    elif bad == 'missing_meta':
        (ref / 'time_meta.json').unlink()
    elif bad == 'missing_times':
        (ref / 'times.bin').unlink()
    elif bad == 'duplicate_id':
        import struct
        (ref / 'times.bin').write_bytes(struct.pack('<QIqIq', 2, 1, 0, 1, 5))
    elif bad == 'timed_camera':
        camera.write_text(json.dumps({**CAMERA, 'timestamp_ns': 1}))
    else:
        meta = json.loads((ref / 'time_meta.json').read_text())
        meta['clock_domain' if bad == 'clock' else 'time_unit'] = ''
        (ref / 'time_meta.json').write_text(json.dumps(meta))
    with pytest.raises((ValueError, OSError)):
        repeat.repeat(camera, ref, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()
