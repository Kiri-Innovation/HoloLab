"""Real CLI fan-out
optional A800 integration uses real inputs and CUDA renderer.

REPEAT_REAL_PATHS=/tmp/repeat-real-paths.json pytest -s ... runs nine render shards.
Only the node transport is replaced with local subprocess dispatch.
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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


def test_broadcast_and_render(tmp_path, monkeypatch):
    real = os.environ.get('REPEAT_REAL_PATHS')
    paths = json.loads(Path(real).read_text()) if real else {}
    if not real:
        arr, ref = tmp_path / 'cameras', tmp_path / 'reference'
        arr.mkdir()
        ref.mkdir()
        for i in range(3):
            (arr / f'{i}.json').write_text(json.dumps(dict(model='PINHOLE', width=64, height=48,
                params=[50, 51, 32, 24], qvec=[1, 0, 0, 0], tvec=[i, 0, 0])))
        colmap4d.write_times_bin(ref / 'times.bin', {1: 30, 2: 10, 3: 10})
        colmap4d.write_time_meta(ref / 'time_meta.json', dict(time_unit='ns', clock_domain='capture'))
        paths = dict(camera=str(arr), timestamp_cameras=str(ref))
    ref = Path(paths['timestamp_cameras'])
    if real:
        assert json.loads((ref / 'time_meta.json').read_text())['clock_domain'] == json.loads(
            (Path(paths['points']) / 'time_meta.json').read_text())['clock_domain']
    pack_dirs = {'repeat': ROOT / 'community_packs/repeat-into-4d@0.2.0'}
    if real:
        pack_dirs['render'] = ROOT.parent / 'packages/Vista4D/packs/colmap4d-render-video@0.2.0'
    manifests = {k: load_manifest(p / 'manifest.yaml')[0] for k, p in pack_dirs.items()}
    source = dict(name='source', version='1.0.0', inputs={}, outputs={
        'camera': dict(tags=['colmap-single-cam'], arrayed=True, dim_labels=['camera']),
        'timestamp_cameras': dict(tags=['colmap4d-cams']),
        'points': dict(tags=['colmap4d-point-cloud'])})
    entries = [source]
    nodes = [GraphNode(id='source', algorithm_name='source', algorithm_version='1.0.0')]
    for k, m in manifests.items():
        entry = m.model_dump(mode='json', by_alias=True)
        entry.update(node_ids=['local'], manifest_hash='test')
        entries.append(entry)
        params = {name: p.default for name, p in m.params.items()}
        if k == 'render':
            params['max_frames'] = 1
        nodes.append(GraphNode(id=k, algorithm_name=m.name, algorithm_version=m.version,
            assigned_node_id='local', arrayed_toggle=True, parallelism=1 if real else 2,
            batch_size=1, params=params))
    edges = [dict(id=k, source='source', sourceHandle=k, target='repeat', targetHandle=k)
             for k in ('camera', 'timestamp_cameras')]
    if real:
        edges += [dict(id='r', source='repeat', sourceHandle='4d_cams', target='render', targetHandle='4d_cams'),
                  dict(id='p', source='source', sourceHandle='points', target='render', targetHandle='4d_points')]
    graph = WorkflowGraph(nodes=nodes, edges=edges)
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
        if key == 'repeat':
            assert inputs['timestamp_cameras'] == paths['timestamp_cameras']
        else:
            assert inputs['4d_points'] == paths['points']
        outputs = rendered_output_paths(m, tmp_path, 'wf', job.job_id,
            shard_output_prefix=kw['shard_output_prefix'], shard_element_id=kw['shard_element_id'])
        for path in outputs.values():
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        rendered = render_manifest(m, RenderContext(inputs=inputs, outputs=outputs, params=job.params,
            pack_dir=str(pack_dirs[key]), manifest_dir=str(pack_dirs[key])))
        env = dict(os.environ)
        executable = os.environ.get('REPEAT_RENDER_PYTHON', sys.executable) if key == 'render' else sys.executable
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
                assert len(shards) == (9 if real else 3)
                assert all(s.state == JobState.DONE for s in shards)
        client.portal.call(run)
    times = sorted(set(colmap4d.read_times_bin(ref / 'times.bin').values()))
    for item in evidence:
        if item['node'] == 'repeat':
            out = Path(item['output']['4d_cams'])
            assert list(colmap4d.read_times_bin(out / 'times.bin').values()) == times
            assert (out / 'time_meta.json').read_bytes() == (ref / 'time_meta.json').read_bytes()
    (tmp_path / 'evidence.json').write_text(json.dumps(evidence, indent=2))
    print('evidence:', tmp_path / 'evidence.json')
