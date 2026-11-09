import json
import tempfile
import unittest
import warnings
from pathlib import Path

from colmap4d import colmap_io as cio, sidecar
from trim import trim


def fixture(root):
    s = root/'sparse/0'; s.mkdir(parents=True)
    (root/'images').mkdir()
    cameras = {1: cio.Camera(1, 'PINHOLE', 2, 2, [2, 2, 1, 1]),
               2: cio.Camera(2, 'PINHOLE', 2, 2, [2, 2, 1, 1])}
    # Unsorted IDs/times; two cameras at one timestamp; exact int64 arithmetic.
    ts = {3: 2**60+20, 1: 2**60, 4: 2**60+10, 2: 2**60+10}
    ims = {}; pts = {}
    for i, t in ts.items():
        ims[i] = cio.Image(i, f'{i}.png', 2 if i == 3 else 1, [1,0,0,0], [0,0,0], [(0,0)], [i])
        pts[i] = cio.Point3D(i, [0,0,1], [0,0,0], 0, [(i,0)])
        (root/'images'/f'{i}.png').write_bytes(b'pixel bytes preserved')
    cio.write_cameras_bin(s/'cameras.bin', cameras)
    cio.write_images_bin(s/'images.bin', ims)
    cio.write_points3D_bin(s/'points3D.bin', pts)
    sidecar.write_times_bin(s/'times.bin', ts)
    sidecar.write_points_t_bin(s/'points_t.bin', ts)
    sidecar.write_time_meta(s/'time_meta.json', dict(colmap4d_spec='1.0',time_unit='ns',time_convention='mid_exposure',clock_domain='original',points_t_method='track_centroid',custom={'kept': True}))
    return s


class TrimTests(unittest.TestCase):
    def test_all_types_and_boundaries(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as d:
            r=Path(d); s=fixture(r/'src'); a=2**60
            result=trim(r/'src', r/'model', a, a+20)
            self.assertEqual(result['images'],3)
            self.assertEqual(result['points'],3)
            dest=r/'model/sparse/0'
            self.assertEqual(set(cio.read_cameras_bin(dest/'cameras.bin')),{1})
            self.assertEqual(set(cio.read_images_bin(dest/'images.bin')),{1,2,4})
            self.assertEqual((s/'time_meta.json').read_bytes(),(dest/'time_meta.json').read_bytes())
            import shutil
            for name,stems in [('cams',['cameras','images','times']),('points',['points3D','points_t'])]:
                source=r/name;source.mkdir()
                for stem in stems: shutil.copyfile(s/f'{stem}.bin',source/f'{stem}.bin')
                shutil.copyfile(s/'time_meta.json',source/'time_meta.json')
                trim(source,r/(name+'-out'),a,a+20)
                for stem in stems:
                    self.assertEqual((dest/f'{stem}.bin').read_bytes(),(r/(name+'-out')/f'{stem}.bin').read_bytes())
            pixels=r/'pixels';shutil.copytree(r/'src/images',pixels/'images')
            for f in ['times.bin','time_meta.json']:shutil.copyfile(s/f,pixels/f)
            (pixels/'image_names.json').write_text(json.dumps({str(i):f'{i}.png' for i in [1,2,3,4]}))
            trim(pixels,r/'pixels-out',a,a+20)
            self.assertEqual(len(list((r/'pixels-out/images').iterdir())),3)
            for start,end in [(a,a),(a+20,a+10),(a+30,-1),(a+1,a+2)]:
                with self.assertRaises(ValueError):trim(r/'src',r/'bad',start,end)
                self.assertFalse((r/'bad').exists())
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always');trim(r/'src',r/'clipped',a-1,a+100)
                self.assertTrue(caught)
            self.assertEqual(trim(r/'src',r/'tail',a+20,-1)['images'],1)
            for v in [True,1.0,2**63,-2**63-1]:
                with self.assertRaises(ValueError):trim(r/'src',r/'bad',v,-1)
            with self.assertRaises(ValueError):trim(r/'src',r/'src/sub',a,-1)
            with self.assertRaises(ValueError):trim(r/'src',r/'model',a,-1)

    def test_cross_window_links(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as d:
            r=Path(d);s=fixture(r/'src');a=2**60
            ims=cio.read_images_bin(s/'images.bin');points=cio.read_points3D_bin(s/'points3D.bin')
            # A retained point has an outside observation; an outside point is
            # observed by a retained image. Independent provenance permits this.
            points[1].track.append((3,1));ims[3].xys.append((0,0));ims[3].point3D_ids.append(1)
            points[3].track.append((1,1));ims[1].xys.append((0,0));ims[1].point3D_ids.append(3)
            cio.write_images_bin(s/'images.bin',ims);cio.write_points3D_bin(s/'points3D.bin',points)
            meta=sidecar.read_time_meta(s/'time_meta.json');meta['points_t_method']='independent';sidecar.write_time_meta(s/'time_meta.json',meta)
            trim(r/'src',r/'out',a,a+20)
            out=r/'out/sparse/0'
            self.assertEqual(cio.read_points3D_bin(out/'points3D.bin')[1].track,[(1,0)])
            self.assertEqual(cio.read_images_bin(out/'images.bin')[1].point3D_ids,[1,cio.INVALID_POINT3D_ID])

    def test_text_tables_and_centroid_conflict(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as d:
            r=Path(d); source=r/'cams'; source.mkdir()
            (source/'cameras.txt').write_text('1 PINHOLE 2 2 2 2 1 1\n')
            (source/'images.txt').write_text('1 1 0 0 0 0 0 0 1 a.png\n0 0 18446744073709551615\n2 1 0 0 0 0 0 0 1 b.png\n0 0 18446744073709551615\n')
            sidecar.write_times_txt(source/'times.txt',{1:100,2:200})
            sidecar.write_time_meta(source/'time_meta.json',{'clock_domain':'text'})
            self.assertEqual(trim(source,r/'out',100,200)['images'],1)
            s=fixture(r/'model');a=2**60
            ims=cio.read_images_bin(s/'images.bin');points=cio.read_points3D_bin(s/'points3D.bin')
            # Point at the in-window centroid of two observations; pruning the
            # outside one would change its claimed time.
            points[2].track=[(1,1),(3,1)]
            for iid in (1,3):
                ims[iid].xys.append((0,0));ims[iid].point3D_ids.append(2)
            cio.write_images_bin(s/'images.bin',ims);cio.write_points3D_bin(s/'points3D.bin',points)
            with self.assertRaisesRegex(ValueError,'track-centroid'):
                trim(r/'model',r/'bad',a,a+20)
            self.assertFalse((r/'bad').exists())

    def test_untimed_pixels_rejected(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as d:
            r=Path(d);(r/'source/images').mkdir(parents=True)
            with self.assertRaisesRegex(ValueError,'times'):trim(r/'source',r/'out')


if __name__=='__main__':unittest.main()
