"""Compare every model file, excluding only per-job HoloLab provenance."""
import hashlib
import json
from pathlib import Path
import time
import requests

REPORT=Path(__file__).resolve().parent
API='http://127.0.0.1:8828/api'

def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()

def compare(a,b,destination):
    started=time.perf_counter()
    def files(root):
        return {str(p.relative_to(root)):p for p in root.rglob('*') if p.is_file() and p.name!='.hololab-metadata.json'}
    fa,fb=files(a),files(b)
    assert fa.keys()==fb.keys(),(fa.keys()-fb.keys(),fb.keys()-fa.keys())
    records=[]
    for key in sorted(fa):
        x,y=fa[key],fb[key];ha,hb=digest(x),digest(y)
        records.append(dict(file=key,bytes=x.stat().st_size,left_sha256=ha,right_sha256=hb,equal=ha==hb))
    result=dict(left=str(a),right=str(b),files=records,all_equal=all(v['equal'] for v in records),
                total_bytes=sum(v['bytes'] for v in records),seconds=time.perf_counter()-started,
                excluded=['.hololab-metadata.json (per-job provenance)'])
    destination.write_text(json.dumps(result,indent=2)+'\n')
    print(destination.name,result['all_equal'],len(records),result['total_bytes'],flush=True)
    assert result['all_equal']
    return result

if __name__=='__main__':
    roots=[]
    for label in ('baseline','roundtrip'):
        j=json.loads((REPORT/(label+'-job.json')).read_text())
        h=requests.get(API+'/handles/'+j['output_handles']['model']).json()
        (REPORT/(label+'-handle.json')).write_text(json.dumps(h,indent=2))
        roots.append(Path(h['absolute_path']))
    compare(*roots,REPORT/'roundtrip-equivalence.json')
