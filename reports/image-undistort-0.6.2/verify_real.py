"""Run with the colmap/kiri Python; writes isolated evidence under /tmp."""
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
OUT = Path('/tmp/hololab-image-undistort-062-verification')
OUT.mkdir(exist_ok=True)
JOB = Path('/cloud/cloud-ssd1/Kiri4DGS/output/hololab/w/586915eb-0a2d-4566-b0f1-f6febd1957b3/j')
SRC = JOB / '737df0a5-6403-4bf2-9b0d-26aac2cc3727/out/p_0000000000000000/p_0000000000000000.png'
PRIOR = JOB / 'f9e569ac-5740-45af-90de-4d7622b07384/cams'


def load(version):
    folder = ROOT / f'community_packs/image-undistort@{version}'
    sys.path.insert(0, str(folder))
    spec = importlib.util.spec_from_file_location('worker_' + version, folder / 'und_worker.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.path.pop(0)
    return module

old, new = load('0.6.1'), load('0.6.2')
from position_model import read_images, camera_rows
pose = sorted(read_images(PRIOR / 'images.txt'), key=lambda p: int(p[0][0]))[0][0]
cam_id = int(pose[8])
cams = OUT / 'input-cams'
cams.mkdir(exist_ok=True)
(cams / 'cameras.txt').write_text(' '.join(camera_rows(PRIOR / 'cameras.txt')[cam_id]) + '\n')
(cams / 'images.txt').write_text(' '.join(pose) + '\n\n')
(cams / 'points3D.txt').write_text('')
Image.open(SRC).convert('RGB').save(OUT / 'rgb-input.png')


def run(worker, label, source):
    dest = OUT / label
    req = dict(cams=str(cams), images=str(source), cams_out=str(dest / 'cams'),
               images_out=str(dest / 'images'), scratch=str(dest / 'scratch'),
               blank_pixels=0, io_workers=1, png_compress=1, chunk_size=1, dtype='fp16')
    assert worker.process_shard(req) == 0
    return dest / 'images/p_0000000000000000.png'

rgba_path = run(new, 'rgba-new', SRC)
old_path = run(old, 'rgb-old', OUT / 'rgb-input.png')
new_path = run(new, 'rgb-new', OUT / 'rgb-input.png')


def stats(path):
    with Image.open(path) as im:
        a = np.asarray(im)
        result = dict(path=str(path), mode=im.mode, shape=list(a.shape))
        if a.shape[-1] == 4:
            alpha = a[..., 3]
            result['alpha'] = dict(unique=int(np.unique(alpha).size), min=int(alpha.min()),
                max=int(alpha.max()), zero=int((alpha == 0).sum()),
                opaque=int((alpha == 255).sum()), soft=int(((alpha > 0) & (alpha < 255)).sum()))
        return result

rgba = np.asarray(Image.open(rgba_path))
a = np.asarray(Image.open(old_path)); b = np.asarray(Image.open(new_path))
assert np.array_equal(a, b)
assert Image.open(rgba_path).mode == 'RGBA'
assert rgba.shape[-1] == 4 and np.unique(rgba[..., 3]).size > 2
assert rgba[..., 3].min() == 0 and rgba[..., 3].max() == 255
cam = new.parse_cameras(cams / 'cameras.txt')[cam_id]
grid = new._build_grid(cam, new.compute_new_pinhole(cam), 'cuda')
source = new._read_rgb(SRC)
# Independent legacy RGB resampling of the source mask verifies alpha coordinates.
mask_rgb = np.repeat(source[..., 3:4], 3, axis=-1)
reference = old.undistort_batch(mask_rgb[None], grid)[0, ..., 0]
delta = np.abs(rgba[..., 3].astype(np.int16) - reference.astype(np.int16))
assert delta.max() <= 1
# A four-channel landmark image with R==alpha must keep them aligned exactly.
landmark = source.copy(); landmark[..., 0] = landmark[..., 3]
warped = new.undistort_batch(landmark[None], grid)[0]
assert np.array_equal(warped[..., 0], warped[..., 3])
result = dict(input=stats(SRC), output=stats(rgba_path), source_prior=str(PRIOR),
    camera_row=(cams / 'cameras.txt').read_text().strip(),
    rgb=dict(input=stats(OUT / 'rgb-input.png'), old=stats(old_path), new=stats(new_path),
             differing_values=int(np.count_nonzero(a != b)), max_absolute_error=int(np.abs(a.astype(int)-b.astype(int)).max()),
             encoded_png_identical=old_path.read_bytes() == new_path.read_bytes()),
    geometry=dict(alpha_reference_max_error=int(delta.max()), alpha_reference_differing_pixels=int((delta != 0).sum()),
                  alpha_nonzero_region_equal=bool(np.array_equal(rgba[...,3] > 0, reference > 0)),
                  red_alpha_landmark_exact=True))
(ROOT / 'reports/image-undistort-0.6.2/results.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
# Scratch contains links/copies only for these isolated runs, no user outputs.
import shutil
for label in ('rgba-new', 'rgb-old', 'rgb-new'):
    shutil.rmtree(OUT / label / 'scratch')
