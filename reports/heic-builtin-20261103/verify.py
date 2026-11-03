import hashlib
import json
import os
from pathlib import Path
import time
import pillow_heif
from PIL import Image

source = Path('/cloud/cloud-ssd1/Kiri4DGS/data/persona_bench/beach_sunglasses_hat_sitting/input/image')
output = Path('/tmp/hololab-heic-builtin/frames')
frames = sorted(source.glob('*.heic'), key=lambda p: os.fsencode(p.name))
results = []
for frame in frames:
    target = output / frame.with_suffix('.png').name
    # Conversion log is flushed only after PNG writing is finished.
    while f'] {frame.name} ->' not in Path('/tmp/hololab-heic-builtin/conversion.log').read_text():
        time.sleep(1)
    assert target.is_file() and not target.is_symlink(), target
    assert target.read_bytes()[:8] == b'\x89PNG\r\n\x1a\n'
    with pillow_heif.open_heif(frame, convert_hdr_to_8bit=False).to_pillow() as decoded, Image.open(target) as png:
        assert png.format == 'PNG'
        assert png.size == (1440, 1920)
        assert png.getexif().get(0x0112) is None
        decoded = decoded.transpose(Image.Transpose.ROTATE_270)
        assert png.mode == decoded.mode
        assert png.tobytes() == decoded.tobytes(), frame
        results.append(dict(source=frame.name, output=target.name, size=png.size, mode=png.mode,
                            decoded_sha256=hashlib.sha256(decoded.tobytes()).hexdigest(),
                            png_pixels_sha256=hashlib.sha256(png.tobytes()).hexdigest()))
    print(f'verified {len(results)}/{len(frames)}', flush=True)
assert len(frames) == len(list(output.glob('*.png'))) == 252
assert [r['output'] for r in results] == sorted(p.name for p in output.glob('*.png'))
Path('/tmp/hololab-heic-builtin/pixel-evidence.json').write_text(json.dumps(results, indent=2))
print('PASS: 252/252 PNGs = 1440x1920; 252/252 exact pixel matches to manual ROTATE_270; 252/252 orientation absent; sequence preserved', flush=True)
