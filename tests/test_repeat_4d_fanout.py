"""Real repeat CLI shards under the local scheduler; no running service required."""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import colmap4d
from colmap4d.arrays import read_image_headers
from fastapi.testclient import TestClient

from hololab.array_positions import encode_position
from hololab.gateway import execution
from hololab.gateway.app import create_app
from hololab.gateway.handles import Handle
from hololab.gateway.jobs import JobState, JobStateMachine, event_from_transition
from hololab.gateway.registry import NodeSession
from hololab.gateway.workflows import (
    GraphNode, WorkflowGraph, packs_by_key_from_catalog, resolved_port_types,
)
from hololab.manifest import load_manifest
from hololab.manifest.render import rendered_output_paths
from hololab.protocol.messages import GpuInfo, PackInventoryEntry

PACK = Path(__file__).parents[1] / 'community_packs/repeat-4d@0.2.0'


@pytest.mark.parametrize('version,override', [('0.2.0', 0), ('0.3.0', None), ('0.3.0', 0)])
def test_parallel_camera_cli_and_aggregate(tmp_path, monkeypatch, version, override):
    PACK = Path(__file__).parents[1] / f'community_packs/repeat-4d@{version}'
    tag = 'colmap-camera' if version == '0.2.0' else 'colmap4d-single-cam'
    manifest, _ = load_manifest(PACK / 'manifest.yaml')
    entry = manifest.model_dump(mode='json', by_alias=True)
    entry.update(node_ids=['local'], manifest_hash='test')
    source = dict(name='source', version='1.0.0', inputs={}, outputs={
        'out': dict(tags=[tag], arrayed=True, dim_labels=['camera'])})
    graph = WorkflowGraph(nodes=[
        GraphNode(id='source', algorithm_name='source', algorithm_version='1.0.0'),
        GraphNode(id='repeat', algorithm_name=manifest.name, algorithm_version=manifest.version,
                  assigned_node_id='local', arrayed_toggle=True, parallelism=2, batch_size=1,
                  params=dict(duration=1, fps=3, start_time_ns=override)),
    ], edges=[dict(id='edge', source='source', sourceHandle='out',
                   target='repeat', targetHandle='camera')])
    packs = packs_by_key_from_catalog([source, entry])
    node = graph.nodes[1]
    types, outputs = resolved_port_types(
        node, packs[(manifest.name, manifest.version)], graph,
        {n.id: n for n in graph.nodes}, packs)
    assert outputs['4d_cams'] == dict(tags=['colmap4d-cams'], arrayed=True, dim_labels=['camera'])
    assert types.inputs['camera'].views.invocation.kind == 'object'
    assert types.outputs['4d_cams'].views.invocation.kind == 'object'

    arr = tmp_path / 'cameras'
    arr.mkdir()
    for i in range(3):
        (arr / f'{i}.json').write_text(json.dumps(dict(
            model='PINHOLE', width=640, height=480, params=[500, 510, 320, 240],
            qvec=[1, 0, 0, 0], tvec=[i, 0, 0],
            **({'timestamp_ns': 123456789 + i} if version == '0.3.0' else {}))))
    app = create_app(db_path=tmp_path / 'test.sqlite')
    active = peak = 0
    gate = asyncio.Event()

    async def local_dispatch(**kw):
        # Replace only remote transport: run the real CLI and persist its result.
        nonlocal active, peak
        job, store = kw['shard'], kw['store']
        for state in (JobState.ASSIGNED, JobState.RUNNING):
            updated = JobStateMachine.transition(job, state)
            kind, payload = event_from_transition(job, updated)
            await store.update(updated, kind, payload)
            job = updated
        active += 1
        peak = max(peak, active)
        if active == 2:
            gate.set()
        await asyncio.wait_for(gate.wait(), timeout=10)
        output = rendered_output_paths(
            manifest, tmp_path, 'wf', job.job_id,
            shard_output_prefix=kw['shard_output_prefix'],
            shard_element_id=kw['shard_element_id'])['4d_cams']
        camera = kw['shard_input_paths']['camera']
        handle = await app.state.handles.get(job.input_handles['camera'])
        assert handle.path == camera and handle.storage == 'file'
        assert job.params == node.params
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(PACK / 'repeat_4d.py'), '--camera', camera,
            '--output', output, '--duration', str(job.params['duration']),
            '--fps', str(job.params['fps']),
            *(['--start-time-ns', str(override)] if override is not None else []),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        assert process.returncode == 0, (stdout, stderr)
        active -= 1
        done = JobStateMachine.transition(job, JobState.DONE)
        kind, payload = event_from_transition(job, done)
        await store.update(done, kind, payload)

    monkeypatch.setattr(execution, '_dispatch_prepared_shard', local_dispatch)
    with TestClient(app) as client:
        app.state.registry._sessions['local'] = NodeSession(
            node_id='local', session_id='test', node_name='local',
            ws=MagicMock(send_text=AsyncMock()), advertised_url=None, gpu=GpuInfo(),
            packs=[PackInventoryEntry(name=manifest.name, version=manifest.version,
                                      manifest_hash='test')],
            protocol_v=1, workspace_root=str(tmp_path))
        app.state.registry.catalog_json = lambda: [source, entry]

        async def run():
            await app.state.handles.register(Handle(
                handle_id='cameras', node_id='local', storage='dir',
                tags=[tag], path=str(arr)))
            await app.state.workflows.save_draft(workflow_id='wf', name='test', graph=graph)
            snap = await app.state.workflows.create_snapshot(workflow_id='wf', graph=graph)
            ids = await execution.run_snapshot(
                app, workflow_id='wf', snapshot_id=snap.snapshot_id, graph=graph,
                skip_graph_nodes={'source'}, seed_outputs={'source': {'out': 'cameras'}})
            shards = await app.state.jobs_store.list_shards_of(ids[0])
            assert len(shards) == 3 and all(s.state == JobState.DONE for s in shards)
            handles = await app.state.handles.list_by_job(ids[0])
            assert len(handles) == 1
            return handles[0]

        aggregate = client.portal.call(run)
    assert peak == 2
    assert aggregate.storage == 'dir' and aggregate.tags == ['colmap4d-cams']
    root = Path(aggregate.path)
    assert sorted(p.name for p in root.iterdir()) == [encode_position(i) for i in range(3)]
    for i in range(3):
        output = root / encode_position(i)
        images = read_image_headers(output / 'images.bin')
        assert len(images) == 3
        assert all(image.tvec == [i, 0, 0] for image in images.values())
        origin = 123456789 + i if override is None else override
        assert list(colmap4d.read_times_bin(output / 'times.bin').values()) == [
            origin, origin + 333333333, origin + 666666667]
        meta = json.loads((output / 'time_meta.json').read_text())
        assert meta['conversion']['producer'] == f'col4d/repeat-4d@{version}'
