"""Lossless binary camera-component views with timed seven-field JSON leaves."""
import argparse
import base64
import hashlib
import io
import json
import math
import shutil
import struct
from pathlib import Path

from colmap4d import colmap_io as cio
from colmap4d.arrays import read_image_headers

SIDECAR = '.camera-view.json'
FORMAT = 'hololab.camera-view.v2'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(data):
    return base64.b64encode(data).decode('ascii')


def decode(data):
    return base64.b64decode(data, validate=True)


def read(stream, size):
    data = stream.read(size)
    require(len(data) == size, 'truncated binary table')
    return data


def records(data, kind):
    """Retain order and every byte, including observations and unused intrinsics."""
    stream = io.BytesIO(data)
    count, = struct.unpack('<Q', read(stream, 8))
    result = {}
    for _ in range(count):
        start = stream.tell()
        if kind == 'cameras':
            header = read(stream, 24)
            identity, model, width, height = struct.unpack('<IiQQ', header)
            require(model in cio._MODEL_NUM_PARAMS, 'unsupported camera model')
            read(stream, cio._MODEL_NUM_PARAMS[model] * 8)
            require(width > 0 and height > 0, 'invalid camera dimensions')
        elif kind == 'images':
            identity, = struct.unpack_from('<I', read(stream, 64))
            while read(stream, 1) != b'\0':
                pass
            n, = struct.unpack('<Q', read(stream, 8))
            read(stream, n * 24)
        else:
            identity, = struct.unpack_from('<I', read(stream, 12))
        require(identity not in result, f'duplicate {kind} ID')
        result[identity] = data[start:stream.tell()]
    require(stream.tell() == len(data), f'trailing {kind} bytes')
    return result


def image_index(path):
    result = {}
    with path.open('rb') as stream:
        count, = struct.unpack('<Q', read(stream, 8))
        size = path.stat().st_size
        for _ in range(count):
            start = stream.tell()
            iid, = struct.unpack_from('<I', read(stream, 64))
            while read(stream, 1) != b'\0':
                pass
            n, = struct.unpack('<Q', read(stream, 8))
            end = stream.tell() + n * 24
            require(end <= size, 'truncated image observations')
            require(iid not in result, 'duplicate images ID')
            stream.seek(start)
            digest = hashlib.sha256()
            remaining = end - start
            while remaining:
                block = read(stream, min(remaining, 1024 * 1024))
                digest.update(block)
                remaining -= len(block)
            result[iid] = dict(offset=start, size=end-start, sha256=digest.hexdigest())
        require(stream.tell() == size, 'trailing images bytes')
    return result


def empty_output(source, output):
    source, output = source.resolve(), output.resolve()
    require(source != output and source not in output.parents and output not in source.parents,
            'input and output must not overlap')
    require(not output.exists() or (output.is_dir() and not any(output.iterdir())),
            'output must be an empty directory')


def timestamp(data):
    value = data.get('timestamp_ns')
    require(type(value) is int and -(2**63) <= value < 2**63,
            'timestamp_ns must be a signed int64 integer')
    return value


def leaf(camera, image, timestamp_ns):
    data = dict(model=camera.model, width=camera.width, height=camera.height,
                params=list(camera.params), qvec=list(image.qvec), tvec=list(image.tvec),
                timestamp_ns=timestamp_ns)
    timestamp(data)
    require(all(math.isfinite(x) for k in ('params', 'qvec', 'tvec') for x in data[k]),
            'nonfinite camera')
    require(abs(math.hypot(*data['qvec']) - 1) < 1e-6, 'invalid unit quaternion')
    require(data['params'][0] > 0, 'invalid focal length')
    if camera.model in ('PINHOLE', 'OPENCV', 'OPENCV_FISHEYE', 'FULL_OPENCV', 'FOV', 'THIN_PRISM_FISHEYE'):
        require(data['params'][1] > 0, 'invalid fy')
    return (json.dumps(data, allow_nan=False, sort_keys=True) + '\n').encode()


