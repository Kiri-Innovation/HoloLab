"""CAS-protected migration of the requested draft, preserving input parameters."""
import copy,json
from pathlib import Path
import requests
R=Path(__file__).resolve().parent
U='http://127.0.0.1:8828/api'
ID='9967b1f8-3646-41f5-a269-394b6d392169'
old=requests.get(U+'/workflows/'+ID).json()
g=copy.deepcopy(old['graph'])
mega=next(n for n in g['nodes'] if n['id']=='nmw38vr8y4')
assert mega['algorithm_name']=='k4d/megasam-track' and mega['algorithm_version']=='0.6.1'
merge=next(n for n in g['nodes'] if n['id']=='nmw3rj68z2')
assert merge['algorithm_name']=='col4d/merge-colmap4d'
mega['algorithm_version']='0.7.0'
# Stored port contracts must reflect the new version; gateway derives fresh ones.
mega.pop('port_types',None)
mega.pop('latest_run',None)
g['nodes']=[n for n in g['nodes'] if n['id']!=merge['id']]
edges=[]
for e in g['edges']:
    if e['target']==merge['id']:
        assert e['source']==mega['id']
        continue
    if e['source']==merge['id']:
        assert e['sourceHandle']=='model'
        e['source']=mega['id'];e['sourceHandle']='model'
    edges.append(e)
g['edges']=edges
for d in g['recipe_dependencies']:
    if d['name']=='k4d/megasam-track':
        d['version']='0.7.0';d['hash']=None
        d['requires']['packages'][0]='colmap4d>=1.0.4'
if not any(n.get('algorithm_name')=='col4d/merge-colmap4d' for n in g['nodes']):
    g['recipe_dependencies']=[d for d in g['recipe_dependencies'] if d['name']!='col4d/merge-colmap4d']
body=dict(workflow_id=ID,name=old['name'],graph=g,base_updated_ts=old['updated_ts'],origin='colmap4d-070-migration')
(R/'migration-request.json').write_text(json.dumps(body,indent=2))
r=requests.post(U+'/workflows',json=body)
(R/'migration-response.json').write_text(r.text)
r.raise_for_status()
after=requests.get(U+'/workflows/'+ID).json()
(R/'workflow-after.json').write_text(json.dumps(after,indent=2))
newmega=next(n for n in after['graph']['nodes'] if n['id']==mega['id'])
assert newmega['params']==mega['params']
assert newmega['algorithm_version']=='0.7.0'
assert all(e['sourceHandle'] not in ('4d_cams','4d_points','4d_images') for e in after['graph']['edges'] if e['source']==mega['id'])
print('Migrated',len(old['graph']['nodes']),'→',len(after['graph']['nodes']),'nodes;',len(old['graph']['edges']),'→',len(after['graph']['edges']),'edges. Params unchanged.')
