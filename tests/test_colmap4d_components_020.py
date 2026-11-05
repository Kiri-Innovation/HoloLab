"""Strict component assembly and negative contract fixtures, CPU only."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image
import colmap4d
from colmap4d import colmap_io as cio
from colmap4d.validate import exit_code

PACK=Path(__file__).parents[1]/'community_packs/merge-colmap4d@0.2.0/merge_colmap4d.py'
spec=importlib.util.spec_from_file_location('merge020',PACK)
merge=importlib.util.module_from_spec(spec); spec.loader.exec_module(merge)


def fixture(root):
    root=Path(root)
    cams,points,pixels=[root/k for k in ('cams','points','pixels')]
    for p in (cams,points,pixels/'images'): p.mkdir(parents=True)
    cio.write_cameras_bin(cams/'cameras.bin',{7:cio.Camera(7,'PINHOLE',4,4,[4,4,2,2])})
    ims={11:cio.Image(11,'f000000__cam0.png',7,[1,0,0,0],[0,0,0],[(2,2)],[101]),
         31:cio.Image(31,'f000001__cam0.png',7,[1,0,0,0],[-1,0,0],[(2,2)],[900])}
    cio.write_images_bin(cams/'images.bin',ims)
    pts={101:cio.Point3D(101,[0,0,2],[255,0,0],0,[(11,0)]),900:cio.Point3D(900,[1,0,2],[0,255,0],0,[(31,0)])}
    cio.write_points3D_bin(points/'points3D.bin',pts)
    colmap4d.write_times_bin(cams/'times.bin',{11:1000000000000000000,31:1000000000033333333})
    colmap4d.write_points_t_bin(points/'points_t.bin',{101:1000000000000000000,900:1000000000033333333})
    meta=dict(colmap4d_spec='1.0',time_unit='ns',time_convention='mid_exposure',clock_domain='fixture',points_t_method='track_centroid',custom={'keep':True})
    for p in (cams,points): colmap4d.write_time_meta(p/'time_meta.json',meta)
    for image in ims.values(): Image.new('RGB',(4,4),(128,128,128)).save(pixels/'images'/image.name)
    return cams,points,pixels,root/'model'


class Components(unittest.TestCase):
    def test_assembly_preserves_ids_ns(self):
        with tempfile.TemporaryDirectory() as tmp:
            cams,pts,pixels,out=fixture(tmp)
            self.assertEqual(merge.assemble(cams,pts,pixels,out),dict(images=2,points=2,strict_problems=0))
            model=colmap4d.load_model_view(out/'sparse/0')
            self.assertEqual(model.image_ids(),{11,31}); self.assertEqual(model.point_ids(),{101,900})
            self.assertEqual(model.effective_times()[31],1000000000033333333)
            self.assertEqual(exit_code(colmap4d.validate_full(out/'sparse/0'),strict=True),0)
            self.assertEqual(json.loads((out/'sparse/0/time_meta.json').read_text())['custom'],{'keep':True})

    def test_invalid_components(self):
        mutations={
            'missing time':lambda c,p,i: colmap4d.write_times_bin(c/'times.bin',{11:1}),
            'wrong source time':lambda c,p,i: colmap4d.write_points_t_bin(p/'points_t.bin',{101:1,900:2}),
            'metadata conflict':lambda c,p,i: (p/'time_meta.json').write_text((p/'time_meta.json').read_text().replace('fixture','other')),
            'extra pixel':lambda c,p,i: Image.new('RGB',(4,4)).save(i/'images/extra.png'),
            'missing pixel':lambda c,p,i: (i/'images/f000000__cam0.png').unlink(),
            'wrong resolution':lambda c,p,i: Image.new('RGB',(2,2)).save(i/'images/f000000__cam0.png'),
            'wrong mode':lambda c,p,i: Image.new('L',(4,4)).save(i/'images/f000000__cam0.png'),
            'truncated sidecar':lambda c,p,i: (p/'points_t.bin').write_bytes(b'123'),
            'trailing base bytes':lambda c,p,i: (c/'images.bin').write_bytes((c/'images.bin').read_bytes()+b'x'),
        }
        for label,mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                c,p,i,o=fixture(tmp); mutate(c,p,i)
                with self.assertRaises((ValueError,EOFError)): merge.assemble(c,p,i,o)
                self.assertFalse(o.exists())

    def test_tracks_and_duplicate_ids(self):
        for mutation in ('track','duplicate','unsafe'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                c,p,i,o=fixture(tmp)
                if mutation=='track':
                    points=cio.read_points3D_bin(p/'points3D.bin'); points[101].track=[(31,0)]
                    cio.write_points3D_bin(p/'points3D.bin',points)
                elif mutation=='unsafe':
                    images=cio.read_images_bin(c/'images.bin'); images[11].name='../outside.png'
                    cio.write_images_bin(c/'images.bin',images)
                else:
                    import struct
                    raw=(c/'times.bin').read_bytes(); (c/'times.bin').write_bytes(struct.pack('<Q',3)+raw[8:]+raw[8:20])
                with self.assertRaises(ValueError): merge.assemble(c,p,i,o)

    def test_text_empty_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'images.txt'
            path.write_text('# images\n3 1 0 0 0 0 0 0 7 frame.png\n\n5 1 0 0 0 0 0 0 7 frame2.png\n\n')
            self.assertEqual(set(merge.table(Path(tmp),'images')),{3,5})

if __name__=='__main__': unittest.main()
