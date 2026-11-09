import importlib.util
import tempfile
import unittest
from pathlib import Path
import colmap4d
from colmap4d import colmap_io as cio

spec = importlib.util.spec_from_file_location('merge', Path(__file__).with_name('merge.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class MergeTest(unittest.TestCase):
    def test_colliding_ids_stable_times_and_distinct_intrinsics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(3):
                p = root/'in'/f'p_{index:016d}'
                p.mkdir(parents=True)
                cio.write_cameras_bin(p/'cameras.bin', {1: cio.Camera(1,'PINHOLE',64,64,[40+index%2,40,32,32])})
                cio.write_images_bin(p/'images.bin', {i: cio.Image(i,f'{i}.png',1,[1,0,0,0],[index,0,0]) for i in (1,2)})
                colmap4d.write_times_bin(p/'times.bin', {1:20,2:10})
                colmap4d.write_time_meta(p/'time_meta.json', dict(colmap4d_spec='1.0',time_unit='ns',time_convention='mid_exposure',clock_domain='test'))
            result = m.merge(root/'in',root/'out')
            self.assertEqual((result['images'],result['intrinsics']), (6,2))
            images = cio.read_images_bin(root/'out/images.bin')
            self.assertEqual(list(images),list(range(1,7)))
            self.assertEqual([i.tvec[0] for i in images.values()], [0,1,2,0,1,2])
            self.assertEqual(list(colmap4d.read_times_bin(root/'out/times.bin').values()),[10]*3+[20]*3)
            meta = colmap4d.read_time_meta(root/'in/p_0000000000000000/time_meta.json')
            meta['clock_domain']='other'
            colmap4d.write_time_meta(root/'in/p_0000000000000000/time_meta.json',meta)
            with self.assertRaisesRegex(ValueError,'incompatible clocks'):
                m.merge(root/'in',root/'bad')
            self.assertFalse((root/'bad').exists())

if __name__ == '__main__':
    unittest.main()
