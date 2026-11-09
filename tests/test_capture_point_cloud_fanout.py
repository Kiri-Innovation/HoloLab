"""Isolated HoloLab scheduler; replace only worker transport, never production state.

CAPTURE_REAL_PATHS selects real camera/point inputs. Real fan-out reuses one
colmap4d-decoded cloud in the local worker; scalar mode runs the real CLI cold.
"""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock

import colmap4d
from colmap4d import colmap_io as cio
from fastapi.testclient import TestClient
import pytest

from hololab.gateway import execution
from hololab.gateway.app import create_app
from hololab.gateway.handles import Handle
from hololab.gateway.jobs import JobState, JobStateMachine, event_from_transition
from hololab.gateway.registry import NodeSession
from hololab.gateway.workflows import GraphNode, WorkflowGraph, packs_by_key_from_catalog, resolved_port_types
from hololab.manifest import load_manifest
from hololab.manifest.render import RenderContext, render_manifest, rendered_output_paths
from hololab.protocol.messages import GpuInfo, PackInventoryEntry

PACK = Path(__file__).parents[1] / 'community_packs/capture-point-cloud@0.1.0'
spec = importlib.util.spec_from_file_location('capture_pack_for_test', PACK/'capture.py')
capture_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = capture_module
spec.loader.exec_module(capture_module)


