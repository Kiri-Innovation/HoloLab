"""Local real-data demo: compile a scalar->slice graph, execute its manifests.

This is not a live gateway dispatch. The existing producer output is explicitly
seeded. Converter nodes are chosen by the platform planner, not this runner.
"""
import json,os,subprocess,sys,time
from pathlib import Path
from hololab.manifest import load_manifest,render_manifest,RenderContext
from hololab.gateway.preparation import resolve_recipe
from hololab.gateway.workflows import WorkflowGraph,GraphNode,GraphEdge,execution_topological_order
repo=Path(__file__).resolve().parents[2]
output=repo.parent/'output/array-view-validation-20261105/compiled-demo'
source=repo.parent/'output/hololab/w/9967b1f8-3646-41f5-a269-394b6d392169/j/a31dba7e-9ca5-4c04-8d4b-c915a5a30937/model'
paths=[repo/'community_packs/merge-colmap4d@0.3.0',repo/'community_packs/colmap4d-to-array@0.1.0',repo/'community_packs/colmap4d-from-array@0.1.0',repo/'hololab/builtin_packs/slice@0.1.0']
loaded={};catalog=[]
for path in paths:
 m,_=load_manifest(path/'manifest.yaml');loaded[m.name,m.version]=(m,path);catalog.append(m.model_dump(mode='json'))
graph=WorkflowGraph(nodes=[GraphNode(id='existing-model',algorithm_name='col4d/merge-colmap4d',algorithm_version='0.3.0',assigned_node_id='local-demo'),GraphNode(id='slice',algorithm_name='builtin/slice',algorithm_version='0.1.0',assigned_node_id='local-demo',params={'start':30,'end':32,'step':1})],edges=[GraphEdge(id='scalar-to-slice',source='existing-model',sourceHandle='model',target='slice',targetHandle='in')])
prepared=resolve_recipe(graph,catalog)
report=Path(__file__).parent
(report/'compiled-plan.json').write_text(prepared.model_dump_json(indent=2))
outputs={'existing-model':{'model':str(source)}};records=[];started=time.perf_counter()
for nid in execution_topological_order(prepared):
 if nid=='existing-model':continue
 node=next(n for n in prepared.nodes if n.id==nid);m,path=loaded[node.algorithm_name,node.algorithm_version]
 inputs={e.targetHandle:outputs[e.source][e.sourceHandle] for e in prepared.edges if e.target==nid}
 outs={name:str(output/nid/name) for name in m.outputs}
 for value in outs.values():Path(value).parent.mkdir(parents=True,exist_ok=True)
 rendered=render_manifest(m,RenderContext(inputs=inputs,outputs=outs,params=node.params,pack_dir=str(path),workspace_root=str(output)))
 # Resume only a completed to_array from the same deterministic plan. Its
 # completion log and root manifest must exist; subsequent nodes still run.
 if '--resume' in sys.argv and node.algorithm_name=='col4d/colmap4d-to-array':
  completed=Path(outs['frames'])/'.array-view.json'
  log_path=report/f'demo-{nid}.txt'
  if completed.is_file() and log_path.is_file():
   prior=json.loads(log_path.read_text().splitlines()[-1])
   partition=json.loads(completed.read_text())
   assert prior['namespace']==partition['namespace'] and prior['groups']==len(partition['members'])
   outputs[nid]=outs
   records.append(dict(node_id=nid,algorithm=node.algorithm_name,inputs=inputs,outputs=outs,seconds=prior['seconds'],resumed_completed_output=True))
   continue
 env={**os.environ,'PATH':'/cloud/cloud-ssd1/envs/kiri/bin:'+os.environ['PATH']}
 begin=time.perf_counter()
 with (report/f'demo-{nid}.txt').open('w') as log:
  if m.exec.python:
   command=[sys.executable,str(path/m.exec.python),*rendered.python_args]
   subprocess.run(command,env=env,check=True,stdout=log,stderr=subprocess.STDOUT)
  else:subprocess.run(['bash','-c',rendered.shell],env=env,check=True,stdout=log,stderr=subprocess.STDOUT)
 outputs[nid]=outs;records.append(dict(node_id=nid,algorithm=node.algorithm_name,version=node.algorithm_version,inputs=inputs,outputs=outs,seconds=time.perf_counter()-begin))
result=dict(entry='local compiled graph + real manifest subprocesses (not gateway)',source=str(source),seconds=time.perf_counter()-started,executions=records)
(report/'compiled-demo.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
