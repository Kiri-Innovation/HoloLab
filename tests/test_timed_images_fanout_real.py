"""Real pixels through the actual scheduler; local subprocess replaces node transport."""
import shutil
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import colmap4d
from fastapi.testclient import TestClient

from hololab.gateway import execution
from hololab.gateway.app import create_app
from hololab.gateway.handles import Handle
from hololab.gateway.jobs import JobState, JobStateMachine, event_from_transition
from hololab.gateway.registry import NodeSession
from hololab.gateway.workflows import GraphNode, WorkflowGraph
from hololab.manifest import load_manifest
from hololab.manifest.render import RenderContext, render_manifest, rendered_output_paths
from hololab.protocol.messages import GpuInfo, PackInventoryEntry

ROOT = Path(__file__).parents[1]


def test_real_images_fanout(tmp_path, monkeypatch):
    real = Path(os.environ.get('TIMED_IMAGES_REAL', '/tmp/render-images-v03'))
    if not real.is_dir():
        pytest.skip('Set TIMED_IMAGES_REAL to a real timed image component')
    arr = tmp_path/'input'
    arr.mkdir()
    for i in range(3):
        shutil.copytree(real, arr/f'p_{i:016d}')
    paths = {'timed_images': str(arr)}
    pack_dirs = {'convert': ROOT/'community_packs/colmap4d-images-to-images@0.2.0'}
    manifests = {k: load_manifest(p/'manifest.yaml')[0] for k,p in pack_dirs.items()}
    source = dict(name='source', version='1.0.0', inputs={}, outputs={
        'timed_images':dict(tags=['colmap4d-images'], arrayed=True, dim_labels=['camera'])})
    m = manifests['convert']
    entry = m.model_dump(mode='json', by_alias=True)
    entry.update(node_ids=['local'], manifest_hash='test')
    entries = [source, entry]
    graph = WorkflowGraph(nodes=[
        GraphNode(id='source', algorithm_name='source', algorithm_version='1.0.0'),
        GraphNode(id='convert', algorithm_name=m.name, algorithm_version=m.version,
                  assigned_node_id='local', arrayed_toggle=True, parallelism=2, batch_size=1)],
        edges=[dict(id='edge', source='source', sourceHandle='timed_images',
                    target='convert', targetHandle='timed_images')])
    app = create_app(db_path=tmp_path / 'test.sqlite')
    evidence = []

    async def dispatch(**kw):
        job, store = kw['shard'], kw['store']
        key = job.graph_node_id
        m = manifests[key]
        for state in (JobState.ASSIGNED, JobState.RUNNING):
            updated = JobStateMachine.transition(job, state)
            kind, payload = event_from_transition(job, updated)
            await store.update(updated, kind, payload)
            job = updated
        inputs = dict(kw['shard_input_paths'])
        for port, hid in job.input_handles.items():
            if port not in inputs:
                inputs[port] = (await app.state.handles.get(hid)).path
        outputs = rendered_output_paths(m, tmp_path, 'wf', job.job_id,
            shard_output_prefix=kw['shard_output_prefix'], shard_element_id=kw['shard_element_id'])
        for path in outputs.values():
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        rendered = render_manifest(m, RenderContext(inputs=inputs, outputs=outputs, params=job.params,
            pack_dir=str(pack_dirs[key]), manifest_dir=str(pack_dirs[key])))
        env = dict(os.environ)
        executable = '/cloud/cloud-ssd1/envs/kiri/bin/python'
        python = str(Path(executable).parent)
        env['PATH'] = python + ':' + env['PATH']
        proc = await asyncio.create_subprocess_exec('bash', '-c', rendered.shell, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        assert proc.returncode == 0, (stdout.decode(), stderr.decode())
        evidence.append(dict(node=key, output=outputs, shard=kw['shard_element_id']))
        done = JobStateMachine.transition(job, JobState.DONE)
        kind, payload = event_from_transition(job, done)
        await store.update(done, kind, payload)

    monkeypatch.setattr(execution, '_dispatch_prepared_shard', dispatch)
    with TestClient(app) as client:
        app.state.registry._sessions['local'] = NodeSession(node_id='local', session_id='test', node_name='local',
            ws=MagicMock(send_text=AsyncMock()), advertised_url=None, gpu=GpuInfo(),
            packs=[PackInventoryEntry(name=m.name, version=m.version, manifest_hash='test') for m in manifests.values()],
            protocol_v=1, workspace_root=str(tmp_path))
        app.state.registry.catalog_json = lambda: entries

        async def run():
            for k, path in paths.items():
                await app.state.handles.register(Handle(handle_id=k, node_id='local', storage='dir',
                    tags=source['outputs'][k]['tags'], path=path))
            await app.state.workflows.save_draft(workflow_id='wf', name='isolated verification', graph=graph)
            snap = await app.state.workflows.create_snapshot(workflow_id='wf', graph=graph)
            ids = await execution.run_snapshot(app, workflow_id='wf', snapshot_id=snap.snapshot_id, graph=graph,
                skip_graph_nodes={'source'}, seed_outputs={'source': {k:k for k in paths}})
            for jid in ids:
                shards = await app.state.jobs_store.list_shards_of(jid)
                assert len(shards) == 3
                assert all(s.state == JobState.DONE for s in shards)
        client.portal.call(run)
    assert len(evidence)==3
    for item in evidence:
        out=Path(item['output']['images'])
        assert len(list(out.glob('p_*.png')))==2
    (tmp_path/'evidence.json').write_text(json.dumps(evidence,indent=2))
    print('evidence:',tmp_path/'evidence.json')
