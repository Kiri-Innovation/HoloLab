"""Old exporter + released merge vs direct output, sharing one real native run."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import requests
from colmap4d import read_times_bin
from verify_roundtrip import compare

r=Path(__file__).resolve().parent
repo=r.parents[2]
u='http://127.0.0.1:8828/api'
job=json.loads((r/'megasam-dispatch.json').read_text())['job_id']
for _ in range(3600):
    j=requests.get(u+'/jobs/'+job).json()
    if j['state'] in ('done','failed','cancelled'): break
    time.sleep(2)
assert j['state']=='done',j.get('fail_message')
(r/'megasam-job.json').write_text(json.dumps(j,indent=2))
h=requests.get(u+'/handles/'+j['output_handles']['model']).json()
(r/'megasam-handle.json').write_text(json.dumps(h,indent=2))
model=Path(h['absolute_path']);sparse=model/'sparse/0'
work=repo/'output/colmap4d-split-070/native'
for _ in range(60):
    if (work/'megasam_run/original_resolution.npz').exists(): break
    time.sleep(1)
old=repo/'tryouts/mega-sam/packs/megasam-track@0.6.1/run.py'
spec=importlib.util.spec_from_file_location('old_exporter',old);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
times=read_times_bin(sparse/'times.bin');meta=json.loads((sparse/'time_meta.json').read_text())
# Source 0.6.1 exporter is executed unmodified on the completed native output.
outputs={k:work.parent/'old-components'/k for k in ('cams','points','images')}
start=time.perf_counter()
export=module.export(work,outputs,[times[k] for k in sorted(times)],meta,
                     j['params'].get('depth_min',.001),j['params'].get('pts_stride',2))
export_seconds=time.perf_counter()-start
baseline=work.parent/'old-assembled'
cmd=[sys.executable,str(repo/'hololab/community_packs/merge-colmap4d@0.3.0/merge_colmap4d.py')]
for k,p in outputs.items():cmd.extend(['--'+k,str(p)])
cmd.extend(['--output',str(baseline)])
with (r/'old-export-merge.log').open('w') as log: subprocess.run(cmd,check=True,stdout=log,stderr=subprocess.STDOUT)
compare(model,baseline,r/'megasam-equivalence.json')
(r/'megasam-validation.json').write_text(json.dumps(dict(native_job=job,frames=export.get('images'),
    exporter_061=str(old),export_result=export,export_seconds=export_seconds,
    validation_basis='Same completed real native inference; execute unchanged 0.6.1 export then released merge 0.3.0. No mocked inference; no claim that independent GPU runs are byte-deterministic.'),indent=2))
print('MegaSAM direct equivalence complete',flush=True)
