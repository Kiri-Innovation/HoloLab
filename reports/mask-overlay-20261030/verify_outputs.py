"""Read actual workflow artifacts; never dispatch or mutate source artifacts."""
import json
from pathlib import Path

import numpy as np
from PIL import Image

report_dir = Path(__file__).resolve().parent

def root(name):
    return Path(json.loads((report_dir / name).read_text())['absolute_path'])


def visible(path):
    return sorted(p for p in path.iterdir() if not p.name.startswith('.'))


output = root('overlay-summary.json')
images = root('fx-summary.json')
masks = root('workflow-mask-summary.json')
count = sampled = size_bytes = 0
outer = visible(output)
assert len(outer) == 80
for directory, image_dir, mask_dir in zip(outer, visible(images), visible(masks), strict=True):
    files, image_files, mask_files = visible(directory), visible(image_dir), visible(mask_dir)
    assert len(files) == len(image_files) == len(mask_files) == 50
    assert [f.name for f in files] == [f'p_{i:016d}.png' for i in range(50)]
    assert (directory / '.hololab-done').is_file()
    for i, (f, src, mask) in enumerate(zip(files, image_files, mask_files, strict=True)):
        with Image.open(f) as actual, Image.open(src) as image, Image.open(mask) as binary:
            assert actual.mode == 'RGB' and actual.size == image.size == binary.size
            if i in (0, 49):
                expected = ((np.asarray(image.convert('RGB'), dtype=np.uint16)
                             + np.asarray(binary.convert('RGB'), dtype=np.uint16)) // 2).astype(np.uint8)
                assert np.array_equal(np.asarray(actual), expected), str(f)
                sampled += 1
        count += 1
        size_bytes += f.stat().st_size
result = {'groups': len(outer), 'images': count, 'all_mode': 'RGB',
          'all_dimensions_match_inputs': True, 'exact_pixel_checks': sampled,
          'png_bytes': size_bytes, 'output': str(output)}
(report_dir / 'workflow-output-validation.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))
