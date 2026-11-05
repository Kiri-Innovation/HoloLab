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

    def test_video_selection_exposure_and_native_boundary(self):
        """Real ffmpeg decode, controlled CPU tracker fixture (not inference evidence)."""
        import hashlib
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); video=root/'input.mp4'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i',
                'testsrc2=size=8x8:rate=4:duration=1','-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True,timeout=30)
            repo=root/'mega'; repo.mkdir(); (repo/'sentinel').write_text('original')
            sharp=root/'sharp'; (sharp/'per-frame').mkdir(parents=True)
            (sharp/'per-frame/video_to_colmap.py').write_text(r"""import argparse
from pathlib import Path
import numpy as np
from PIL import Image
p=argparse.ArgumentParser();p.add_argument('--convert-only',action='store_true');p.add_argument('--track_megasam',action='store_true');p.add_argument('--megasam-root');p.add_argument('-o');a=p.parse_args()
assert a.convert_only and a.track_megasam
Path(a.megasam_root,'native-marker').write_text('private')
w=Path(a.o); frames=sorted((w/'frames').glob('*.png')); width,height=Image.open(frames[0]).size
s=w/'colmap/sparse/0';s.mkdir(parents=True);(w/'megasam_run').mkdir()
(s/'cameras.txt').write_text(f'1 PINHOLE {width} {height} 4 4 4 4\n')
(s/'images.txt').write_text(''.join(f'{i+1} 1 0 0 0 {i} 0 0 1 {f.name}\n\n' for i,f in enumerate(frames)))
np.savez(w/'megasam_run/original_resolution.npz',depths=np.ones((len(frames),height,width)))
""")
            exposure=root/'exposure.json'
            exposure.write_text(json.dumps(dict(video_sha256=hashlib.sha256(video.read_bytes()).hexdigest(),clock_domain='sensor',frames=[
                dict(decoded_index=0,mid_exposure_ns=101),dict(decoded_index=2,mid_exposure_ns=908)])))
            outputs={k:str(root/k) for k in ('cams','points','images')}
            mega.main(dict(inputs={'frames':str(video)},outputs={'4d_'+k:v for k,v in outputs.items()},scratch=str(root/'scratch'),params=dict(
                megasam_repo=str(repo),sharp_repo=str(sharp),tracker_python=sys.executable,skip=2,max_frames=2,
                exposure_metadata_path=str(exposure))))
            self.assertEqual(read_times_bin(Path(outputs['cams'])/'times.bin'),{1:101,2:908})
            self.assertFalse((repo/'native-marker').exists())
            meta=json.loads((Path(outputs['cams'])/'time_meta.json').read_text())
            self.assertEqual(meta['conversion']['selected_decoded_indexes'],[0,2])
            self.assertEqual(meta['conversion']['pixels']['output'],'sRGB RGB8')
            frames=root/'input-frames'; frames.mkdir()
            for j in range(4): Image.new('RGB',(8,8),(j*40,20,30)).save(frames/f'{j:04d}.png')
            frame_outputs={k:str(root/('array-'+k)) for k in outputs}
            mega.main(dict(inputs={'frames':str(frames)},outputs={'4d_'+k:v for k,v in frame_outputs.items()},
                scratch=str(root/'array-scratch'),params=dict(megasam_repo=str(repo),sharp_repo=str(sharp),
                tracker_python=sys.executable,skip=2,max_frames=2,frames_fps=25)))
            self.assertEqual(read_times_bin(Path(frame_outputs['cams'])/'times.bin'),{1:0,2:80000000})
            # Both full adapter branches must compose into a strict complete model.
            from test_colmap4d_components_020 import merge
            for label,bundle in [('video',outputs),('array',frame_outputs)]:
                result=merge.assemble(bundle['cams'],bundle['points'],bundle['images'],root/(label+'-model'))
                self.assertEqual(result['strict_problems'],0)


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
            # Native MegaSAM stores two-line text image records with blank observations.
            (sparse/'images.bin').unlink()
            (sparse/'images.txt').write_text(''.join(
                f'{im.id} 1 0 0 0 {im.tvec[0]} 0 0 7 {im.name}\n\n' for im in ims.values()))
            text_outputs={k:Path(tmp)/('text-'+k) for k in outputs}
            mega.export(work,text_outputs,[-1,41],meta,pts_stride=1)
            self.assertEqual((outputs['points']/'points3D.bin').read_bytes(),
                             (text_outputs['points']/'points3D.bin').read_bytes())

if __name__=='__main__': unittest.main()
