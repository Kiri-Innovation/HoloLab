"""Repeat-4d contract, preparation and real COLMAP reader interoperability."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
import colmap4d
from colmap4d import colmap_io as cio
from colmap4d.arrays import read_image_headers
from colmap4d.validate import validate, exit_code
from hololab.manifest.schema import Manifest
from hololab.gateway.workflows import WorkflowGraph
from hololab.gateway.preparation import resolve_recipe

PACK = Path(__file__).parents[1] / 'community_packs/repeat-4d@0.1.0'
spec = importlib.util.spec_from_file_location('repeat_4d', PACK/'repeat_4d.py')
repeat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repeat)
CAMERA = dict(model='PINHOLE', width=640, height=480, params=[500,510,320,240],
              qvec=[0.5,0.5,0.5,0.5], tvec=[1,-2,3])


def manifest():
    return Manifest.model_validate(yaml.safe_load((PACK/'manifest.yaml').read_text()))


def test_schema_and_ports():
    m = manifest()
    assert m.inputs['camera'].tags == ['colmap-camera']
    assert m.outputs['4d_cams'].tags == ['colmap4d-cams']
    assert not m.outputs['4d_cams'].arrayed


@pytest.mark.parametrize('name', ['duration', 'fps'])
@pytest.mark.parametrize('bad', [0, -1, float('nan'), float('inf'), -float('inf'), True, '30'])
def test_prepare_rejects(name, bad):
    m = manifest().model_dump(mode='json', by_alias=True)
    graph = WorkflowGraph(nodes=[dict(id='repeat', algorithm_name=m['name'], algorithm_version=m['version'],
                                     params={name:bad}, position=dict(x=0,y=0))], edges=[])
    with pytest.raises(ValueError, match=name):
        resolve_recipe(graph, [m])


@pytest.mark.parametrize('duration,fps,expected', [(1,30,30), (0.29,100,29), (1.05,30,31), (1/30,30,None), (1e-9,1e9,1), (1e6,1,1000000)])
def test_count_policy(duration, fps, expected):
    if expected is None:
        with pytest.raises(ValueError, match='frames'):
            repeat.schedule(duration, fps)
    else:
        assert repeat.schedule(duration, fps)[0] == expected


@pytest.mark.parametrize('duration,fps,start', [(1e-300,30,0), (1e308,1e308,0), (1000001,1,0),
    (1,1e9+1,0), (2,1,2**63-1), (1,1,-2**63-1), (1,1,1.5), (0,30,0), (1,float('nan'),0)])
def test_extremes(duration, fps, start):
    with pytest.raises(ValueError):
        repeat.schedule(duration, fps, start)


@pytest.mark.parametrize('field,value', [('model','UNKNOWN'), ('width',0), ('height',True), ('params',[1]),
    ('params',[-1,1,1,1]), ('params',[1,0,1,1]), ('qvec',[0,0,0,0]), ('tvec',[0,0,float('inf')])])
def test_invalid_camera(tmp_path, field, value):
    path = tmp_path/'camera.json'
    path.write_text(json.dumps({**CAMERA, field:value}))
    with pytest.raises(ValueError):
        repeat.repeat(path, tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_cli_reader_and_validation(tmp_path):
    source, out = tmp_path/'camera.json', tmp_path/'cams'
    source.write_text(json.dumps(CAMERA))
    before = source.read_bytes()
    start = 1_000_000_000_000_000_001
    subprocess.run([sys.executable, str(PACK/'repeat_4d.py'), '--camera', str(source), '--output', str(out),
                    '--duration', '1.05', '--fps', '30', '--start-time-ns', str(start)], check=True)
    assert source.read_bytes() == before
    cameras = cio.read_cameras_bin(out/'cameras.bin')
    images = read_image_headers(out/'images.bin')
    times = colmap4d.read_times_bin(out/'times.bin')
    assert len(images) == 31 and set(images) == set(times)
    assert len(cameras) == 1 and cameras[1].params == CAMERA['params']
    assert len({im.name for im in images.values()}) == 31
    assert all(im.camera_id == 1 and im.qvec == CAMERA['qvec'] and im.tvec == CAMERA['tvec'] for im in images.values())
    assert times[1] == start and times[2] == start+33333333 and times[3] == start+66666667
    assert times[31] == start+1000000000
    assert all(not im.xys for im in cio.read_images_bin(out/'images.bin').values())
    assert exit_code(validate(out, image_ids=set(images)), strict=True) == 0
    assert set(p.name for p in out.iterdir()) == {'cameras.bin','images.bin','times.bin','time_meta.json'}
    assert json.loads((out/'time_meta.json').read_text())['conversion']['synthetic'] is True


def test_ties_even_and_int64_edges(tmp_path):
    source = tmp_path/'camera.json'
    source.write_text(json.dumps(CAMERA))
    repeat.repeat(source, tmp_path/'out', 1e-8, 400000000, -2**63)
    times = colmap4d.read_times_bin(tmp_path/'out/times.bin')
    assert list(times.values()) == [-2**63+i for i in (0,2,5,8)]
    assert repeat.schedule(1,1,2**63-1)[0] == 1
