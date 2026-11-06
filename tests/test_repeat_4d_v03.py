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

PACK = Path(__file__).parents[1] / 'community_packs/repeat-4d@0.3.0'
spec = importlib.util.spec_from_file_location('repeat_4d', PACK/'repeat_4d.py')
repeat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repeat)
CAMERA = dict(model='PINHOLE', width=640, height=480, params=[500,510,320,240],
              qvec=[0.5,0.5,0.5,0.5], tvec=[1,-2,3], timestamp_ns=123456789)


def manifest():
    return Manifest.model_validate(yaml.safe_load((PACK/'manifest.yaml').read_text()))


def test_schema_and_ports():
    m = manifest()
    assert m.arrayable and m.parallelism == 4 and m.batch_size == 1
    assert not m.inputs['camera'].arrayed
    assert m.inputs['camera'].tags == ['colmap4d-single-cam']
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


CONVERTER = PACK.parent / 'single-cam-drop-time@0.1.0'
converter_spec = importlib.util.spec_from_file_location('drop_time', CONVERTER / 'drop_time.py')
converter = importlib.util.module_from_spec(converter_spec)
converter_spec.loader.exec_module(converter)


@pytest.mark.parametrize('override,origin', [({}, CAMERA['timestamp_ns']),
    ({'start_time_ns': None}, CAMERA['timestamp_ns']), ({'start_time_ns': 0}, 0),
    ({'start_time_ns': -123}, -123)])
def test_prepared_rendered_cli_origin(tmp_path, override, origin):
    import shlex
    from hololab.manifest.render import RenderContext, render_manifest
    m = manifest()
    graph = WorkflowGraph(nodes=[dict(id='repeat', algorithm_name=m.name,
        algorithm_version=m.version, params={'duration': 1, 'fps': 3, **override})], edges=[])
    prepared = resolve_recipe(graph, [m.model_dump(mode='json', by_alias=True)])
    source, out = tmp_path / 'camera.json', tmp_path / 'out'
    source.write_text(json.dumps(CAMERA))
    rendered = render_manifest(m, RenderContext(inputs={'camera': str(source)},
        outputs={'4d_cams': str(out)}, params=prepared.nodes[0].params, pack_dir=str(PACK)))
    argv = shlex.split(rendered.shell)
    assert ('--start-time-ns' in argv) == (override.get('start_time_ns') is not None)
    subprocess.run([sys.executable, *argv[1:]], check=True)
    assert list(colmap4d.read_times_bin(out / 'times.bin').values()) == [
        origin, origin + 333333333, origin + 666666667]


@pytest.mark.parametrize('bad', [None, True, 1.0, '1', -2**63-1, 2**63])
@pytest.mark.parametrize('loader', [repeat.load_camera, converter.load_camera])
def test_timestamp_is_required_signed_int64(tmp_path, bad, loader):
    source = tmp_path / 'camera.json'
    source.write_text(json.dumps({**CAMERA, 'timestamp_ns': bad}))
    with pytest.raises(ValueError, match='timestamp_ns'):
        loader(source)


@pytest.mark.parametrize('loader', [repeat.load_camera, converter.load_camera])
def test_missing_time_and_extra_fields_rejected(tmp_path, loader):
    for data in ({k:v for k,v in CAMERA.items() if k != 'timestamp_ns'},
                 {**CAMERA, 'unknown': 1}):
        source = tmp_path / 'camera.json'
        source.write_text(json.dumps(data))
        with pytest.raises(ValueError, match='exactly'):
            loader(source)


@pytest.mark.parametrize('timestamp', [-2**63, 2**63-1])
def test_default_origin_int64_edges_and_overflow(tmp_path, timestamp):
    source = tmp_path / 'camera.json'
    source.write_text(json.dumps({**CAMERA, 'timestamp_ns': timestamp}))
    repeat.repeat(source, tmp_path / 'one', duration=1, fps=1)
    assert colmap4d.read_times_bin(tmp_path / 'one/times.bin')[1] == timestamp
    if timestamp == 2**63-1:
        with pytest.raises(ValueError, match='last timestamp'):
            repeat.repeat(source, tmp_path / 'overflow', duration=2, fps=1)
        assert not (tmp_path / 'overflow').exists()


def test_drop_time_schema_cli_and_geometry_roundtrip(tmp_path):
    import shlex
    from hololab.manifest import load_manifest
    from hololab.manifest.render import RenderContext, render_manifest
    m, _ = load_manifest(CONVERTER / 'manifest.yaml')
    assert m.inputs['camera'].tags == ['colmap4d-single-cam']
    assert m.outputs['camera'].tags == ['colmap-single-cam']
    assert m.outputs['camera'].storage == 'file' and m.arrayable
    source, output = tmp_path / 'timed.json', tmp_path / 'geometry.json'
    source.write_text(json.dumps(CAMERA))
    before = source.read_bytes()
    command = render_manifest(m, RenderContext(inputs={'camera': str(source)},
        outputs={'camera': str(output)}, pack_dir=str(CONVERTER)))
    subprocess.run([sys.executable, *shlex.split(command.shell)[1:]], check=True)
    geometry = json.loads(output.read_text())
    assert geometry == {k:v for k,v in CAMERA.items() if k != 'timestamp_ns'}
    assert source.read_bytes() == before
    # Lossy by design: only explicitly reattaching the known timestamp restores it.
    restored = tmp_path / 'restored.json'
    restored.write_text(json.dumps({**geometry, 'timestamp_ns': CAMERA['timestamp_ns']}))
    assert repeat.load_camera(restored) == CAMERA
    with pytest.raises(ValueError, match='timestamp_ns'):
        repeat.load_camera(output)
    with pytest.raises(FileExistsError):
        converter.convert(source, output)
    assert json.loads(output.read_text()) == geometry


@pytest.mark.parametrize('bad', [True, 1.5, '0', -2**63-1, 2**63])
def test_prepare_rejects_invalid_override(bad):
    m = manifest()
    graph = WorkflowGraph(nodes=[dict(id='repeat', algorithm_name=m.name,
        algorithm_version=m.version, params={'start_time_ns': bad})], edges=[])
    with pytest.raises(ValueError, match='start_time_ns'):
        resolve_recipe(graph, [m.model_dump(mode='json', by_alias=True)])


@pytest.mark.parametrize('field,value', [('model','UNKNOWN'), ('width',0),
    ('params',[-1,1,1,1]), ('qvec',[0,0,0,0]), ('tvec',[0,0,float('inf')])])
def test_converter_rejects_geometry_before_writing(tmp_path, field, value):
    source = tmp_path / 'input.json'
    source.write_text(json.dumps({**CAMERA, field: value}))
    output = tmp_path / 'out.json'
    with pytest.raises(ValueError):
        converter.convert(source, output)
    assert not output.exists()
