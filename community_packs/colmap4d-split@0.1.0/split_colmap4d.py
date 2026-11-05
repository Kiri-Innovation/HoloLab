"""Split a timed colmap4d bundle without rewriting IDs, observations or timestamps."""
import argparse
import json
from pathlib import Path, PurePosixPath
import resource
import shutil
import time

import numpy as np
from colmap4d.arrays import read_image_headers, read_point_arrays


def selected(root, stem):
    for suffix in ('.bin', '.txt'):
        path = root / (stem + suffix)
        if path.is_file():
            return path
    raise ValueError(f'Missing {stem} in {root}')


def split(model, cams, points, images):
    started = time.perf_counter()
    model = Path(model).resolve()
    roots = [Path(p).resolve() for p in (cams, points, images)]
    paths = [model, *roots]
    for i, a in enumerate(paths):
        for b in paths[i+1:]:
            if a == b or a in b.parents or b in a.parents:
                raise ValueError('Input and output directories must not overlap')
    for root in roots:
        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            raise ValueError('Outputs must be empty directories')
    sparse = next((p for p in (model/'sparse/0', model/'sparse', model)
                   if (p/'cameras.bin').is_file() or (p/'cameras.txt').is_file()), None)
    if sparse is None:
        raise ValueError('Missing COLMAP sparse model')
    tables = {s: selected(sparse, s) for s in ('cameras','images','points3D','times','points_t')}
    meta = sparse / 'time_meta.json'
    if not meta.is_file():
        raise ValueError('Timed component profile requires time_meta.json')
    print('[ITER 1/3] read arrays and image headers', flush=True)
    headers = read_image_headers(tables['images'])
    ids, xyz, rgb = read_point_arrays(tables['points3D'])
    n = len(ids)
    for start in range(0, n, 524288):
        if not np.isfinite(xyz[start:start+524288]).all():
            raise ValueError('Nonfinite point geometry')
    del ids, xyz, rgb
    # Validate all pixel paths before any output is written. No observation or
    # track objects, no ID/time normalization, no duplicate authoritative tables.
    pixels = []
    names = set()
    for im in headers.values():
        name = im.name
        rel = PurePosixPath(name)
        source = model / 'images' / name
        if (not name or rel.is_absolute() or '..' in rel.parts or '\\' in name
                or rel.as_posix() != name or name in names):
            raise ValueError('Unsafe/duplicate image NAME')
        if not source.is_file() or source.is_symlink() or (model/'images').resolve() not in source.resolve().parents:
            raise ValueError(f'Missing or external pixel file: {name}')
        names.add(name)
        pixels.append((source, roots[2]/'images'/name))
    print('[ITER 2/3] copy components byte-for-byte', flush=True)
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)
    for root, stems in ((roots[0], ('cameras','images','times')),
                        (roots[1], ('points3D','points_t'))):
        for stem in stems:
            shutil.copyfile(tables[stem], root/tables[stem].name)
        shutil.copyfile(meta, root/meta.name)
    (roots[2]/'images').mkdir()
    for source, target in pixels:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    result = dict(images=len(headers), points=n, seconds=time.perf_counter()-started,
                  max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    print('[ITER 3/3] done', flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('model','cams','points','images'):
        parser.add_argument('--'+name, required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(split(args.model, args.cams, args.points, args.images)), flush=True)