def to_array(source, output):
    source, output = Path(source), Path(output)
    empty_output(source, output)
    raw = {k: (source / (k + '.bin')).read_bytes() for k in ('cameras', 'times')}
    tables = {k: records(v, k) for k, v in raw.items()}
    image_records = image_index(source / 'images.bin')
    require(image_records, 'empty camera components are not supported')
    require(image_records.keys() == tables['times'].keys(), 'times must cover every image exactly')
    meta_raw = (source / 'time_meta.json').read_bytes()
    meta = json.loads(meta_raw)
    require(meta.get('time_unit') == 'ns' and meta.get('clock_domain'), 'explicit ns clock domain required')
    cameras = cio.read_cameras_bin(source / 'cameras.bin')
    images = read_image_headers(source / 'images.bin')
    times = {i: struct.unpack('<Iq', record)[1] for i, record in tables['times'].items()}
    entries = {}
    payloads = {}
    for position, iid in enumerate(sorted(images, key=lambda i: (times[i], i))):
        image = images[iid]
        require(image.camera_id in cameras, 'unknown CAMERA_ID')
        name = f'p_{position:016d}.json'
        payloads[name] = leaf(cameras[image.camera_id], image, times[iid])
        entries[name] = dict(image_id=iid, camera_id=image.camera_id, timestamp_ns=times[iid],
                             sha256=sha(payloads[name]))
    # Root metadata is hidden from array enumeration. Links made by interval-pick
    # resolve back here; plain copies must retain this sidecar beside the leaves.
    evidence = dict(format=FORMAT, entries=entries, time_meta=encode(meta_raw),
                    image_records=list(image_records.items()),
                    tables={k: [[i, encode(record)] for i, record in rows.items()]
                            for k, rows in tables.items()})
    output.mkdir(parents=True, exist_ok=True)
    (output / '.camera-view').mkdir()
    shutil.copyfile(source / 'images.bin', output / '.camera-view/images.bin')
    for name, data in payloads.items():
        (output / name).write_bytes(data)
    (output / SIDECAR).write_text(json.dumps(evidence, sort_keys=True) + '\n')
    return dict(images=len(entries))


def from_array(source, output):
    source, output = Path(source), Path(output)
    empty_output(source, output)
    paths = sorted(p for p in source.iterdir() if not p.name.startswith('.'))
    require(paths, 'cannot restore an empty selection')
    cache = {}
    namespace = None
    selected = set()
    leaf_times = {}
    evidence = None
    archive = None
    for path in paths:
        resolved = path.resolve(strict=True)
        require(resolved.is_file(), 'colmap4d-single-cam elements must be files')
        sidecar = resolved.parent / SIDECAR
        if sidecar not in cache:
            raw = sidecar.read_bytes()
            cache[sidecar] = (sha(raw), json.loads(raw))
        digest, data = cache[sidecar]
        require(data.get('format') == FORMAT, 'missing camera view provenance')
        if namespace is None:
            namespace, evidence = digest, data
            archive = resolved.parent / '.camera-view/images.bin'
        require(namespace == digest, 'source identity conflict; automatic remapping forbidden')
        entry = data['entries'].get(resolved.name)
        require(entry is not None, 'missing element identity')
        payload = resolved.read_bytes()
        camera = json.loads(payload)
        require(isinstance(camera, dict) and set(camera) == {
            'model', 'width', 'height', 'params', 'qvec', 'tvec', 'timestamp_ns'},
            'colmap4d-single-cam requires exactly seven fields')
        time_ns = timestamp(camera)
        require(sha(payload) == entry['sha256'], 'camera leaf was modified')
        iid = entry['image_id']
        require(iid not in leaf_times or leaf_times[iid] == time_ns, 'image timestamp conflict')
        leaf_times[iid] = time_ns
        selected.add(iid)
    tables = {k: {i: decode(raw) for i, raw in rows} for k, rows in evidence['tables'].items()}
    image_records = dict(evidence['image_records'])
    require(selected <= image_records.keys() and selected <= tables['times'].keys(), 'missing source record')
    require(archive.is_file(), 'missing image record archive')
    # Keep all original intrinsics, including unused ones, and original table order.
    restored = {}
    for kind in ('cameras', 'times'):
        # Original times table supplies identity/order only. Time values come
        # from the typed JSON leaves, never from the provenance snapshot.
        rows = [record if kind == 'cameras' else struct.pack('<Iq', iid, leaf_times[iid])
                for iid, record in tables[kind].items() if kind == 'cameras' or iid in selected]
        restored[kind] = struct.pack('<Q', len(rows)) + b''.join(rows)
        records(restored[kind], kind)
    output.mkdir(parents=True, exist_ok=True)
    for kind, raw in restored.items():
        (output / (kind + '.bin')).write_bytes(raw)
    with archive.open('rb') as src, (output / 'images.bin').open('wb') as dst:
        dst.write(struct.pack('<Q', len(selected)))
        for iid, record in image_records.items():
            if iid not in selected:
                continue
            src.seek(record['offset'])
            remaining = record['size']
            digest = hashlib.sha256()
            while remaining:
                block = read(src, min(remaining, 1024 * 1024))
                digest.update(block)
                dst.write(block)
                remaining -= len(block)
            require(digest.hexdigest() == record['sha256'], 'image record archive was modified')
    (output / 'time_meta.json').write_bytes(decode(evidence['time_meta']))
    return dict(images=len(selected))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['to_array', 'from_array'])
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(globals()[args.operation](args.input, args.output)))


if __name__ == '__main__':
    main()
