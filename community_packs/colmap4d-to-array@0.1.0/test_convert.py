import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import colmap4d
from colmap4d import colmap_io as cio
from PIL import Image
import convert


def fixture(root):
    s=root/'sparse/0';s.mkdir(parents=True);(root/'images').mkdir()
    cio.write_cameras_bin(s/'cameras.bin',{1:cio.Camera(1,'PINHOLE',4,4,[4,4,2,2])})
    imgs={};pts={};ts={};pt={}
    for i,t in [(1,10**18),(2,10**18),(3,10**18+100)]:
        imgs[i]=cio.Image(i,f'{i}.png',1,[1,0,0,0],[0,0,0],[(1.,1.)],[i*10])
        pts[i*10]=cio.Point3D(i*10,[i,0,2],[255,0,0],0,[(i,0)])
        ts[i]=t;pt[i*10]=t;Image.new('RGB',(4,4)).save(root/'images'/f'{i}.png')
    cio.write_images_bin(s/'images.bin',imgs);cio.write_points3D_bin(s/'points3D.bin',pts)
    colmap4d.write_times_bin(s/'times.bin',ts);colmap4d.write_points_t_bin(s/'points_t.bin',pt)
    colmap4d.write_time_meta(s/'time_meta.json',dict(colmap4d_spec='1.0',time_unit='ns',time_convention='mid_exposure',points_t_method='track_centroid',clock_domain='test'))


class Conversion(unittest.TestCase):
    def test_roundtrip_and_real_builtin_slice(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'source';fixture(source)
            r=convert.to_array(source,root/'arr');self.assertEqual(r['groups'],2)
            for p in (root/'arr').iterdir():
                if p.is_dir(): convert.validate(p)
            convert.from_array(root/'arr',root/'restored')
            for stem in ['cameras','images','points3D','times','points_t']:
                self.assertEqual((source/f'sparse/0/{stem}.bin').read_bytes(),(root/f'restored/sparse/0/{stem}.bin').read_bytes())
            repo=Path(__file__).resolve().parents[2]
            subprocess.run([sys.executable,str(repo/'hololab/builtin_packs/slice@0.1.0/slice.py'),'--input',str(root/'arr'),'--output',str(root/'slice'),'--start','0','--end','1','--step','1'],check=True)
            out=convert.from_array(root/'slice',root/'subset');self.assertEqual(out['points'],2);self.assertEqual(out['images'],2)
            shutil.rmtree(source);shutil.rmtree(root/'arr');shutil.rmtree(root/'slice')
            convert.validate(root/'subset')  # independent files, no dangling links

    def test_duplicate_selection_deduplicates_by_id_not_time(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);fixture(r/'source');convert.to_array(r/'source',r/'arr')
            shutil.copytree(r/'arr/p_0000000000000000',r/'arr/duplicate')
            self.assertEqual(convert.from_array(r/'arr',r/'out')['points'],3)

    def test_modified_or_missing_provenance_rejected(self):
        for mutation in ['provenance','content','clock']:
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as d:
                r=Path(d);fixture(r/'source');convert.to_array(r/'source',r/'arr')
                leaf=r/'arr/p_0000000000000000'
                if mutation=='provenance': (leaf/'.array-view.json').unlink()
                elif mutation=='content': (leaf/'images/1.png').write_bytes(b'bad')
                else:
                    p=leaf/'sparse/0/time_meta.json';m=json.loads(p.read_text());m['clock_domain']='other';p.write_text(json.dumps(m))
                    e=json.loads((leaf/'.array-view.json').read_text());e['files']=convert.files_digest(leaf);(leaf/'.array-view.json').write_text(json.dumps(e))
                with self.assertRaises((ValueError,FileNotFoundError)):convert.from_array(r/'arr',r/'out')


if __name__=='__main__':unittest.main()
