"""Frozen pre-migration output, including unreadable/unmeasured cases."""
import json
import os
import struct
from pathlib import Path

from PIL import Image

from hololab.gateway.handles import Handle
from hololab.gateway.handle_summary import summarize_handle


def cases(root):
    root.mkdir(exist_ok=True)
    sparse = root / 'sparse'
    sparse.mkdir()
    (sparse / 'cameras.txt').write_text('# models\n1 PINHOLE 16 12 8 8 8 6\n')
    (sparse / 'images.txt').write_text('# Number of images: 3, mean observations: 2\n1 pose\n')
    (sparse / 'points3D.txt').write_text('# Number of points: 5\n1 point\n2 point\n')
    images = sparse / 'images'
    images.mkdir()
    (images / 'a.png').write_bytes(b'opaque')
    (images / '.hidden').write_bytes(b'opaque')
    for suffix in ('png', 'jpg', 'gif'):
        Image.new('RGB', (16, 12), 'red').save(root / ('image.' + suffix))
    (root / 'count.txt').write_text(' -12\n')
    (root / 'bad.txt').write_bytes(b'not an integer\xff\n')
    (root / 'movie.mp4').write_bytes(b'opaque video')
    os.utime(root / 'movie.mp4', (100000, 100000))
    header = json.dumps([{'texwidth': 4, 'size': 64, 'cameras': [{}, {}], 'type': 'test'}]).encode()
    (root / 'scene.splatv').write_bytes(struct.pack('<HHI', 0x674b, 0, len(header)) + header)
    (root / 'short.splatv').write_bytes(b'bad')
    (root / 'magic.splatv').write_bytes(b'abcdefgh')
    (root / 'window.splatv').write_bytes(struct.pack('<HHI', 0x674b, 0, 999999))
    (root / 'json.splatv').write_bytes(struct.pack('<HHI', 0x674b, 0, 1) + b'{')
    (root / 'empty').mkdir()
    (root / 'opaque').write_bytes(b'opaque')
    entries = []
    for tag in ('colmap-cameras-txt', 'colmap-points-txt', 'colmap-images-txt', 'colmap-cams', 'point-cloud', 'colmap-folder', 'pose', 'intr', 'colmap'):
        for name in ('sparse', 'empty', 'missing'):
            entries.append((tag + ':' + name, name, 'dir', [tag], 0, None))
    for name, tags in [('count.txt',['int']), ('bad.txt',['int']), ('missing.txt',['int']),
                       ('image.png',['image']), ('image.jpg',[]), ('image.gif',[]), ('missing.png',['image']),
                       ('opaque',['image']), ('movie.mp4',[]), ('missing.mp4',[]), ('count.txt',[]),
                       ('bad.txt',[]), ('missing.txt',[]), ('opaque',[]), ('scene.splatv',[]),
                       ('short.splatv',['splatv']), ('magic.splatv',[]), ('window.splatv',[]), ('json.splatv',[])]:
        entries.append((name + ':' + ','.join(tags), name, 'file', tags, 0, None))
    for depth in (None, 0, 1, 2):
        entries.append(('collection:' + str(depth), 'sparse', 'dir', ['colmap'], depth, ['outer','inner'][:depth] if depth else None))
    return [(key, Handle(key, 'golden', storage=storage, tags=tags, path=str(root / name)), depth, labels)
            for key, name, storage, tags, depth, labels in entries]


def normalize(value, root):
    return json.loads(json.dumps(value).replace(str(root), '<ROOT>'))


def test_pre_migration_golden(tmp_path):
    expected = json.loads((Path(__file__).parent / 'fixtures/type_summary_golden.json').read_text())
    actual = {key: normalize(summarize_handle(handle, depth=depth, dim_labels=labels), tmp_path)
              for key, handle, depth, labels in cases(tmp_path)}
    assert actual == expected
