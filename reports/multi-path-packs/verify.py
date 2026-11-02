import json, tempfile, time
from pathlib import Path
from fastapi.testclient import TestClient
from hololab.gateway.app import create_app
from hololab.node.packs import scan_multi_packs
from hololab.protocol.messages import PackInventoryEntry
from tests.test_scoped_packages import session
roots=[Path('/root/.hololab/node/packs'),Path('/cloud/cloud-ssd1/Kiri4DGS/SpacetimeGaussians/packs')]
start=time.monotonic()
packs=[p for p in scan_multi_packs(roots) if p.manifest.name=='k4d/stg-train' and p.manifest.version in ('0.4.8','0.4.7')]
node=session([])
for p in packs:
 content=p.content
 node.packs.append(PackInventoryEntry(name=p.manifest.name,version=p.manifest.version,manifest_hash=p.manifest_hash,manifest_path=str(p.manifest_path),source_dir=str(p.source_dir),root=p.manifest.root,content=content,code_sha256=content['sha256'] if content else None,locations=p.locations(content)))
with tempfile.TemporaryDirectory() as tmp, TestClient(create_app(db_path=Path(tmp)/'gateway.sqlite')) as client:
 client.app.state.registry._sessions['n']=node
 response=client.get('/api/pack-catalog')
 assert response.status_code==200
 rows=response.json()
 for row in rows:
  loc=row['locations_by_node']['n']
  assert len(loc)==(2 if row['version']=='0.4.8' else 1)
  assert loc[0]['effective']
  if len(loc)>1: assert loc[0]['source_type']=='community_install' and not loc[1]['effective']
 Path('reports/multi-path-packs/api-evidence.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
 print('Verified real paths through isolated API',[(r['version'],len(r['locations_by_node']['n'])) for r in rows], 'seconds',round(time.monotonic()-start,2))
