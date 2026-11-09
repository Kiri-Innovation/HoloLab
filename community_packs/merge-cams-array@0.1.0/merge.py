"""Merge a camera-axis array of binary timed camera components."""
import argparse
import json
from dataclasses import replace
from pathlib import Path

import colmap4d
from colmap4d import colmap_io as cio


def merge(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError('input and output must not overlap')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('output must be empty')
    paths = sorted(p for p in source.iterdir() if not p.name.startswith('.'))
    if not paths:
        raise ValueError('empty camera array')
    cameras, keys, rows, provenance = {}, {}, [], []
    common = None
    for path in paths:
        meta = colmap4d.read_time_meta(path/'time_meta.json')
        semantics = {k: meta.get(k) for k in ('colmap4d_spec', 'time_unit', 'time_convention', 'clock_domain')}
        if semantics['time_unit'] != 'ns' or not all(semantics.values()):
            raise ValueError('explicit ns clock and time convention required')
        if common is not None and semantics != common:
            raise ValueError('incompatible clocks/conventions; align timestamps upstream before merging')
        common = semantics
        cams = cio.read_cameras_bin(path/'cameras.bin')
        images = cio.read_images_bin(path/'images.bin')
        times = colmap4d.read_times_bin(path/'times.bin', strict=True)
        if not images or images.keys() != times.keys():
            raise ValueError('times must cover every image exactly; empty shards unsupported')
        remap = {}
        for cid, cam in sorted(cams.items()):
            key = (cam.model, cam.width, cam.height, tuple(cam.params))
            if key not in keys:
                keys[key] = len(keys) + 1
                cameras[keys[key]] = replace(cam, id=keys[key])
            remap[cid] = keys[key]
        for iid, im in sorted(images.items()):
            if im.camera_id not in remap:
                raise ValueError('unknown CAMERA_ID')
            if any(pid != cio.INVALID_POINT3D_ID for pid in im.point3D_ids):
                raise ValueError('point references require a full-model merge; cams-only input expected')
            rows.append((times[iid], replace(im, camera_id=remap[im.camera_id], name=f'{path.name}/{im.name}')))
        provenance.append(dict(shard=path.name, images=len(images), time_meta=meta))
    rows.sort(key=lambda row: row[0])  # stable: shard name, then original IMAGE_ID
    images = {i: replace(im, id=i) for i, (_, im) in enumerate(rows, 1)}
    times = {i: t for i, (t, _) in enumerate(rows, 1)}
    summary = dict(shards=len(paths), images=len(images), intrinsics=len(cameras),
                   min_timestamp_ns=rows[0][0], max_timestamp_ns=rows[-1][0])
    common['conversion'] = dict(producer='col4d/merge-cams-array@0.1.0',
        order='timestamp_ns, shard name, original IMAGE_ID', sources=provenance, **summary)
    output.mkdir(parents=True, exist_ok=True)
    cio.write_cameras_bin(output/'cameras.bin', cameras)
    cio.write_images_bin(output/'images.bin', images)
    colmap4d.write_times_bin(output/'times.bin', times)
    colmap4d.write_time_meta(output/'time_meta.json', common)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(merge(args.input, args.output)))
