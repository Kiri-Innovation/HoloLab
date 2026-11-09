"""Real pixels through the actual scheduler; local subprocess replaces node transport."""

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from colmap4d import sidecar
from fastapi.testclient import TestClient
from PIL import Image

from hololab.gateway import execution
from hololab.gateway.app import create_app
from hololab.gateway.handles import Handle
from hololab.gateway.jobs import JobState, JobStateMachine, event_from_transition
from hololab.gateway.registry import NodeSession
from hololab.gateway.workflows import (
    GraphNode,
    WorkflowGraph,
    packs_by_key_from_catalog,
    resolved_port_types,
    validate_snapshot,
)
from hololab.manifest import load_manifest
from hololab.manifest.render import RenderContext, render_manifest, rendered_output_paths
from hololab.protocol.messages import GpuInfo, PackInventoryEntry

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("kind", ["generate", "timed-component", "timestamp-only"])
def test_broadcast_and_timestamp_fanout(tmp_path, monkeypatch, kind):
    real = Path(os.environ.get("SOURCE_FRAMES_REAL", "/tmp/source-broadcast-040/frames"))
    timed = Path(os.environ.get("TIMED_COMPONENT_REAL", "/tmp/render-images-roundtrip"))
    if not real.is_dir() or not timed.is_dir():
        pytest.skip(
            "Provide SOURCE_FRAMES_REAL and TIMED_COMPONENT_REAL for real-pixel verification"
        )
    frames = sorted(real.glob("*.png"))
    assert len(frames) == 2
    arr = tmp_path / "cameras"
    arr.mkdir()
    clocks = tmp_path / "clocks"
    clocks.mkdir()
    expected_times = {}
    for i in range(3):
        leaf = arr / f"p_{i:016d}"
        shutil.copytree(
            Path(os.environ.get("RENDER_FRAMES_REAL", "/tmp/render-image-array"))
            if kind == "generate"
            else real,
            leaf,
        )
        clock = clocks / f"p_{i:016d}"
        clock.mkdir()
        times = sidecar.read_times_bin(timed / "times.bin", strict=True)
        assert len(times) == len(frames)
        times = {iid: t + i * 1000000000 for iid, t in times.items()}
        sidecar.write_times_bin(clock / "times.bin", times)
        shutil.copyfile(timed / "time_meta.json", clock / "time_meta.json")
        if kind == "timed-component":
            for filename in ("images.bin", "cameras.bin"):
                shutil.copyfile(timed / filename, clock / filename)
        expected_times[leaf.name] = times
    if kind == "generate":
        paths = {"source_images": str(real), "render_images": str(arr)}
        pack_dirs = {"convert": ROOT.parent / "packages/Vista4D/packs/vista4d-generate@0.4.0"}
        output_specs = {
            "source_images": dict(tags=["image"], arrayed=True, dim_labels=["frame"]),
            "render_images": dict(tags=["image"], arrayed=True, dim_labels=["camera", "frame"]),
        }
    else:
        paths = {"images": str(arr), "timestamps": str(clocks)}
        pack_dirs = {"convert": ROOT / "community_packs/images-add-timestamp@0.2.0"}
        output_specs = {
            "images": dict(tags=["image"], arrayed=True, dim_labels=["camera", "frame"]),
            "timestamps": dict(
                tags=["colmap4d-images" if kind == "timed-component" else "colmap4d-timestamps"],
                arrayed=True,
                dim_labels=["camera"],
            ),
        }
    manifests = {k: load_manifest(p / "manifest.yaml")[0] for k, p in pack_dirs.items()}
    source = dict(name="source", version="1.0.0", inputs={}, outputs=output_specs)
    m = manifests["convert"]
    entry = m.model_dump(mode="json", by_alias=True)
    entry.update(node_ids=["local"], manifest_hash="test")
    entries = [source, entry]
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="source",
                algorithm_name="source",
                algorithm_version="1.0.0",
                assigned_node_id="local",
            ),
            GraphNode(
                id="convert",
                algorithm_name=m.name,
                algorithm_version=m.version,
                assigned_node_id="local",
                arrayed_toggle=True,
                parallelism=2,
                batch_size=1,
                params={"num_frames": 1, "num_inference_steps": 1} if kind == "generate" else {},
            ),
        ],
        edges=[
            dict(id=k, source="source", sourceHandle=k, target="convert", targetHandle=k)
            for k in paths
        ],
    )
    indexed = packs_by_key_from_catalog(entries)
    issues = validate_snapshot(
        graph,
        packs_by_key=indexed,
        online_node_ids={"local"},
        packs_offered_by_node={"local": {(source["name"], source["version"]), (m.name, m.version)}},
    )
    assert not issues, issues
    types, _ = resolved_port_types(
        graph.nodes[1], indexed[(m.name, m.version)], graph, {n.id: n for n in graph.nodes}, indexed
    )
    if kind == "generate":
        port = types.inputs["source_images"]
        assert port.views.participation == "broadcast-input"
        assert port.views.aggregate == port.views.invocation
    else:
        assert types.inputs["timestamps"].views.participation == "map-input"
    app = create_app(db_path=tmp_path / "test.sqlite")
    evidence = []

    async def dispatch(**kw):
        job, store = kw["shard"], kw["store"]
        key = job.graph_node_id
        m = manifests[key]
        for state in (JobState.ASSIGNED, JobState.RUNNING):
            updated = JobStateMachine.transition(job, state)
            event_kind, payload = event_from_transition(job, updated)
            await store.update(updated, event_kind, payload)
            job = updated
        inputs = dict(kw["shard_input_paths"])
        for port, hid in job.input_handles.items():
            if port not in inputs:
                inputs[port] = (await app.state.handles.get(hid)).path
        if kind == "generate":
            assert inputs["source_images"] == str(real)
            assert job.input_handles["source_images"] == "source_images"
            assert len(list(Path(inputs["source_images"]).glob("*.png"))) == len(frames)
            assert len(list(Path(inputs["render_images"]).glob("*.png"))) == len(frames)
            assert Path(inputs["render_images"]).name == kw["shard_element_id"]
        else:
            assert (
                Path(inputs["images"]).name
                == Path(inputs["timestamps"]).name
                == kw["shard_element_id"]
            )
        outputs = rendered_output_paths(
            m,
            tmp_path,
            "wf",
            job.job_id,
            shard_output_prefix=kw["shard_output_prefix"],
            shard_element_id=kw["shard_element_id"],
        )
        for path in outputs.values():
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        rendered = render_manifest(
            m,
            RenderContext(
                inputs=inputs,
                outputs=outputs,
                params=job.params,
                pack_dir=str(pack_dirs[key]),
                manifest_dir=str(pack_dirs[key]),
            ),
        )
        env = dict(os.environ)
        executable = (
            "/cloud/cloud-ssd1/conda/envs/vista4d_h20/bin/python"
            if kind == "generate"
            else sys.executable
        )
        python = str(Path(executable).parent)
        env["PATH"] = python + ":" + env["PATH"]
        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            rendered.shell.replace('run.py"', 'run.py" --validate-only')
            if kind == "generate"
            else rendered.shell,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        assert proc.returncode == 0, (stdout.decode(), stderr.decode())
        if kind != "generate":
            output = Path(outputs["timed_images"])
            assert (
                sidecar.read_times_bin(output / "times.bin", strict=True)
                == expected_times[kw["shard_element_id"]]
            )
            for filename in ("times.bin", "time_meta.json", "images.bin", "cameras.bin"):
                original = Path(inputs["timestamps"]) / filename
                if original.exists():
                    assert (output / filename).read_bytes() == original.read_bytes()
            pixels = sorted((output / "images").rglob("*.png"))
            assert len(pixels) == len(frames)
            for actual, original in zip(pixels, frames, strict=True):
                with Image.open(actual) as a, Image.open(original) as b:
                    assert a.convert("RGB").tobytes() == b.convert("RGB").tobytes()
        evidence.append(
            dict(
                node=key,
                output=outputs,
                inputs=inputs,
                stdout=stdout.decode(),
                shard=kw["shard_element_id"],
            )
        )
        done = JobStateMachine.transition(job, JobState.DONE)
        event_kind, payload = event_from_transition(job, done)
        await store.update(done, event_kind, payload)

    monkeypatch.setattr(execution, "_dispatch_prepared_shard", dispatch)
    with TestClient(app) as client:
        app.state.registry._sessions["local"] = NodeSession(
            node_id="local",
            session_id="test",
            node_name="local",
            ws=MagicMock(send_text=AsyncMock()),
            advertised_url=None,
            gpu=GpuInfo(),
            packs=[
                PackInventoryEntry(name=m.name, version=m.version, manifest_hash="test")
                for m in manifests.values()
            ],
            protocol_v=1,
            workspace_root=str(tmp_path),
        )
        app.state.registry.catalog_json = lambda: entries

        async def run():
            for k, path in paths.items():
                await app.state.handles.register(
                    Handle(
                        handle_id=k,
                        node_id="local",
                        storage="dir",
                        tags=source["outputs"][k]["tags"],
                        path=path,
                    )
                )
            await app.state.workflows.save_draft(
                workflow_id="wf", name="isolated verification", graph=graph
            )
            snap = await app.state.workflows.create_snapshot(workflow_id="wf", graph=graph)
            ids = await execution.run_snapshot(
                app,
                workflow_id="wf",
                snapshot_id=snap.snapshot_id,
                graph=graph,
                skip_graph_nodes={"source"},
                seed_outputs={"source": {k: k for k in paths}},
            )
            for jid in ids:
                shards = await app.state.jobs_store.list_shards_of(jid)
                assert len(shards) == 3
                assert all(s.state == JobState.DONE for s in shards)

        client.portal.call(run)
    assert len(evidence) == 3
    (tmp_path / "evidence.json").write_text(json.dumps(evidence, indent=2))
    print("evidence:", tmp_path / "evidence.json")
