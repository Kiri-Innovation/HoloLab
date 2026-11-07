"""Legacy canvas batch_size is accepted but fan-out always uses one element per job."""

from __future__ import annotations

from pathlib import Path

import pytest

from hololab.array_positions import encode_position
from hololab.gateway.execution import _FanoutPlan, _prepare_shard_rows
from hololab.gateway.handles import Handle, HandleBook
from hololab.gateway.jobs import Job
from hololab.gateway.registry import JobsStore
from hololab.gateway.workflows import GraphNode
from hololab.persistence.db import open_database


@pytest.fixture(autouse=True)
async def close_test_databases(monkeypatch):
    """These store-only tests must drain writers before their event loop ends."""
    databases = []
    original = open_database

    async def tracked_open(path):
        db = await original(path)
        databases.append(db)
        return db

    monkeypatch.setitem(globals(), "open_database", tracked_open)
    try:
        yield
    finally:
        for db in databases:
            await db.close()


async def _open_stores(tmp_path: Path) -> tuple[JobsStore, HandleBook]:
    db = await open_database(tmp_path / "db.sqlite")
    return JobsStore(db), HandleBook(db)


async def _seeded_book_with_parent(
    tmp_path: Path, elements: list[str]
) -> tuple[JobsStore, HandleBook, Handle]:
    store, book = await _open_stores(tmp_path)
    root = tmp_path / "arr"
    root.mkdir()
    for name in elements:
        (root / name).mkdir()
    parent = Handle(
        handle_id="h-arrayed",
        node_id="node-a",
        storage="dir",
        tags=["colmap-cams"],
        path=str(root),
        job_id=None,
        output_port_name=None,
    )
    await book.register(parent)
    await store.create(_plan(parent, elements).parent_job)
    return store, book, parent


def _plan(parent: Handle, element_ids: list[str]) -> _FanoutPlan:
    return _FanoutPlan(
        parent_job=Job(
            job_id="parent-1",
            workflow_id="wf",
            snapshot_id="snap",
            algorithm_name="pack",
            algorithm_version="0.1.0",
            params={},
            input_handles={"frames": parent.handle_id, "cfg": "h-scalar"},
            graph_node_id="n1",
            expected_shards=len(element_ids),
        ),
        parent_ws="/tmp/parent-ws",
        element_ids=[encode_position(i) for i in range(len(element_ids))],
        arrayed_input_ports=["frames"],
        pack_outputs={},
        input_handles={"frames": parent.handle_id, "cfg": "h-scalar"},
        session_node_id="node-a",
        input_paths_by_port={"frames": [str(Path(parent.path) / name) for name in element_ids]},
    )


def _graph_node(batch_size: int = 1) -> GraphNode:
    return GraphNode(
        id="n1",
        algorithm_name="pack",
        algorithm_version="0.1.0",
        arrayed_toggle=True,
        parallelism=1,
        batch_size=batch_size,
        assigned_node_id="node-a",
    )


@pytest.mark.parametrize("legacy_size", [1, 2, 4, 100])
@pytest.mark.asyncio
async def test_legacy_batch_size_never_coalesces_80_elements(tmp_path, legacy_size):
    elements = [f"camera-{i}" for i in range(80)]
    store, book, parent = await _seeded_book_with_parent(tmp_path, elements)
    node = _graph_node(batch_size=legacy_size)
    shards = await _prepare_shard_rows(
        store=store,
        handles=book,
        plan=_plan(parent, elements),
        snapshot_id="snap",
        workflow_id="wf",
        gnode=node,
    )
    assert node.batch_size == 1
    assert "batch_size" not in node.model_dump()
    assert len(shards) == 80
    assert len({shard.job_id for _, _, shard, _ in shards}) == 80
    for i, (_, ids, shard, paths) in enumerate(shards):
        assert ids == [encode_position(i)]
        assert len(paths) == 1
        assert paths[0]["frames"].endswith(f"/camera-{i}")
        persisted = await store.get(shard.job_id)
        assert persisted.parent_job_id == "parent-1"
        assert persisted.shard_element_id == encode_position(i)
        assert persisted.shard_element_ids is None


def test_legacy_workflow_load_warns_and_preserves_algorithm_batch_size(monkeypatch):
    import json
    from unittest.mock import Mock

    from hololab.gateway import workflows
    from hololab.gateway.models import GraphNodeOut

    warning = Mock()
    monkeypatch.setattr(workflows.log, "warning", warning)
    graph = workflows.graph_from_json(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": "legacy",
                        "algorithm_name": "community/birefnet-mask",
                        "algorithm_version": "2.2.3",
                        "batch_size": 4,
                        "parallelism": 2,
                        "params": {"batch_size": 4},
                    }
                ],
                "edges": [],
            }
        )
    )
    assert graph.nodes[0].batch_size == 1
    warning.assert_called_once()
    assert warning.call_args.kwargs["batch_size"] == 4
    saved = json.loads(workflows.graph_to_json(graph))
    assert "batch_size" not in saved["nodes"][0]
    assert saved["nodes"][0]["params"]["batch_size"] == 4
    assert saved["nodes"][0]["parallelism"] == 2
    response = GraphNodeOut.model_validate(graph.nodes[0].model_dump()).model_dump()
    assert "batch_size" not in response
