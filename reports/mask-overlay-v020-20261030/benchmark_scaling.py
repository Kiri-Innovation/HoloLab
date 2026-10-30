import importlib.util,json,pathlib,subprocess,time
from concurrent.futures import ThreadPoolExecutor
r=pathlib.Path(__file__).resolve().parent;old=r.parent/'mask-overlay-20261030';pack=r.parents[1]/'community_packs/mask-overlay@0.2.0/run.py'
s=importlib.util.spec_from_file_location('overlay',pack);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
images=pathlib.Path(json.loads((old/'fx-summary.json').read_text())['absolute_path']);masks=pathlib.Path(json.loads((old/'workflow-mask-summary.json').read_text())['absolute_path'])
m.overlay_group(images/'p_0000000000000000',masks/'p_0000000000000000',r/'bench-blend50-png',mode='blend50',io_workers=3)
def run(cam):
 out=r/f'scaling-jpg-{cam}';cmd=['/cloud/cloud-ssd1/envs/kiri/bin/python',str(pack),'--image',str(images/f'p_{cam:016d}'),'--mask',str(masks/f'p_{cam:016d}'),'--output',str(out),'--format','jpg','--io-workers','3']
 with (r/f'scaling-{cam}.log').open('w') as log: subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
 return json.loads((out/'.metrics.json').read_text())
t=time.perf_counter()
with ThreadPoolExecutor(4) as pool:results=list(pool.map(run,[0,20,40,60]))
elapsed=time.perf_counter()-t;summary={'images':200,'parallelism':4,'io_workers':3,'format':'jpg','wall_seconds':elapsed,'images_per_second':200/elapsed,'projected_4000_seconds':elapsed*20,'shards':results};(r/'scaling-benchmark.json').write_text(json.dumps(summary,indent=2));print(summary)
