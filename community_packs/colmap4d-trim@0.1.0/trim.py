"""Trim timed COLMAP components using an absolute, half-open nanosecond window.

No renumbering, rebasing, seconds conversion or index selection. Equal timestamps
are selected together. -1 is reserved for the unbounded end. Out-of-range bounds
warn and intersect available records; an empty selection fails before writing.

Standalone cams/points retain references into their complementary components.
Whole models prune removed observations/tracks reciprocally, without compacting
POINT2D indices. If pruning breaks track-centroid provenance, fail explicitly.
Pixel components require times plus images records or image_names.json; legacy
untimed pixels cannot establish absolute time and are rejected.

All model/sidecar parsing and serialization uses colmap4d. The object API loads
full observations and tracks into CPU memory; dense inputs need substantial RAM.
Unknown time metadata is copied byte-for-byte. Output is an independent copy.
"""
import gc
import json
import shutil
import sys
import warnings
from pathlib import Path, PurePosixPath

from colmap4d import colmap_io as cio, sidecar


def table(root, stem, module=cio):
    for ext in ('bin', 'txt'):
        path = root / f'{stem}.{ext}'
        if path.is_file():
            kwargs = {'strict': True} if module is sidecar else {}
            return getattr(module, f'read_{stem}_{ext}')(path, **kwargs)
    raise ValueError(f'Missing {stem}.bin/.txt in {root}')


def has(root, stem):
    return any((root / f'{stem}.{ext}').is_file() for ext in ('bin', 'txt'))


def trim(source, output, start_time_ns=0, end_time_ns=-1):
    # COLMAP records are acyclic. Avoid repeated whole-heap cyclic-GC scans
    # while filtering tens of millions of library objects; restore caller state.
    enabled = gc.isenabled()
    gc.disable()
    try:
        return _trim(source, output, start_time_ns, end_time_ns)
    finally:
        if enabled:
            gc.enable()


