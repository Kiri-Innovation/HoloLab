"""Complete an independently validated local collection without dispatching HoloLab jobs.
Completed managed artifacts are read-only; never alter job states or their directories.
"""
import importlib.util,json,os,pathlib,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from PIL import Image
import cv2
r=pathlib.Path(__file__).resolve().parent;old=r.parent/'mask-overlay-20261030';pack=r.parents[1]/'community_packs/mask-overlay@0.2.0/run.py'
spec=importlib.util.spec_from_file_location('overlay',pack);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
images=pathlib.Path(json.loads((old/'fx-summary.json').read_text())['absolute_path']);masks=pathlib.Path(json.loads((old/'workflow-mask-summary.json').read_text())['absolute_path'])
managed=pathlib.Path('/cloud/cloud-ssd1/Kiri4DGS/output/hololab/w/586915eb-0a2d-4566-b0f1-f6febd1957b3/j/3a264742-a2a0-46e8-9b16-142f392efb1d/overlay')
out=r/'full-local-verification';out.mkdir();cv2.setNumThreads(1)
def run(cam):
 start=time.perf_counter();name=f'p_{cam:016d}';source=managed/name;dest=out/name
 if (source/'.hololab-done').exists():
  dest.symlink_to(source,target_is_directory=True)
  return {'cam':cam,'reused':50,'computed':0,'elapsed_seconds':0}
 dest.mkdir();reused=0;missing=[]
 for i in range(50):
  prior=source/f'p_{i:016d}.jpg';good=False
  if prior.is_file():
   with prior.open('rb') as f:
    f.seek(-2,2);good=f.read()==b'\xff\xd9'
   if good:
    with Image.open(prior) as im: good=im.mode=='RGB' and im.size==(3840,2160)
  if good:
   (dest/prior.name).symlink_to(prior);reused+=1
  else:missing.append(i)
 totals=dict(decode_s=0.,blend_s=0.,encode_write_s=0.,bytes=0)
 with ThreadPoolExecutor(3) as pool:
  tasks=[pool.submit(m.process_pair,i,images/name/f'p_{i:016d}.png',masks/name/f'p_{i:016d}.png',dest,'mask0_black',0.5,'jpg',1,95) for i in missing]
  for future in as_completed(tasks):
   for key,value in future.result().items():totals[key]+=value
 elapsed=time.perf_counter()-start
 metrics={'count':50,'mode':'mask0_black','alpha':0.5,'format':'jpg','io_workers':3,'elapsed_seconds':elapsed,'reused_images':reused,'computed_images':len(missing),'worker_seconds_sum':totals,'execution':'local completion, not a HoloLab job'}
 (dest/'.metrics.json').write_text(json.dumps(metrics,indent=2));(dest/'.hololab-done').touch()
 return {'cam':cam,'reused':reused,'computed':len(missing),'elapsed_seconds':elapsed}
start=time.perf_counter()
with ThreadPoolExecutor(4) as pool:
 results=[]
 for result in pool.map(run,range(80)):
  results.append(result);print(result,flush=True)
summary={'elapsed_seconds':time.perf_counter()-start,'reused_images':sum(x['reused'] for x in results),'computed_images':sum(x['computed'] for x in results),'images':4000,'output':str(out),'scope':'local completion only; workflow remains failed/cancelled','groups':results}
(r/'local-completion.json').write_text(json.dumps(summary,indent=2));(r/'local-output-path.json').write_text(json.dumps({'absolute_path':str(out)},indent=2));print(summary,flush=True)
