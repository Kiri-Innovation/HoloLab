"""CPU contract/corruption tests, no production input edits."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
import colmap4d
from colmap4d import colmap_io as cio
import merge_colmap4d as merge


def fixture(root, multi=False):
    c,p,i=[root/k for k in ('cams','points','pixels')]
    for d in (c,p,i/'images'):d.mkdir(parents=True)
    cio.write_cameras_bin(c/'cameras.bin',{7:cio.Camera(7,'PINHOLE',4,4,[4,4,2,2])})
    ims={11:cio.Image(11,'a.png',7,[1,0,0,0],[0,0,0],[(2.,2.)],[101]),31:cio.Image(31,'b.png',7,[1,0,0,0],[1,0,0],[(2.,2.)],[900])}
    pts={101:cio.Point3D(101,[0,0,2],[255,0,0],0,[(11,0)]),900:cio.Point3D(900,[1,0,2],[0,255,0],0,[(31,0)])}
    times={11:1000000000000000000,31:1000000000033333333}
    pt={101:times[11],900:times[31]}
    if multi:
        for im in ims.values():im.xys=[(1.,1.),(2.,2.)];im.point3D_ids=[101,900]
        for index,point in enumerate(pts.values()):point.track=[(11,index),(31,index)]
        pt={pid:(sum(times.values())+1)//2 for pid in pts}
    cio.write_images_bin(c/'images.bin',ims);cio.write_points3D_bin(p/'points3D.bin',pts)
    colmap4d.write_times_bin(c/'times.bin',times);colmap4d.write_points_t_bin(p/'points_t.bin',pt)
    meta=dict(colmap4d_spec='1.0',time_unit='ns',time_convention='mid_exposure',clock_domain='test',points_t_method='track_centroid',custom={'keep':True})
    for d in (c,p):colmap4d.write_time_meta(d/'time_meta.json',meta)
    for name in ('a.png','b.png'):Image.new('RGB',(4,4),(0,128,255)).save(i/'images'/name)
    return c,p,i


def write(path,offset,fmt,value):
    with path.open('r+b') as f:f.seek(offset);f.write(struct.pack(fmt,value))


def hashes(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


class Fast(unittest.TestCase):
    def test_byte_equivalence_and_no_object_readers(self):
        for multi in (False,True):
            with self.subTest(multi=multi),tempfile.TemporaryDirectory() as d:
                r=Path(d);c,p,i=fixture(r,multi)
                with patch.object(cio,'read_points3D_bin',side_effect=AssertionError('objects')),patch.object(cio,'read_images_bin',side_effect=AssertionError('observations')):
                    result=merge.assemble(c,p,i,r/'fast')
                self.assertFalse(result['strict_validate'])
                merge.assemble(c,p,i,r/'strict',True)
                self.assertEqual(hashes(r/'fast'),hashes(r/'strict'))
                for f in (r/'fast').rglob('*'):
                    if f.is_file():self.assertEqual(f.stat().st_nlink,1)

    def test_corruptions(self):
        mutations={
            'duplicate points':lambda c,p,i:write(p/'points3D.bin',8+59,'<Q',101),
            'zero point id':lambda c,p,i:write(p/'points3D.bin',8,'<Q',0),
            'nonfinite xyz':lambda c,p,i:write(p/'points3D.bin',16,'<d',float('nan')),
            'negative error':lambda c,p,i:write(p/'points3D.bin',8+35,'<d',-1),
            'wrong source time':lambda c,p,i:write(p/'points_t.bin',16,'<q',123),
            'duplicate time id':lambda c,p,i:write(p/'points_t.bin',24,'<Q',101),
            'truncated points':lambda c,p,i:(p/'points3D.bin').write_bytes((p/'points3D.bin').read_bytes()[:-1]),
            'truncated times':lambda c,p,i:(p/'points_t.bin').write_bytes(b'123'),
            'trailing images':lambda c,p,i:(c/'images.bin').write_bytes((c/'images.bin').read_bytes()+b'x'),
            'duplicate images':lambda c,p,i:write(c/'images.bin',8+64+6+8+24,'<I',11),
            'wrong track image':lambda c,p,i:write(p/'points3D.bin',8+51,'<I',31),
            'wrong track index':lambda c,p,i:write(p/'points3D.bin',8+55,'<I',1),
            'unknown observation':lambda c,p,i:write(c/'images.bin',8+64+6+8+16,'<Q',999),
            'missing reciprocal':lambda c,p,i:write(c/'images.bin',8+64+6+8+16,'<Q',cio.INVALID_POINT3D_ID),
            'nonfinite observation':lambda c,p,i:write(c/'images.bin',8+64+6+8,'<d',float('inf')),
            'missing pixel':lambda c,p,i:(i/'images/b.png').unlink(),
            'extra pixel':lambda c,p,i:Image.new('RGB',(4,4)).save(i/'images/extra.png'),
            'wrong resolution':lambda c,p,i:Image.new('RGB',(2,2)).save(i/'images/a.png'),
            'wrong mode':lambda c,p,i:Image.new('L',(4,4)).save(i/'images/a.png'),
            'meta conflict':lambda c,p,i:(p/'time_meta.json').write_text((p/'time_meta.json').read_text().replace('test','other')),
        }
        for label,mutate in mutations.items():
            with self.subTest(case=label),tempfile.TemporaryDirectory() as d:
                r=Path(d);c,p,i=fixture(r);mutate(c,p,i)
                with self.assertRaises((ValueError,EOFError,struct.error)) as got:merge.assemble(c,p,i,r/'out')
                self.assertFalse((r/'out/.hololab-done').exists())
                print(f'CORRUPTION {label}: rejected: {got.exception}',flush=True)

    def test_multi_track_time_sample(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);c,p,i=fixture(r,True);write(p/'points_t.bin',16,'<q',123)
            with self.assertRaisesRegex(ValueError,'centroid'):merge.assemble(c,p,i,r/'out')

    def test_unsorted_sidecar_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);c,p,i=fixture(r);f=p/'points_t.bin';raw=f.read_bytes();f.write_bytes(raw[:8]+raw[24:]+raw[8:24])
            result=merge.assemble(c,p,i,r/'out');self.assertTrue(result['strict_validate'])

    def test_text_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);c,p,i=fixture(r)
            (p/'points3D.bin').unlink();(p/'points3D.txt').write_text('101 0 0 2 255 0 0 0 11 0\n900 1 0 2 0 255 0 0 31 0\n')
            result=merge.assemble(c,p,i,r/'out');self.assertTrue(result['strict_validate'])

if __name__=='__main__':unittest.main()
