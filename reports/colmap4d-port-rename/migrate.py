import json,copy
from pathlib import Path
import requests
R=Path(__file__).resolve().parent;U='http://127.0.0.1:8828/api'
changes=[]
for summary in requests.get(U+'/workflows').json():
 w=requests.get(U+'/workflows/'+summary['workflow_id']).json();g=copy.deepcopy(w['graph']);affected={};dirty=False
 for n in g['nodes']:
  name=n.get('algorithm_name');v=n.get('algorithm_version')
  if (name,v) in [('k4d/megasam-track','0.7.0'),('col4d/colmap4d-split','0.1.0'),('col4d/merge-colmap4d','0.3.0'),('col4d/frames-to-colmap4d','0.1.0')]:
   affected[n['id']]=name;dirty=True
   if name=='col4d/merge-colmap4d':n['algorithm_version']='0.4.0'
   n.pop('port_types',None);n.pop('latest_run',None)
   if name=='col4d/colmap4d-split' and 'model' in n.get('port_values',{}):n['port_values']['colmap4d']=n['port_values'].pop('model')
 for e in g['edges']:
  if affected.get(e['source']) in ('k4d/megasam-track','col4d/merge-colmap4d','col4d/frames-to-colmap4d') and e['sourceHandle']=='model':e['sourceHandle']='colmap4d'
  if affected.get(e['target'])=='col4d/colmap4d-split' and e['targetHandle']=='model':e['targetHandle']='colmap4d'
 for d in g.get('recipe_dependencies',[]):
  if d['name']=='col4d/merge-colmap4d' and d['version']=='0.3.0' and 'col4d/merge-colmap4d' in affected.values():d['version']='0.4.0';d['hash']=None
 if dirty:
  body=dict(workflow_id=w['workflow_id'],name=w['name'],graph=g,base_updated_ts=w['updated_ts'],origin='colmap4d-port-rename')
  a=requests.post(U+'/workflows',json=body,timeout=120);a.raise_for_status()
  new=requests.get(U+'/workflows/'+w['workflow_id']).json();changes.append(dict(workflow=w['workflow_id'],affected=affected,before_edges=w['graph']['edges'],after_edges=new['graph']['edges']))
(R/'migration.json').write_text(json.dumps(changes,indent=2));print(changes)
allw=[requests.get(U+'/workflows/'+w['workflow_id']).json() for w in requests.get(U+'/workflows').json()]
(R/'workflows-after.json').write_text(json.dumps(allw,indent=2))
