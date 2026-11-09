"""Capture static COLMAP points using an inclusive nanosecond-centered window."""
import argparse
from dataclasses import dataclass
from decimal import Decimal
import gc
import json
import math
from pathlib import Path

import numpy as np
from colmap4d import colmap_io as cio, sidecar
from colmap4d.arrays import read_point_arrays

MIN_NS, MAX_NS = -(2**63), 2**63 - 1

# Timed-camera validation copied from single-cam-drop-time@0.1.0.
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



def window_ns(time_range_s):
    require(finite(time_range_s) and time_range_s >= 0,
            'time_range_s must be a finite nonnegative number')
    # Integer ns differences <= a fractional ns threshold equal <= its floor.
    # Decimal(str(...)) avoids rounding 0.03 s to 29999999.999... ns.
    value = Decimal(str(time_range_s)) * Decimal(1_000_000_000)
    return int(min(value, Decimal(MAX_NS - MIN_NS)))


def table(root, stem):
    for ext in ('bin', 'txt'):
        path = root / f'{stem}.{ext}'
        if path.is_file():
            return path
    raise ValueError(f'Missing {stem}.bin/.txt in {root}')


@dataclass
class Cloud:
    ids: np.ndarray
    xyz: np.ndarray
    rgb: np.ndarray
    rows: np.ndarray
    times: np.ndarray
    dangling_count: int

    @property
    def untimed_count(self):
        return len(self.ids) - len(self.rows)


def load_cloud(source):
    """All COLMAP and time parsing is delegated to colmap4d, including text."""
    root = Path(source)
    meta = sidecar.read_time_meta(root / 'time_meta.json')
    require(meta.get('time_unit') == 'ns', 'point time_meta must declare time_unit ns')
    require(isinstance(meta.get('clock_domain'), str) and bool(meta['clock_domain'].strip()),
            'point time_meta must declare a nonempty clock_domain')
    path = table(root, 'points_t')
    mapping = getattr(sidecar, f'read_points_t_{path.suffix[1:]}')(path, strict=True)
    time_ids = np.fromiter(mapping.keys(), dtype=np.uint64, count=len(mapping))
    times = np.fromiter(mapping.values(), dtype=np.int64, count=len(mapping))
    del mapping
    gc.collect()
    ids, xyz, rgb = read_point_arrays(table(root, 'points3D'))
    # The library supplies sorted unique IDs. Join by ID, never by file position.
    rows = np.searchsorted(ids, time_ids)
    present = rows < len(ids)
    in_bounds = np.flatnonzero(present)
    present[in_bounds] = ids[rows[in_bounds]] == time_ids[in_bounds]
    dangling = int(len(time_ids) - np.count_nonzero(present))
    return Cloud(ids, xyz, rgb, rows[present], times[present], dangling)


def select_rows(cloud, timestamp_ns, time_range_s=0.03):
    require(type(timestamp_ns) is int and MIN_NS <= timestamp_ns <= MAX_NS,
            'timestamp_ns must be a signed int64 integer')
    radius = window_ns(time_range_s)
    low, high = max(MIN_NS, timestamp_ns - radius), min(MAX_NS, timestamp_ns + radius)
    # Avoid int64 subtraction/abs overflow and float conversion of epoch ns.
    mask = (cloud.times >= low) & (cloud.times <= high)
    return np.sort(cloud.rows[mask]), cloud.times[mask]


def capture_loaded(cloud, camera, output, time_range_s=0.03):
    """Same filter/writer used by CLI and callers reusing a decoded cloud."""
    data = load_camera(camera)
    rows, selected_times = select_rows(cloud, data['timestamp_ns'], time_range_s)
    require(np.isfinite(cloud.xyz[rows]).all(), 'selected points must have finite XYZ')
    output = Path(output)
    require(not output.exists() or (output.is_dir() and not any(output.iterdir())),
            'output must be an empty directory')
    require(output.resolve() != Path(camera).resolve() and output.resolve() not in Path(camera).resolve().parents,
            'output must not contain the camera input')
    output.mkdir(parents=True, exist_ok=True)
    # point-cloud is a directory of COLMAP-text points, with no time/observations.
    with (output / 'points3D.txt').open('x') as stream:
        stream.write('# 3D point list: POINT3D_ID X Y Z R G B ERROR\n')
        stream.write(f'# Number of points: {len(rows)}, mean track length: 0\n')
        for row in rows:
            xyz = ' '.join(format(float(v), '.17g') for v in cloud.xyz[row])
            rgb = ' '.join(str(int(v)) for v in cloud.rgb[row])
            stream.write(f'{int(cloud.ids[row])} {xyz} {rgb} 0\n')
    count = len(rows)
    info = dict(count=count, total=len(cloud.ids), timestamp_ns=data['timestamp_ns'],
                radius_ns=window_ns(time_range_s), untimed_count=cloud.untimed_count,
                dangling_time_count=cloud.dangling_count,
                min_selected_ns=int(selected_times.min()) if count else None,
                max_selected_ns=int(selected_times.max()) if count else None)
    print(f'[ITER 1/1] captured {count} / {len(cloud.ids)} points', flush=True)
    return info


def capture(timestamp_cam, points, output, time_range_s=0.03):
    window_ns(time_range_s)
    load_camera(timestamp_cam)  # Fail invalid cameras before loading a dense cloud.
    source, destination = Path(points).resolve(), Path(output).resolve()
    require(source != destination and source not in destination.parents and destination not in source.parents,
            'point-cloud input and output must not overlap')
    require(not destination.exists() or (destination.is_dir() and not any(destination.iterdir())),
            'output must be an empty directory')
    cloud = load_cloud(source)
    if cloud.untimed_count:
        print(f'WARNING: excluding {cloud.untimed_count} points without a timestamp center', flush=True)
    return capture_loaded(cloud, timestamp_cam, output, time_range_s)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timestamp-cam', required=True)
    parser.add_argument('--points', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--time-range-s', type=float, default=0.03)
    args = parser.parse_args()
    try:
        capture(args.timestamp_cam, args.points, args.output, args.time_range_s)
    except (ValueError, OSError, OverflowError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
