"""Repeat a timeless COLMAP camera as a synthetic timed camera component."""
import argparse
import json
import math
from fractions import Fraction
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


def schedule(duration, fps, start_time_ns=0):
    require(finite(duration) and duration > 0, 'duration must be finite and > 0 seconds')
    require(finite(fps) and 0 < fps <= 1e9, 'fps must be finite and > 0, at most 1e9 (nanosecond resolution)')
    require(type(start_time_ns) is int and MIN_NS <= start_time_ns <= MAX_NS,
            'start_time_ns must be a signed int64 integer')
    # Decimal user values, exact arithmetic, no accumulated float drift.
    rate = Fraction(str(fps))
    count = int(Fraction(str(duration)) * rate)
    require(1 <= count <= MAX_FRAMES,
            f'floor(duration * fps) must be between 1 and {MAX_FRAMES} frames')
    step = Fraction(1_000_000_000) / rate
    require(start_time_ns + round((count - 1) * step) <= MAX_NS,
            'last timestamp exceeds signed int64 nanoseconds')
    return count, step


def load_camera(path):
    data = json.loads(Path(path).read_text())
    require(isinstance(data, dict), 'colmap-camera must be a JSON object')
    fields = {'model', 'width', 'height', 'params', 'qvec', 'tvec'}
    require(set(data) == fields, 'colmap-camera requires exactly model, width, height, params, qvec, tvec')
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


def repeat(camera, output, duration=1.0, fps=30.0, start_time_ns=0):
    count, step = schedule(duration, fps, start_time_ns)
    data = load_camera(camera)
    output = Path(output)
    require(not output.exists() or (output.is_dir() and not any(output.iterdir())), 'output directory must be empty')
    output.mkdir(parents=True, exist_ok=True)
    cio.write_cameras_bin(output/'cameras.bin', {1: cio.Camera(1, data['model'], data['width'], data['height'], data['params'])})
    images = {i+1: cio.Image(i+1, f'f{i:06d}__cam0.png', 1, data['qvec'], data['tvec']) for i in range(count)}
    cio.write_images_bin(output/'images.bin', images)
    colmap4d.write_times_bin(output/'times.bin', {i+1: start_time_ns + round(i*step) for i in range(count)})
    colmap4d.write_time_meta(output/'time_meta.json', {
        'colmap4d_spec': '1.0', 'time_unit': 'ns', 'time_convention': 'mid_exposure',
        'clock_domain': 'synthetic_uniform',
        'conversion': {'producer': 'col4d/repeat-4d@0.1.0', 'timestamp_mode': 'synthetic_uniform',
                       'synthetic': True, 'duration_seconds': duration, 'fps': fps,
                       'frame_count_policy': 'floor'},
    })
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--camera', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--duration', type=float, default=1.0)
    parser.add_argument('--fps', type=float, default=30.0)
    parser.add_argument('--start-time-ns', type=int, default=0)
    args = parser.parse_args()
    try:
        count = repeat(**vars(args))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f'[ITER 1/1] wrote {count} constant-pose camera frames', flush=True)


if __name__ == '__main__':
    main()
