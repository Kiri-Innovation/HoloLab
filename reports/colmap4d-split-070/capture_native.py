"""Retain this verification job's native result before normal scratch cleanup."""
from pathlib import Path
import shutil,time,json
r=Path(__file__).resolve().parent
job=json.loads((r/'megasam-dispatch.json').read_text())['job_id']
scratch=r.parents[2]/'output/hololab/scratch'/job
out=r.parents[2]/'output/colmap4d-split-070/native'
for _ in range(3600):
    work=next(scratch.glob('megasam-070-*'),None)
    if work and (work/'components/cams').is_dir():
        out.mkdir(parents=True,exist_ok=False)
        shutil.copytree(work/'frames',out/'frames')
        shutil.copytree(work/'colmap',out/'colmap')
        (out/'megasam_run').mkdir()
        shutil.copyfile(work/'megasam_run/original_resolution.npz',out/'megasam_run/original_resolution.npz')
        print('Native inference retained:',out,flush=True)
        break
    time.sleep(1)
else: raise TimeoutError('Native export did not appear')