@pytest.mark.parametrize('fanout', [True, False])
def test_scheduler_shapes_broadcast_and_execution(tmp_path, monkeypatch, fanout):
    real = os.environ.get('CAPTURE_REAL_PATHS')
    if real:
        paths = json.loads(Path(real).read_text())
        camera_root, points = Path(paths['timestamp_cam']), Path(paths['4d_points'])
    else:
        camera_root, points = tmp_path/'cams', tmp_path/'cloud'
        camera_root.mkdir(); points.mkdir()
        for i in range(3):
            (camera_root/f'p_{i:016d}').write_text(json.dumps(dict(model='PINHOLE',width=64,height=48,
                params=[50,51,32,24],qvec=[1,0,0,0],tvec=[0,0,0],timestamp_ns=i*100_000_000)))
        cio.write_points3D_bin(points/'points3D.bin', {i+1:cio.Point3D(i+1,[i+.25,0,1],[20,40,60],0,[]) for i in range(3)})
        colmap4d.write_points_t_bin(points/'points_t.bin',{i+1:i*100_000_000 for i in range(3)})
        colmap4d.write_time_meta(points/'time_meta.json',dict(time_unit='ns',clock_domain='test'))
    members = sorted(p for p in camera_root.iterdir() if not p.name.startswith('.'))
    assert len(members) == (81 if real else 3)
    camera_path = camera_root if fanout else members[0]
    manifest, _ = load_manifest(PACK/'manifest.yaml')
    entry = manifest.model_dump(mode='json',by_alias=True)
    entry.update(node_ids=['local'],manifest_hash='test')
    source = dict(name='source',version='1.0.0',inputs={},outputs={
        'timestamp_cam':dict(tags=['colmap4d-single-cam'],arrayed=fanout,dim_labels=['camera'] if fanout else []),
        '4d_points':dict(tags=['colmap4d-point-cloud'])})
    target = GraphNode(id='capture',algorithm_name=manifest.name,algorithm_version=manifest.version,
        assigned_node_id='local',arrayed_toggle=fanout,parallelism=1,batch_size=1,params={'time_range_s':.03})
    graph = WorkflowGraph(nodes=[GraphNode(id='source',algorithm_name='source',algorithm_version='1.0.0'),target],
        edges=[dict(id=p,source='source',sourceHandle=p,target='capture',targetHandle=p) for p in ('timestamp_cam','4d_points')])
    packs = packs_by_key_from_catalog([source,entry])
    types, output_types = resolved_port_types(target,packs[(manifest.name,manifest.version)],graph,
        {n.id:n for n in graph.nodes},packs)
    assert output_types['points']['arrayed'] == fanout
    assert output_types['points']['tags'] == ['point-cloud']
    assert types.inputs['4d_points'].views.invocation.kind == 'object'
    assert types.outputs['points'].views.invocation.kind == 'object'
    app = create_app(db_path=tmp_path/'isolated.sqlite')
    evidence = []
    # Full real fan-out uses one read-only decoded cloud, not 81 copies in RAM.
    decoded = capture_module.load_cloud(points) if real and fanout else None

    async def execute_worker(job, store, inputs, prefix=None, element=None):
        assert inputs['4d_points'] == str(points)
        assert Path(inputs['timestamp_cam']).is_file()
        for state in (JobState.ASSIGNED,JobState.RUNNING):
            if job.state == state: continue
            if state == JobState.ASSIGNED and job.state != JobState.PENDING: continue
            changed=JobStateMachine.transition(job,state)
            kind,payload=event_from_transition(job,changed)
            await store.update(changed,kind,payload);job=changed
        outputs=rendered_output_paths(manifest,tmp_path,'wf',job.job_id,
            shard_output_prefix=prefix,shard_element_id=element)
        if decoded is not None:
            info = await asyncio.to_thread(capture_module.capture_loaded,decoded,
                inputs['timestamp_cam'],outputs['points'],.03)
            t=json.loads(Path(inputs['timestamp_cam']).read_text())['timestamp_ns']
            selected=decoded.times[(decoded.times>=t-30_000_000)&(decoded.times<=t+30_000_000)]
            assert len(selected)==info['count']==len(decoded.ids)//81
            assert selected.min()>=t-30_000_000 and selected.max()<=t+30_000_000
        else:
            rendered=render_manifest(manifest,RenderContext(inputs=inputs,outputs=outputs,
                params=job.params,pack_dir=str(PACK),manifest_dir=str(PACK)))
            env=dict(os.environ)
            executable=os.environ.get('CAPTURE_PYTHON',sys.executable)
            env['PATH']=str(Path(executable).parent)+':'+env['PATH']
            proc=await asyncio.create_subprocess_exec('bash','-c',rendered.shell,env=env,
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            stdout,stderr=await proc.communicate()
            assert proc.returncode==0,(stdout.decode(),stderr.decode())
            result=cio.read_points3D_txt(Path(outputs['points'])/'points3D.txt')
            assert len(result)==(518400 if real else 1)
            assert all(not p.track for p in result.values())
            info=dict(count=len(result))
        evidence.append(dict(camera=inputs['timestamp_cam'],output=outputs['points'],
            point_input=inputs['4d_points'],shard=element,**info))
        done=JobStateMachine.transition(job,JobState.DONE)
        kind,payload=event_from_transition(job,done);await store.update(done,kind,payload)
        return outputs

    async def dispatch_shard(**kw):
        inputs=dict(kw['shard_input_paths'])
        for port,hid in kw['shard'].input_handles.items():
            if port not in inputs: inputs[port]=(await app.state.handles.get(hid)).path
        await execute_worker(kw['shard'],kw['store'],inputs,kw['shard_output_prefix'],kw['shard_element_id'])

    original_dispatch=execution._dispatch_job
    async def dispatch_scalar(**kw):
        jid=await original_dispatch(**kw)
        job=await kw['store'].get(jid)
        inputs={port:(await app.state.handles.get(hid)).path for port,hid in job.input_handles.items()}
        outputs=await execute_worker(job,kw['store'],inputs)
        await app.state.handles.register(Handle(handle_id='scalar-result',node_id='local',storage='dir',
            tags=['point-cloud'],path=outputs['points'],job_id=jid,output_port_name='points'))
        return jid
    monkeypatch.setattr(execution,'_dispatch_prepared_shard',dispatch_shard)
    monkeypatch.setattr(execution,'_dispatch_job',dispatch_scalar)
    with TestClient(app) as client:
        app.state.registry._sessions['local']=NodeSession(node_id='local',session_id='test',node_name='local',
            ws=MagicMock(send_text=AsyncMock()),advertised_url=None,gpu=GpuInfo(),
            packs=[PackInventoryEntry(name=manifest.name,version=manifest.version,manifest_hash='test')],
            protocol_v=1,workspace_root=str(tmp_path))
        app.state.registry.catalog_json=lambda:[source,entry]
        async def run():
            for port,path,tag,storage in [('timestamp_cam',camera_path,'colmap4d-single-cam','dir' if fanout else 'file'),
                                          ('4d_points',points,'colmap4d-point-cloud','dir')]:
                await app.state.handles.register(Handle(handle_id=port,node_id='local',storage=storage,tags=[tag],path=str(path)))
            await app.state.workflows.save_draft(workflow_id='wf',name='capture acceptance',graph=graph)
            snap=await app.state.workflows.create_snapshot(workflow_id='wf',graph=graph)
            ids=await execution.run_snapshot(app,workflow_id='wf',snapshot_id=snap.snapshot_id,graph=graph,
                skip_graph_nodes={'source'},seed_outputs={'source':{p:p for p in ('timestamp_cam','4d_points')}})
            assert len(ids)==1
            shards=await app.state.jobs_store.list_shards_of(ids[0])
            assert len(shards)==(len(members) if fanout else 0)
            assert all(s.state==JobState.DONE for s in shards)
            assert len(evidence)==(len(members) if fanout else 1)
            handles=await app.state.handles.list_by_job(ids[0]);assert len(handles)==1
            result=handles[0]
            if fanout:
                assert len([p for p in Path(result.path).iterdir() if not p.name.startswith('.')])==len(members)
            else:
                assert (Path(result.path)/'points3D.txt').is_file()
        client.portal.call(run)
    (tmp_path/'evidence.json').write_text(json.dumps(evidence,indent=2))
    print('capture evidence:',tmp_path/'evidence.json')
