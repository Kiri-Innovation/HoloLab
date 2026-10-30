from copy import deepcopy

import pytest

from hololab.gateway.canvas_jobs import select_canvas_jobs
from hololab.gateway.workflows import GraphEdge, GraphNode, WorkflowGraph


def graph():
    return WorkflowGraph(
        nodes=[
            GraphNode(id=n, algorithm_name="test", algorithm_version="1.0.0")
            for n in ["a", "b", "c"]
        ],
        edges=[GraphEdge(id="ac", source="a", sourceHandle="out", target="c", targetHandle="in")],
    )


def job(n, state="running", suffix=""):
    return dict(
        graph_node_id=n,
        job_id=n + suffix,
        state=state,
        created_ts=1,
        parent_job_id=None,
        algorithm_name="test",
        algorithm_version="1.0.0",
        params={},
    )


def select(g, history):
    sources = {sid: (frozen, jobs) for sid, frozen, jobs in history}
    candidates = [
        (sid, j) for sid, _, jobs in history for j in reversed(jobs) if not j.get("parent_job_id")
    ]
    return {j["job_id"]: j for j in select_canvas_jobs(g, candidates, sources)}


@pytest.mark.parametrize("newer_count", [1, 7, 8, 9, 12])
def test_parallel_branches_running_beyond_eight(newer_count):
    g = graph()
    history = [(str(i), g, [job("b", suffix=str(i))]) for i in range(newer_count)]
    history += [("old", g, [job("a"), job("c")])]
    result = select(g, history)
    assert set(result) == {"a", "b0", "c"}
    assert result["a"]["state"] == "running"
    assert result["a"]["snapshot_id"] == "old"


@pytest.mark.parametrize("change", ["param", "version", "delete", "edge"])
def test_true_invalidation_includes_descendants(change):
    old = graph()
    current = deepcopy(old)
    if change == "param":
        current.nodes[0].params["x"] = 2
    if change == "version":
        current.nodes[0].algorithm_version = "2.0.0"
    if change == "delete":
        current.nodes = current.nodes[1:]
        current.edges = []
    if change == "edge":
        current.edges[0].source = "b"
    result = select(current, [("old", old, [job("a"), job("b"), job("c", "done")])])
    assert "c" not in result
    if change != "edge":
        assert "a" not in result
    assert "b" in result


def test_same_configuration_different_upstream_producer_is_not_compatible():
    g = graph()
    result = select(
        g, [("new", g, [job("a", suffix="2")]), ("old", g, [job("a"), job("c", "done")])]
    )
    assert set(result) == {"a2"}


def test_failed_attempt_does_not_fall_back_to_success_and_shards_are_scoped():
    g = graph()
    shard = {**job("b", "done", "shard"), "parent_job_id": "b"}
    old_shard = {**job("b", "failed", "old-shard"), "parent_job_id": "old-b"}
    result = select(
        g,
        [
            ("new", g, [job("a", "failed", "2"), job("b"), shard, old_shard]),
            ("old", g, [job("a", "done")]),
        ],
    )
    assert result["a2"]["state"] == "failed"
    assert set(result) == {"a2", "b", "bshard"}


@pytest.mark.asyncio
async def test_sql_history_query_has_no_window_and_keeps_running(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import aiosqlite

    from hololab.gateway.canvas_jobs import canvas_jobs

    path = tmp_path / "history.sqlite"
    async with aiosqlite.connect(path) as conn:
        await conn.executescript("""
          CREATE TABLE snapshots(snapshot_id TEXT, workflow_id TEXT, created_ts REAL);
          CREATE TABLE jobs(job_id TEXT, parent_job_id TEXT, created_ts REAL);
          CREATE TABLE snapshot_jobs(snapshot_id TEXT, graph_node_id TEXT, job_id TEXT);
        """)
        for i in range(12):
            await conn.execute("INSERT INTO snapshots VALUES (?, ?, ?)", (str(i), "w", i))
            nid = "a" if i == 0 else "b"
            await conn.execute("INSERT INTO jobs VALUES (?, NULL, ?)", (nid + str(i), i))
            await conn.execute(
                "INSERT INTO snapshot_jobs VALUES (?, ?, ?)", (str(i), nid, nid + str(i))
            )
        await conn.commit()

    class DB:
        @asynccontextmanager
        async def read(self):
            async with aiosqlite.connect(path) as conn:
                yield conn

    g = graph()
    g.execution = {"version": 1, "revision": "test", "environment_params": {}, "issues": []}
    workflows = SimpleNamespace(get_snapshot=AsyncMock(return_value=SimpleNamespace(graph=g)))
    store = SimpleNamespace(
        list_by_snapshot=AsyncMock(
            side_effect=lambda sid: [job("a" if sid == "0" else "b", suffix=sid)]
        )
    )
    app = SimpleNamespace(state=SimpleNamespace(db=DB(), workflows=workflows, jobs_store=store))
    result = await canvas_jobs(app, "w", g)
    assert {j["job_id"] for j in result} == {"a0", "b11"}
    assert all(j["state"] == "running" for j in result)
    assert workflows.get_snapshot.await_count == 2

    from hololab.gateway.preparation import analyze_execution

    monkeypatch.setattr("hololab.gateway.preparation.prepare_execution", AsyncMock(return_value=g))
    app.state.snapshot_jobs = SimpleNamespace(
        get_latest_snapshot_for_workflow=AsyncMock(return_value="11")
    )
    analysis = await analyze_execution(app, "w", g)
    assert analysis["staleness"]["a"] is None
    assert analysis["staleness"]["b"] is None
    assert {j["job_id"] for j in analysis["canvas_jobs"]} == {"a0", "b11"}


def test_three_parallel_snapshots_use_one_generation_per_branch():
    g = graph()
    result = select(
        g,
        [
            ("third", g, [job("b")]),
            ("second", g, [job("a"), job("c")]),
            ("first", g, [job("a")]),
        ],
    )
    assert set(result) == {"a", "b", "c"}
    assert all(j["state"] == "running" for j in result.values())
