import importlib.util
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
import pytest

spec = importlib.util.spec_from_file_location('mask_overlay_v2', Path(__file__).with_name('run.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.mark.parametrize('mode', m.MODES)
@pytest.mark.parametrize('alpha', [0, 0.3, 0.5, 1])
def test_modes_threshold_and_unchanged_region(mode, alpha):
    image = np.array([[[50, 200, 100]] * 4], dtype=np.uint8)
    mask = np.array([[0, 127, 128, 255]], dtype=np.uint8)
    got = m.render_bgr(image, mask, mode, alpha)
    if mode == 'blend50':
        expected = ((1-alpha)*image.astype(float)+alpha*mask[:, :, None]).astype('uint8')
    else:
        expected = image.copy()
        selected = mask >= 128 if mode == 'mask1_red' else mask < 128
        color = [0, 0, 0] if mode == 'mask0_black' else [0, 0, 255]
        expected[selected] = ((1-alpha)*image[selected].astype(float)+alpha*np.array(color)).astype('uint8')
        assert np.array_equal(got[~selected], image[~selected])
    np.testing.assert_array_equal(got, expected)


def test_legacy_half_blend_all_byte_values():
    image = np.arange(256, dtype=np.uint8)[None, :, None].repeat(256, axis=0).repeat(3, axis=2)
    mask = np.arange(256, dtype=np.uint8)[:, None].repeat(256, axis=1)
    got = m.render_bgr(image, mask, 'blend50', 0.5)
    expected = ((image.astype(np.uint16)+mask[:, :, None])//2).astype('uint8')
    np.testing.assert_array_equal(got, expected)


def groups(tmp_path):
    a, b = tmp_path/'image', tmp_path/'mask'
    a.mkdir(); b.mkdir()
    for name, value in [('z', 200), ('a', 100)]:
        Image.new('RGB', (4, 4), (value, value, value)).save(a/f'{name}.png')
    for name in ['x', 'q']:
        Image.new('L', (4, 4), 0).save(b/f'{name}.png')
    return a, b, tmp_path/'out'


@pytest.mark.parametrize('fmt', ['png', 'jpg'])
def test_parallel_order_and_formats(tmp_path, fmt):
    a,b,out = groups(tmp_path)
    (a/'.metadata').write_text('ignored')
    m.overlay_group(a,b,out,format=fmt,io_workers=2)
    assert (out/'.hololab-done').exists()
    for i, value in enumerate([50,100]):
        with Image.open(out/f'p_{i:016d}.{fmt}') as im:
            assert im.mode=='RGB' and im.size==(4,4)
            assert im.getpixel((0,0)) == (value,)*3


@pytest.mark.parametrize('kwargs', [{'mode':'invalid'}, {'format':'webp'}, {'alpha':float('nan')},
                                  {'alpha':1.1}, {'io_workers':0}, {'io_workers':17},
                                  {'png_compress':10}, {'jpeg_quality':0}])
def test_invalid_options(tmp_path, kwargs):
    with pytest.raises(ValueError): m.overlay_group(*groups(tmp_path), **kwargs)
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('failure', ['size','count','corrupt','nested','empty'])
def test_input_errors(tmp_path, failure):
    a,b,out=groups(tmp_path)
    if failure=='size': Image.new('L',(3,4)).save(b/'x.png')
    elif failure=='count': (b/'x.png').unlink()
    elif failure=='corrupt': (b/'x.png').write_bytes(b'bad PNG')
    elif failure=='nested': (b/'nested').mkdir()
    else:
        for p in b.iterdir(): p.unlink()
    with pytest.raises((ValueError, OSError)): m.overlay_group(a,b,out)
    assert not (out/'.hololab-done').exists()


def test_worker_failure_has_no_success_marker(tmp_path, monkeypatch):
    a,b,out=groups(tmp_path)
    monkeypatch.setattr(m.cv2,'imwrite',lambda *a,**k: False)
    with pytest.raises(OSError): m.overlay_group(a,b,out)
    assert not (out/'.hololab-done').exists()
