"""Explicitly remove the timestamp from a timed single-camera JSON leaf."""
import argparse
import json
import math
from pathlib import Path

from colmap4d import colmap_io as cio

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
    require(isinstance(data, dict), 'colmap4d-single-cam must be a JSON object')
    fields = {'model', 'width', 'height', 'params', 'qvec', 'tvec', 'timestamp_ns'}
    require(set(data) == fields, 'colmap4d-single-cam requires exactly model, width, height, params, qvec, tvec, timestamp_ns')
    require(type(data['timestamp_ns']) is int and MIN_NS <= data['timestamp_ns'] <= MAX_NS,
            'timestamp_ns must be a signed int64 integer')
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


def convert(camera, output):
    data = load_camera(camera)
    del data['timestamp_ns']
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Refuse to overwrite an existing artifact or the source.
    with output.open('x') as stream:
        json.dump(data, stream, allow_nan=False)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--camera', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        convert(**vars(args))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
