"""Position codec and fan-out regressions; only temporary files and mock stores."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hololab.array_positions import (
    POSITION_LIMIT,
    decode_position,
    encode_position,
    enumerate_array_entries,
)
from hololab.gateway import execution
from hololab.gateway.execution import (
    WorkflowRunError,
    _discover_arrayed_paths,
    _execute_fanout_body,
    _prepare_fanout,
    _prepare_shard_rows,
    _shard_input_handles,
)
from hololab.gateway.handles import Handle
from hololab.gateway.jobs import JobState, JobStateMachine
from hololab.gateway.workflows import GraphNode
from hololab.manifest.render import RenderContext, rendered_output_paths


@pytest.mark.parametrize("position", [0, 1, 9, 10, 9999, 10000, POSITION_LIMIT - 1])
def test_position_round_trip(position):
    name = encode_position(position)
    assert len(name) == 18
    assert decode_position(name) == position
    assert encode_position(decode_position(name)) == name


def test_lexical_order_is_position_order():
    positions = [0, 1, 9, 10, 9999, 10000, POSITION_LIMIT - 1]
    assert sorted(map(encode_position, positions)) == list(map(encode_position, positions))


@pytest.mark.parametrize("position", [-1, POSITION_LIMIT, POSITION_LIMIT + 1])
def test_position_rejects_out_of_range(position):
    with pytest.raises(ValueError):
        encode_position(position)


@pytest.mark.parametrize("position", [True, False, 1.0, "1", None])
def test_position_rejects_non_integer(position):
    with pytest.raises(TypeError):
        encode_position(position)


@pytest.mark.parametrize(
    "name",
    [
        "p_0",
        "p_00000000000000000",
        "p_0000000000000000.png",
        "p_0000000000000000\n",
        "../p_0000000000000000",
        "p_" + "\uff10" * 16,
        "cam_A",
        "p_-000000000000001",
    ],
)
def test_decode_rejects_noncanonical_names(name):
    with pytest.raises(ValueError):
        decode_position(name)


def test_standalone_copy_has_same_codec(tmp_path):
    """An isolated pack can vendor exactly this stdlib-only file, no dependency install."""
    import hololab.array_positions as module

    copy = tmp_path / "array_positions.py"
    copy.write_bytes(Path(module.__file__).read_bytes())
    encoded = subprocess.run(
        [sys.executable, "-I", str(copy), "encode", "10000"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert encoded == encode_position(10000)
    decoded = subprocess.run(
        [sys.executable, "-I", str(copy), "decode", encoded],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert int(decoded) == 10000


def test_entries_use_complete_basename_bytes_and_ignore_link_target(tmp_path):
    target = tmp_path / ".target"
    target.write_text("payload")
    names = ["data2.mp4", "data10.mp4", "data1.mp4", "same.mov", "same.mp4", os.fsdecode(b"\xff")]
    for name in names:
        (tmp_path / name).symlink_to(target)
    assert [entry.name for entry in enumerate_array_entries(tmp_path)] == sorted(
        names, key=os.fsencode
    )


def test_invalid_entry_is_not_silently_dropped(tmp_path):
    (tmp_path / "broken").symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="broken link"):
        enumerate_array_entries(tmp_path)


class MemoryHandles:
    def __init__(self):
        self.values = {}

    async def get(self, handle_id):
        return self.values.get(handle_id)

    async def register(self, handle):
        self.values[handle.handle_id] = handle

    async def register_many(self, handles):
        for handle in handles:
            await self.register(handle)


class MemoryJobs:
    def __init__(self):
        self.values = {}
        self.set_code_identity = AsyncMock()

    async def create(self, job):
        self.values[job.job_id] = job

    async def create_many(self, jobs):
        for job in jobs:
            await self.create(job)

    async def get(self, job_id):
        return self.values.get(job_id)

    async def update(self, job, *_args):
        self.values[job.job_id] = job

    async def wait_terminal(self, job_id, *, timeout_s):
        job = self.values[job_id]
        assert job.state is JobState.DONE
        return job


async def prepared(tmp_path, *, batch_size=1, right_count=3):
    book = MemoryHandles()
    source_names = {
        "left": ["data2.mp4", "data1.mp4", "data10.mp4"],
        "right": [f"other_{i}.png" for i in reversed(range(right_count))],
    }
    for port, names in source_names.items():
        root = tmp_path / port
        root.mkdir(parents=True)
        for name in names:
            (root / name).write_text(name)
        await book.register(
            Handle(handle_id=port, node_id="node", storage="dir", tags=["any"], path=str(root))
        )
    # Deliberately non-enumerable broadcast value: planning must never list it.
    await book.register(
        Handle(
            handle_id="broadcast",
            node_id="node",
            storage="file",
            tags=["any"],
            path=str(tmp_path / "not-an-array"),
        )
    )
    store = MemoryJobs()
    session = SimpleNamespace(node_id="node", workspace_root=str(tmp_path / "workspace"))
    registry = SimpleNamespace(get_session=lambda _id: session, catalog_json=lambda: [])
    app = SimpleNamespace(
        state=SimpleNamespace(
            registry=registry,
            jobs_store=store,
            handles=book,
            hub=MagicMock(),
            snapshot_jobs=SimpleNamespace(attribute=AsyncMock()),
            workflows=object(),
        )
    )
    node = GraphNode(
        id="n",
        algorithm_name="test",
        algorithm_version="1",
        assigned_node_id="node",
        arrayed_toggle=True,
        batch_size=batch_size,
        parallelism=3,
    )
    plan = await _prepare_fanout(
        app,
        snapshot_id="snap",
        workflow_id="wf",
        gnode=node,
        pack_entry={
            "inputs": {"left": {}, "right": {}, "config": {"scalar": True}},
            "outputs": {"out": {"tags": ["any"]}},
        },
        input_handles={"left": "left", "right": "right", "config": "broadcast"},
    )
    return app, node, plan


async def rows(app, node, plan):
    return await _prepare_shard_rows(
        store=app.state.jobs_store,
        handles=app.state.handles,
        plan=plan,
        snapshot_id="snap",
        workflow_id="wf",
        gnode=node,
    )


@pytest.mark.asyncio
async def test_different_names_zip_by_position_in_batch_and_single_paths(tmp_path):
    app, node, plan = await prepared(tmp_path)
    shards = await rows(app, node, plan)
    for i, (_batch_index, encoded, shard, paths) in enumerate(shards):
        expected = {port: plan.input_paths_by_port[port][i] for port in ("left", "right")}
        assert encoded == [encode_position(i)]
        assert paths == [expected]
        assert shard.input_handles["config"] == "broadcast"
        single = await _shard_input_handles(
            handles=app.state.handles,
            input_handles=plan.input_handles,
            arrayed_input_ports=plan.arrayed_input_ports,
            element_id=encode_position(i),
            producer_node_id="node",
            input_paths_by_port=plan.input_paths_by_port,
        )
        assert single["config"] == "broadcast"
        for port in ("left", "right"):
            assert (await app.state.handles.get(single[port])).path == expected[port]
    assert [Path(path).name for path in plan.input_paths_by_port["left"]] == [
        "data1.mp4",
        "data10.mp4",
        "data2.mp4",
    ]


@pytest.mark.asyncio
async def test_outer_length_mismatch_fails_before_creating_parent(tmp_path):
    with pytest.raises(WorkflowRunError, match=r"outer length.*'left' has 3.*'right' has 2"):
        await prepared(tmp_path, right_count=2)


@pytest.mark.asyncio
async def test_fanout_removes_only_one_outer_layer(tmp_path):
    book = MemoryHandles()
    root = tmp_path / "nested"
    for outer, children in [("frame_z", ["a", "b"]), ("frame_a", ["x"])]:
        for child in children:
            (root / outer / child).mkdir(parents=True)
    await book.register(
        Handle(handle_id="nested", node_id="n", storage="dir", tags=["any"], path=str(root))
    )
    paths = await _discover_arrayed_paths(
        handles=book, input_handles={"frames": "nested"}, arrayed_input_ports=["frames"]
    )
    assert paths == {"frames": [str(root / "frame_a"), str(root / "frame_z")]}
    assert [len(list(Path(path).iterdir())) for path in paths["frames"]] == [1, 2]
    with pytest.raises(WorkflowRunError, match="exactly one"):
        await _discover_arrayed_paths(
            handles=book,
            input_handles={"frames": "nested"},
            arrayed_input_ports=["frames"],
            port_depths={"frames": 2},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("batch_size", [1, 2, 3, 10])
async def test_batches_and_reverse_completion_preserve_output_positions(
    tmp_path, monkeypatch, batch_size
):
    app, node, plan = await prepared(tmp_path, batch_size=batch_size)
    completions = []
    # Release whole batches in reverse order without relying on clock timing.
    leads = list(range(0, 3, batch_size))
    gates = {lead: asyncio.Event() for lead in leads}
    gates[leads[-1]].set()

    async def dispatch(**kwargs):
        ids = kwargs["batch_element_ids"]
        lead = decode_position(ids[0])
        await gates[lead].wait()
        for name, paths in zip(ids, kwargs["batch_input_paths"], strict=True):
            output = Path(plan.parent_ws) / "out" / name
            output.mkdir(parents=True)
            (output / "value.txt").write_text(Path(paths["left"]).read_text())
            completions.append(decode_position(name))
        running = JobStateMachine.transition(kwargs["shard"], JobState.ASSIGNED, node_id="node")
        running = JobStateMachine.transition(running, JobState.RUNNING)
        await app.state.jobs_store.update(JobStateMachine.transition(running, JobState.DONE))
        previous = leads.index(lead) - 1
        if previous >= 0:
            gates[leads[previous]].set()

    monkeypatch.setattr(execution, "_dispatch_prepared_shard", dispatch)
    monkeypatch.setattr(execution, "resolve_handle_output_tags", AsyncMock(return_value=["any"]))
    outputs = await _execute_fanout_body(
        app, snapshot_id="snap", workflow_id="wf", gnode=node, plan=plan, job_timeout_s=2
    )
    aggregate = await app.state.handles.get(outputs["out"])
    entries = enumerate_array_entries(aggregate.path)
    assert [p.name for p in entries] == [encode_position(i) for i in range(3)]
    assert [(p / "value.txt").read_text() for p in entries] == [
        "data1.mp4",
        "data10.mp4",
        "data2.mp4",
    ]
    if batch_size == 1:
        assert completions == [2, 1, 0]


def test_retry_same_position_reuses_output_slot(tmp_path):
    manifest = SimpleNamespace(outputs={"out": object()})
    paths = []
    for attempt in range(2):
        output = rendered_output_paths(
            manifest,
            tmp_path,
            "wf",
            f"attempt-{attempt}",
            shard_output_prefix=str(tmp_path / "parent"),
            shard_element_id=encode_position(7),
        )["out"]
        Path(output).mkdir(parents=True, exist_ok=True)
        (Path(output) / "value.txt").write_text(str(attempt))
        paths.append(output)
    assert paths[0] == paths[1]
    assert [entry.name for entry in enumerate_array_entries(tmp_path / "parent" / "out")] == [
        encode_position(7)
    ]
    assert (Path(paths[0]) / "value.txt").read_text() == "1"


@pytest.mark.parametrize("position", [0, 2, 17])
def test_shard_binding_index_is_global_position(position):
    assert RenderContext(shard_element_id=encode_position(position)).to_bindings()["shard"] == {
        "element_id": encode_position(position),
        "index": position,
    }


def test_output_path_rejects_legacy_identity_or_traversal(tmp_path):
    for name in ("cam_A", "../p_0000000000000000"):
        with pytest.raises(ValueError):
            rendered_output_paths(
                SimpleNamespace(outputs={"out": object()}),
                tmp_path,
                "wf",
                "job",
                shard_output_prefix=str(tmp_path),
                shard_element_id=name,
            )
