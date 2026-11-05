import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'merge-colmap4d@0.3.0'))
from test_fast import fixture, hashes
import merge_colmap4d as merge
from colmap4d import colmap_io as cio
from split_colmap4d import split

class Split(unittest.TestCase):
    def test_roundtrip_layouts_and_tracks(self):
        for layout in ('sparse/0', 'sparse', '.'):
            for multi in (False, True):
                with self.subTest(layout=layout,multi=multi), tempfile.TemporaryDirectory() as tmp:
                    root=Path(tmp);c,p,i=fixture(root,multi)
                    merge.assemble(c,p,i,root/'model')
                    expected=hashes(root/'model')
                    if layout != 'sparse/0':
                        target=root/'model'/layout
                        for f in (root/'model/sparse/0').iterdir(): f.rename(target/f.name)
                        (root/'model/sparse/0').rmdir()
                    with patch.object(cio,'read_points3D_bin',side_effect=AssertionError('objects')), patch.object(cio,'read_images_bin',side_effect=AssertionError('observations')):
                        result=split(root/'model',root/'c',root/'p',root/'i')
                    self.assertEqual(result['points'],2)
                    merge.assemble(root/'c',root/'p',root/'i',root/'again')
                    self.assertEqual(expected,hashes(root/'again'))
                    self.assertNotEqual((root/'p/points3D.bin').stat().st_ino,(root/'again/sparse/0/points3D.bin').stat().st_ino)

    def test_overlap_and_missing_pixels_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);c,p,i=fixture(r);merge.assemble(c,p,i,r/'model')
            with self.assertRaisesRegex(ValueError,'overlap'):
                split(r/'model',r/'model/c',r/'p',r/'i')
            (r/'model/images/a.png').unlink()
            with self.assertRaisesRegex(ValueError,'pixel'):
                split(r/'model',r/'c',r/'p',r/'i')
            self.assertFalse((r/'c').exists())

    def test_text_bytes_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);c,p,i=fixture(r);merge.assemble(c,p,i,r/'model')
            sparse=r/'model/sparse/0';f=sparse/'points3D.bin';f.unlink()
            raw=b'101 0 0 2 255 0 0 0 11 0\n900 1 0 2 0 255 0 0 31 0\n'
            (sparse/'points3D.txt').write_bytes(raw)
            split(r/'model',r/'c',r/'p',r/'i')
            self.assertEqual((r/'p/points3D.txt').read_bytes(),raw)

if __name__=='__main__': unittest.main()
