import json
from pathlib import Path
import tempfile
import unittest

import colmap4d
from colmap4d import colmap_io as cio
import numpy as np

from capture import capture, load_cloud, load_camera, select_rows, window_ns, MIN_NS, MAX_NS


def camera(path, timestamp):
    path.write_text(json.dumps(dict(model='PINHOLE', width=64, height=48,
        params=[50, 51, 32, 24], qvec=[1, 0, 0, 0], tvec=[0, 0, 0], timestamp_ns=timestamp)))
    return path


def cloud(root, times, ids=None, text=False):
    root.mkdir()
    ids = list(times) if ids is None else ids
    points = {i: cio.Point3D(i, [i+.125, -i, i/3], [10, 20, 30], .5, [(5, 2)]) for i in ids}
    if text:
        (root/'points3D.txt').write_text(''.join(f'{i} {i+.125} {-i} {i/3} 10 20 30 .5 5 2\n' for i in ids))
        colmap4d.write_points_t_txt(root/'points_t.txt', times)
    else:
        cio.write_points3D_bin(root/'points3D.bin', points)
        colmap4d.write_points_t_bin(root/'points_t.bin', times)
    colmap4d.write_time_meta(root/'time_meta.json', dict(time_unit='ns', clock_domain='test'))
    return root


class CaptureTests(unittest.TestCase):
    def test_seconds_and_fractional_ns(self):
        self.assertEqual(window_ns(.03), 30_000_000)
        self.assertEqual(window_ns(1e-9), 1)
        self.assertEqual(window_ns(.9e-9), 0)
        self.assertEqual(window_ns(1e300), MAX_NS-MIN_NS)
        for value in (-1, float('nan'), float('inf'), True, '0.03'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                window_ns(value)

    def test_inclusive_boundaries_and_static_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); epoch=1_700_000_000_000_000_000
            times={1:epoch-30_000_001, 2:epoch-30_000_000, 3:epoch, 4:epoch+30_000_000, 5:epoch+30_000_001}
            source=cloud(root/'cloud', times); cam=camera(root/'cam.json',epoch)
            info=capture(cam, source, root/'out')
            points=cio.read_points3D_txt(root/'out/points3D.txt')
            self.assertEqual(set(points), {2,3,4});self.assertEqual(info['count'],3)
            self.assertEqual(list((root/'out').iterdir()),[root/'out/points3D.txt'])
            for i,p in points.items():
                self.assertEqual(p.xyz,[i+.125,-i,i/3]);self.assertEqual(p.rgb,[10,20,30])
                self.assertEqual(p.track,[]);self.assertEqual(p.error,0)
            capture(cam,source,root/'instant',0)
            self.assertEqual(set(cio.read_points3D_txt(root/'instant/points3D.txt')),{3})

    def test_empty_windows_and_empty_cloud(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=cloud(root/'cloud',{1:0});cam=camera(root/'cam.json',1_000_000_000)
            self.assertEqual(capture(cam,source,root/'out')['count'],0)
            self.assertEqual(cio.read_points3D_txt(root/'out/points3D.txt'),{})
            empty=cloud(root/'empty',{})
            self.assertEqual(capture(cam,empty,root/'empty-output')['count'],0)

    def test_join_by_id_partial_times_and_text(self):
        for text in (False, True):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);source=cloud(root/'cloud',{9:0, 2:100, 99:0},ids=[2,9,5],text=text)
                data=load_cloud(source)
                self.assertEqual(data.untimed_count,1);self.assertEqual(data.dangling_count,1)
                selected,_=select_rows(data,0,0)
                np.testing.assert_array_equal(data.ids[selected],[9])

    def test_int64_edges_do_not_overflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=cloud(Path(tmp)/'cloud',{1:MIN_NS,2:MAX_NS,3:MIN_NS+1,4:MAX_NS-1})
            data=load_cloud(source)
            for center,wanted in ((MIN_NS,{1,3}),(MAX_NS,{2,4})):
                selected,_=select_rows(data,center,1e-9)
                self.assertEqual(set(map(int,data.ids[selected])),wanted)

    def test_invalid_camera_metadata_and_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=cloud(root/'cloud',{1:0});cam=camera(root/'cam.json',0)
            for changes in ({'timestamp_ns':True},{'timestamp_ns':MAX_NS+1},{'qvec':[0,0,0,0]},{'model':'invalid'}):
                original=json.loads(cam.read_text());cam.write_text(json.dumps({**original,**changes}))
                with self.assertRaises(ValueError):load_camera(cam)
                camera(cam,0)
            timeless=json.loads(cam.read_text());del timeless['timestamp_ns'];cam.write_text(json.dumps(timeless))
            with self.assertRaises(ValueError):load_camera(cam)
            camera(cam,0)
            with self.assertRaises(ValueError):capture(cam,source,source/'nested')
            meta=source/'time_meta.json';meta.write_text('{"time_unit":"s","clock_domain":"test"}')
            with self.assertRaises(ValueError):load_cloud(source)


if __name__=='__main__':
    unittest.main()