def _trim(source, output, start_time_ns, end_time_ns):
    for value in (start_time_ns, end_time_ns):
        if type(value) is not int or not -(2**63) <= value < 2**63:
            raise ValueError('Time parameters must be signed int64 nanoseconds')
    if end_time_ns != -1 and start_time_ns >= end_time_ns:
        raise ValueError('start_time_ns must be less than end_time_ns')
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError('Input and output must not overlap')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Output must be an empty directory')
    sparse = next((p for p in (source/'sparse/0', source/'sparse') if has(p, 'cameras')), None)
    whole = sparse is not None or (has(source, 'cameras') and has(source, 'points3D'))
    root = sparse if sparse is not None else source
    has_pixels = (source/'images').is_dir()
    images = table(root, 'images') if has(root, 'images') else None
    points_present = has(root, 'points3D')
    times = table(root, 'times', sidecar) if has(root, 'times') else None
    pt = table(root, 'points_t', sidecar) if points_present else None
    if whole and (images is None or times is None or pt is None or not has_pixels):
        raise ValueError('Whole model requires cameras, images, points, times and pixels')
    if images is not None and (times is None or set(images) != set(times)):
        raise ValueError('Image records require exact times coverage')
    names = {i: im.name for i, im in images.items()} if images is not None else None
    if has_pixels and names is None:
        mapping = source/'image_names.json'
        if not mapping.is_file() or times is None:
            raise ValueError('Pixel-only component needs times and image_names.json; filenames are not timestamps')
        names = {int(i): name for i, name in json.loads(mapping.read_text()).items()}
        if set(names) != set(times):
            raise ValueError('Image names require exact times coverage')
    if images is None and not has_pixels and not points_present:
        raise ValueError('Expected a colmap4d model or supported component')
    axis = times if times is not None else pt
    if not axis:
        raise ValueError('Missing or empty timestamp axis')
    lo, hi = min(axis.values()), max(axis.values())
    if start_time_ns < lo or start_time_ns > hi or (end_time_ns != -1 and (end_time_ns < lo or end_time_ns > hi)):
        warnings.warn(f'Window exceeds data range [{lo}, {hi}] ns; intersecting with available records', stacklevel=2)
    def inside(t):
        return start_time_ns <= t and (end_time_ns == -1 or t < end_time_ns)
    selected = {i: t for i, t in sorted(axis.items(), key=lambda row: (row[1], row[0])) if inside(t)}
    if not selected:
        raise ValueError('Time window is empty')
    meta = sidecar.read_time_meta(root/'time_meta.json')
    if not meta.get('clock_domain'):
        raise ValueError('Missing clock_domain')
    kept_images = {i: images[i] for i in selected} if images is not None else None
    points = None
    if points_present:
        points = table(root, 'points3D')
        if set(points) != set(pt):
            raise ValueError('Points require exact points_t coverage')
        points = {i: p for i, p in points.items() if inside(pt[i])}
        if not points:
            raise ValueError('Time window contains no points')
        if whole:
            for p in points.values():
                p.track = [(i, n) for i, n in p.track if i in kept_images]
                if meta.get('points_t_method') == 'track_centroid':
                    ts = [times[i] for i, _ in p.track]
                    if not ts or (sum(ts)+len(ts)//2)//len(ts) != pt[p.id]:
                        raise ValueError('Trim changes track-centroid time provenance; cannot preserve point timestamps')
            for im in kept_images.values():
                im.point3D_ids = [pid if pid in points else cio.INVALID_POINT3D_ID for pid in im.point3D_ids]
            for p in points.values():
                for iid, idx in p.track:
                    im = kept_images[iid]
                    if idx >= len(im.point3D_ids) or im.point3D_ids[idx] != p.id:
                        raise ValueError('Nonreciprocal point track')
            for im in kept_images.values():
                for idx, pid in enumerate(im.point3D_ids):
                    if pid != cio.INVALID_POINT3D_ID and (im.id, idx) not in points[pid].track:
                        raise ValueError('Nonreciprocal image observation')
    cameras = table(root, 'cameras') if has(root, 'cameras') else None
    if cameras is not None and kept_images is not None:
        cameras = {i: cameras[i] for i in {im.camera_id for im in kept_images.values()}}
    pixels = []
    if has_pixels:
        seen = set()
        for i in selected:
            name = names[i]; rel = PurePosixPath(name)
            src = source/'images'/name
            if not name or rel.is_absolute() or '..' in rel.parts or '\\' in name or rel.as_posix() != name or name in seen:
                raise ValueError('Unsafe or duplicate image NAME')
            if not src.is_file() or (source/'images').resolve() not in src.resolve().parents:
                raise ValueError(f'Missing or external pixels: {name}')
            seen.add(name); pixels.append((src, output/'images'/name))
    dest = output/'sparse/0' if whole else output
    dest.mkdir(parents=True, exist_ok=True)
    if cameras is not None:
        cio.write_cameras_bin(dest/'cameras.bin', cameras)
    if kept_images is not None:
        cio.write_images_bin(dest/'images.bin', kept_images)
    if times is not None:
        sidecar.write_times_bin(dest/'times.bin', selected)
    if points is not None:
        cio.write_points3D_bin(dest/'points3D.bin', points)
        sidecar.write_points_t_bin(dest/'points_t.bin', {i: pt[i] for i in points})
    # Byte-preserve all metadata, including clock_domain and unknown fields.
    shutil.copyfile(root/'time_meta.json', dest/'time_meta.json')
    for src, target in pixels:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    if has_pixels and (source/'image_names.json').is_file():
        (output/'image_names.json').write_text(json.dumps({str(i): names[i] for i in selected})+'\n')
    return dict(images=len(selected) if times is not None else 0,
                points=len(points) if points is not None else 0,
                timestamps=len(set(selected.values())), clock_domain=meta['clock_domain'])


if __name__ == '__main__':
    config = json.load(sys.stdin)
    print(json.dumps(trim(config['inputs']['input'], config['outputs']['output'], **config.get('params', {}))))
