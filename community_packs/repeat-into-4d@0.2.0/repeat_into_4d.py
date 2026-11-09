"""Repeat a timeless camera over a reference component's unique timestamps."""
import argparse
import json
import math
from pathlib import Path

import colmap4d
from colmap4d import colmap_io as cio

MAX_FRAMES = 1_000_000
MIN_NS, MAX_NS = -(2**63), 2**63 - 1


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def load_camera(path):
    data = json.loads(Path(path).read_text())
    require(isinstance(data, dict), 'colmap-single-cam must be a JSON object')
    fields = {'model', 'width', 'height', 'params', 'qvec', 'tvec'}
    require(set(data) == fields, 'colmap-single-cam requires exactly model, width, height, params, qvec, tvec')
    model = data['model']
    require(isinstance(model, str) and model in cio._MODEL_NAME_TO_ID, 'unsupported COLMAP camera model')
    for key in ('width', 'height'):
        require(type(data[key]) is int and 0 < data[key] < 2**64, f'{key} must be a positive uint64')
    for key, size in [('params', cio._MODEL_NUM_PARAMS[cio._MODEL_NAME_TO_ID[model]]), ('qvec', 4), ('tvec', 3)]:
        require(isinstance(data[key], list) and len(data[key]) == size and all(map(finite, data[key])),
                f'{key} must contain {size} finite numbers')
    require(data['params'][0] > 0, 'focal length must be positive')
    if model in ('PINHOLE', 'OPENCV', 'OPENCV_FISHEYE', 'FULL_OPENCV', 'FOV', 'THIN_PRISM_FISHEYE'):
        require(data['params'][1] > 0, 'fy must be positive')
    require(abs(math.hypot(*data['qvec']) - 1) < 1e-6, 'qvec must be a unit world-to-camera quaternion (w,x,y,z)')
    return data


def repeat(camera, timestamp_cameras, output):
    reference = Path(timestamp_cameras)
    times_path = reference / 'times.bin'
    if times_path.is_file():
        times = colmap4d.read_times_bin(times_path, strict=True)
    else:
        times = colmap4d.read_times_txt(reference / 'times.txt', strict=True)
    timestamps = sorted(set(times.values()))
    require(1 <= len(timestamps) <= MAX_FRAMES,
            f'timestamp_cameras must contain between 1 and {MAX_FRAMES} unique timestamps')
    require(all(type(t) is int and MIN_NS <= t <= MAX_NS for t in timestamps),
            'timestamps must be signed int64 nanoseconds')
    # Preserve every metadata field and its serialization, without synthetic overrides.
    meta_bytes = (reference / 'time_meta.json').read_bytes()
    meta = json.loads(meta_bytes)
    require(isinstance(meta, dict) and isinstance(meta.get('clock_domain'), str)
            and bool(meta['clock_domain'].strip()), 'time_meta requires a nonempty clock_domain')
    require(meta.get('time_unit') == 'ns', 'time_meta time_unit must be ns')
    count = len(timestamps)
    data = load_camera(camera)
    output = Path(output)
    require(not output.exists() or (output.is_dir() and not any(output.iterdir())), 'output directory must be empty')
    output.mkdir(parents=True, exist_ok=True)
    cio.write_cameras_bin(output/'cameras.bin', {1: cio.Camera(1, data['model'], data['width'], data['height'], data['params'])})
    images = {i+1: cio.Image(i+1, f'f{i:06d}__cam0.png', 1, data['qvec'], data['tvec']) for i in range(count)}
    cio.write_images_bin(output/'images.bin', images)
    colmap4d.write_times_bin(output/'times.bin', dict(enumerate(timestamps, start=1)))
    (output/'time_meta.json').write_bytes(meta_bytes)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--camera', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--timestamp-cameras', required=True)
    args = parser.parse_args()
    try:
        count = repeat(**vars(args))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f'[ITER 1/1] wrote {count} constant-pose camera frames', flush=True)


if __name__ == '__main__':
    main()
