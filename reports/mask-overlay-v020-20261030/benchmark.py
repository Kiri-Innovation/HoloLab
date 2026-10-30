import importlib.util
import json
from pathlib import Path
import time

r=Path(__file__).resolve().parent
old=r.parent/'mask-overlay-20261030'
pack=r.parents[1]/'community_packs/mask-overlay@0.2.0/run.py'
spec=importlib.util.spec_from_file_location('overlay',pack);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
images=Path(json.loads((old/'fx-summary.json').read_text())['absolute_path'])/'p_0000000000000000'
masks=Path(json.loads((old/'workflow-mask-summary.json').read_text())['absolute_path'])/'p_0000000000000000'
bench=[]
for fmt in ['png','jpg']:
 out=r/('bench-'+fmt)
 m.overlay_group(images,masks,out,mode='mask0_black',io_workers=3,format=fmt)
 d=json.loads((out/'.metrics.json').read_text());d['ideal_4000_s_at_4_shards']=d['elapsed_seconds']*80/4;bench.append(d)
 (r/'benchmark.json').write_text(json.dumps(bench,indent=2))
# One source pair, four modes; lossless samples isolate exact semantics.
samples=r/'samples';samples.mkdir(exist_ok=True)
for mode in m.MODES:
 out=samples/mode;out.mkdir()
 m.process_pair(0,sorted(images.glob('*.png'))[0],sorted(masks.glob('*.png'))[0],out,mode,0.5,'png',1,95)
print(json.dumps(bench),flush=True)
