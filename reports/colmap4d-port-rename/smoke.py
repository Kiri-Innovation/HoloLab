import json,time,hashlib
from pathlib import Path
import requests
R=Path(__file__).resolve().parent;U='http://127.0.0.1:8828/api'
def finish(label):
 job=json.loads((R/(label+'-dispatch.json')).read_text())['job_id']
 for _ in range(900):
  a=requests.get(U+'/jobs/'+job).json()
  if a['state'] in ('done','failed','cancelled'):break
  time.sleep(1)
 (R/(label+'-job.json')).write_text(json.dumps(a,indent=2));(R/(label+'-log.json')).write_text(json.dumps(requests.get(U+'/jobs/'+job+'/log',params={'tail':20000}).json(),indent=2))
 assert a['state']=='done',a.get('fail_message')
 print(label,a['updated_ts']-a['started_ts'],a['output_handles'],flush=True);return a
mega=finish('megasam')
for label,name,version,inputs in [('split','col4d/colmap4d-split','0.1.0',{'colmap4d':mega['output_handles']['colmap4d']})]:
 req=dict(algorithm_name=name,algorithm_version=version,params={},input_handles=inputs,workflow_id=mega['workflow_id'])
 (R/(label+'-request.json')).write_text(json.dumps(req,indent=2));a=requests.post(U+'/jobs/run',json=req);a.raise_for_status();(R/(label+'-dispatch.json')).write_text(a.text)
split=finish('split')
req=dict(algorithm_name='col4d/merge-colmap4d',algorithm_version='0.4.0',params={},input_handles=split['output_handles'],workflow_id=mega['workflow_id'])
(R/'merge-request.json').write_text(json.dumps(req,indent=2));a=requests.post(U+'/jobs/run',json=req);a.raise_for_status();(R/'merge-dispatch.json').write_text(a.text)
merge=finish('merge')
roots=[]
for label,j in [('megasam',mega),('merge',merge)]:
 h=requests.get(U+'/handles/'+j['output_handles']['colmap4d']).json();(R/(label+'-handle.json')).write_text(json.dumps(h,indent=2));roots.append(Path(h['absolute_path']))
def hashes(root):return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and p.name!='.hololab-metadata.json'}
a,b=map(hashes,roots);assert a==b
(R/'smoke-equivalence.json').write_text(json.dumps({'all_equal':True,'files':a,'excluded':['.hololab-metadata.json']},indent=2));print('All',len(a),'content hashes match',flush=True)
