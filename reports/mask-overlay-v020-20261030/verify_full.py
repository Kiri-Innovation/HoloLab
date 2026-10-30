import json
from pathlib import Path
import statistics

import cv2
import numpy as np
from PIL import Image

r=Path(__file__).resolve().parent;old=r.parent/'mask-overlay-20261030'
root=lambda base,f:Path(json.loads((base/f).read_text())['absolute_path'])
out=root(r,'local-output-path.json');images=root(old,'fx-summary.json');masks=root(old,'workflow-mask-summary.json')
visible=lambda p:sorted(x for x in p.iterdir() if not x.name.startswith('.'))
folders=visible(out);assert len(folders)==80
count=size=0;metrics=[];samples=[];sizes=set()
cv2.setNumThreads(1)
for cam,folder in enumerate(folders):
 files=visible(folder);assert [p.name for p in files]==[f'p_{i:016d}.jpg' for i in range(50)]
 assert (folder/'.hololab-done').exists()
 metric=json.loads((folder/'.metrics.json').read_text());assert metric['count']==50 and metric['format']=='jpg' and metric['mode']=='mask0_black';metrics.append(metric)
 for frame,p in enumerate(files):
  with Image.open(p) as im:
   assert im.mode=='RGB' and im.size==(3840,2160);sizes.add(im.size)
  count+=1;size+=p.stat().st_size
  if cam%5==0 and frame in (0,49):
   rel=Path(f'p_{cam:016d}')/f'p_{frame:016d}.png'
   src=cv2.imread(str(images/rel),cv2.IMREAD_COLOR|cv2.IMREAD_IGNORE_ORIENTATION);mask=cv2.imread(str(masks/rel),0)
   expected=src.copy();expected[mask<128] >>= 1
   ok,encoded=cv2.imencode('.jpg',expected,[cv2.IMWRITE_JPEG_QUALITY,95]);assert ok and encoded.tobytes()==p.read_bytes(),str(p)
   actual=cv2.imread(str(p));delta=actual.astype(np.float32)-expected.astype(np.float32);mse=float(np.mean(delta*delta))
   samples.append({'cam':cam,'frame':frame,'encoded_reference_exact':True,'mean_absolute_error':float(np.mean(np.abs(delta))),'psnr_db':float(10*np.log10(255**2/mse)) if mse else 100.0})
result={'images':count,'shards':80,'all_rgb':True,'sizes':list(sizes),'bytes':size,'samples':samples,
        'shard_seconds':{'min':min(x['elapsed_seconds'] for x in metrics),'median':statistics.median(x['elapsed_seconds'] for x in metrics),'max':max(x['elapsed_seconds'] for x in metrics)},
        'worker_seconds_sum':{key:sum(m['worker_seconds_sum'][key] for m in metrics) for key in ['decode_s','blend_s','encode_write_s']}}
(r/'full-validation.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='samples'}))
