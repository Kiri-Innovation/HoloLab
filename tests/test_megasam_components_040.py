"""CPU contract tests; no algorithm inference or GPU required."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from PIL import Image
from colmap4d import colmap_io as cio, read_points_t_bin, read_times_bin

PACK = Path(__file__).parents[1]/'community_packs/megasam-track@0.4.0/run.py'
spec=importlib.util.spec_from_file_location('mega040',PACK)
mega=importlib.util.module_from_spec(spec); spec.loader.exec_module(mega)

class MegaComponents(unittest.TestCase):
    def test_time_policies(self):
        t,m=mega.timestamps({'timestamp_mode':'synthetic_uniform','fps_num':30000,'fps_den':1001},[0,3],[],None,'hash')
        self.assertEqual(t,[0,100100000]); self.assertTrue(m['conversion']['estimated'])
        t,_=mega.timestamps({'timestamp_mode':'pts_estimate'},[0,2],[{'pts':-2},{'pts':1},{'pts':9}],'1/1000','hash')
        self.assertEqual(t,[-2000000,9000000])
        with self.assertRaises(ValueError): mega.timestamps({},[0],[],None,'hash')
        with self.assertRaises(ValueError): mega.timestamps({'timestamp_mode':'synthetic_uniform','fps_num':True,'fps_den':1},[0],[],None,'hash')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'exposure.json'
            data={'video_sha256':'hash','clock_domain':'sensor','frames':[{'decoded_index':3,'exposure_start_ns':10,'exposure_ns':5}]}
            path.write_text(json.dumps(data))
            params={'exposure_metadata_path':str(path)}
            self.assertEqual(mega.timestamps(params,[3],[],None,'hash')[0],[12])
            with self.assertRaises(ValueError): mega.timestamps(params,[0],[],None,'hash')
            with self.assertRaises(ValueError): mega.timestamps(params,[3],[],None,'wrong')
            data['frames']*=2; path.write_text(json.dumps(data))
            with self.assertRaises(ValueError): mega.timestamps(params,[3],[],None,'hash')

    def test_export_geometry_and_tracks(self):
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp)/'native'; sparse=work/'colmap/sparse/0'; sparse.mkdir(parents=True)
            (work/'frames').mkdir(); (work/'megasam_run').mkdir()
            cio.write_cameras_bin(sparse/'cameras.bin',{7:cio.Camera(7,'PINHOLE',2,2,[2,2,0,0])})
            ims={i+4:cio.Image(i+4,f'frame_{i:06d}.png',7,[1,0,0,0],[i,0,0]) for i in range(2)}
            cio.write_images_bin(sparse/'images.bin',ims); cio.write_points3D_bin(sparse/'points3D.bin',{})
            for i in range(2): Image.new('RGB',(2,2),(10,20,30)).save(work/f'frames/frame_{i:06d}.png')
            np.savez(work/'megasam_run/original_resolution.npz',depths=np.array([[[2,0],[np.nan,2]],[[2,2],[2,2]]]))
            outputs={k:Path(tmp)/k for k in ('cams','points','images')}
            meta={'colmap4d_spec':'1.0','time_unit':'ns','time_convention':'mid_exposure','clock_domain':'test','points_t_method':'track_centroid'}
            self.assertEqual(mega.export(work,outputs,[-1,41],meta,pts_stride=1),{'images':2,'points':6})
            points=cio.read_points3D_bin(outputs['points']/'points3D.bin')
            images=cio.read_images_bin(outputs['cams']/'images.bin')
            pt=read_points_t_bin(outputs['points']/'points_t.bin'); ts=read_times_bin(outputs['cams']/'times.bin')
            self.assertEqual(points[3].xyz,[-1,0,2])
            for pid,point in points.items():
                iid,idx=point.track[0]
                self.assertEqual(images[iid].point3D_ids[idx],pid)
                self.assertEqual(pt[pid],ts[iid])
            self.assertEqual(len(list((outputs['images']/'images').glob('*.png'))),2)

if __name__=='__main__': unittest.main()
