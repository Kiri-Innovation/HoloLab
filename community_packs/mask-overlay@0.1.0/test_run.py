import importlib.util
from pathlib import Path

import pytest
from PIL import Image

spec = importlib.util.spec_from_file_location('mask_overlay_run', Path(__file__).with_name('run.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def groups(tmp_path):
    image, mask = tmp_path / 'image', tmp_path / 'mask'
    image.mkdir()
    mask.mkdir()
    Image.new('RGB', (2, 1), (100, 200, 50)).save(image / 'a.png')
    Image.new('RGB', (2, 1), (20, 40, 60)).save(image / 'b.png')
    m = Image.new('L', (2, 1))
    m.putdata([0, 255])
    m.save(mask / 'x.png')
    m.save(mask / 'z.png')
    return image, mask, tmp_path / 'out'


def test_positions_and_full_frame_blend(tmp_path):
    image, mask, output = groups(tmp_path)
    (mask / '.metadata').write_text('ignored')
    assert module.overlay_group(image, mask, output) == 2
    with Image.open(output / 'p_0000000000000000.png') as im:
        assert im.mode == 'RGB'
        assert list(im.getdata()) == [(50, 100, 25), (177, 227, 152)]
    with Image.open(output / 'p_0000000000000001.png') as im:
        assert list(im.getdata()) == [(10, 20, 30), (137, 147, 157)]
    assert (output / '.hololab-done').exists()


@pytest.mark.parametrize('alpha', [-0.1, 1.1, float('nan'), float('inf')])
def test_alpha(tmp_path, alpha):
    with pytest.raises(ValueError, match='alpha'):
        module.overlay_group(*groups(tmp_path), alpha)


@pytest.mark.parametrize('failure', ['count', 'size', 'corrupt', 'nested', 'empty'])
def test_invalid_inputs_do_not_write_success(tmp_path, failure):
    image, mask, output = groups(tmp_path)
    if failure == 'count':
        (mask / 'z.png').unlink()
    elif failure == 'size':
        Image.new('L', (3, 1)).save(mask / 'z.png')
    elif failure == 'corrupt':
        (mask / 'z.png').write_bytes(b'bad png')
    elif failure == 'nested':
        (mask / 'nested').mkdir()
    else:
        for f in mask.iterdir():
            f.unlink()
    with pytest.raises((ValueError, OSError)):
        module.overlay_group(image, mask, output)
    assert not output.exists()
