from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hololab.gateway.canvas_jobs import select_canvas_jobs
from hololab.gateway.workflows import GraphEdge, GraphNode, WorkflowGraph


@pytest.fixture
def device_resolution(monkeypatch):
    """These display tests isolate invalid graphs from gateway routing/storage."""
    monkeypatch.setattr("hololab.gateway.device_bindings.resolve_devices", AsyncMock(return_value={
        "revision": 0, "assignments": {}, "routing": {}, "reports": {}, "issues": [],
    }))


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


@pytest.mark.asyncio
async def test_new_membership_uses_canvas_result_from_sibling_branch(monkeypatch):
    """Clear C, retain A/B selected from a different branch by the canvas."""
    from hololab.gateway.canvas_jobs import materialize_current_results

    g = graph()
    bridge = SimpleNamespace(attribute_many=AsyncMock())
    handles = SimpleNamespace(list_by_job=AsyncMock(return_value=[]))
    app = SimpleNamespace(state=SimpleNamespace(snapshot_jobs=bridge, handles=handles, jobs_store=SimpleNamespace(get=AsyncMock(return_value=None))))
    chosen = [job("a", "done", "-branch"), job("b", "done", "-branch"), job("c", "done", "-old")]
    monkeypatch.setattr("hololab.gateway.canvas_jobs.canvas_jobs", AsyncMock(return_value=chosen))

    await materialize_current_results(app, "w", g, "new", cleared={"c"})
    bridge.attribute_many.assert_awaited_once_with("new", [("a-branch", "a"), ("b-branch", "b")])


@pytest.mark.asyncio
@pytest.mark.parametrize("membership", [[], ["retained"], ["other-generation"]])
async def test_analysis_pairs_history_verdict_with_job_membership(monkeypatch, membership):
    from hololab.gateway.preparation import analyze_execution

    g = graph()
    g.execution = {"revision": "test", "environment_params": {}, "issues": []}
    selected = {**job("a", state="done"), "job_id": "retained", "snapshot_id": "old"}
    monkeypatch.setattr("hololab.gateway.preparation.prepare_execution", AsyncMock(return_value=g))
    monkeypatch.setattr(
        "hololab.gateway.preparation.historical_execution", AsyncMock(return_value=g)
    )
    monkeypatch.setattr(
        "hololab.gateway.canvas_jobs.canvas_jobs", AsyncMock(return_value=[selected])
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            snapshot_jobs=SimpleNamespace(
                get_latest_snapshot_for_workflow=AsyncMock(side_effect=["old", "new"])
            ),
            workflows=SimpleNamespace(get_snapshot=AsyncMock(return_value=object())),
            jobs_store=SimpleNamespace(
                list_by_snapshot=AsyncMock(return_value=[{"job_id": jid} for jid in membership])
            ),
        )
    )
    result = await analyze_execution(app, "w", g)
    assert result["canvas_jobs"][0]["historical_only"] is ("retained" not in membership)
    assert result["canvas_jobs"][0]["snapshot_id"] == "old"
    assert "historical_only" not in selected
    app.state.jobs_store.list_by_snapshot.assert_awaited_once_with("new")


@pytest.mark.asyncio
async def test_display_analysis_isolates_invalid_legacy_node_parameters(monkeypatch, device_resolution):
    """One old parameter cannot turn the whole canvas-state response into a 500."""
    from hololab.gateway.preparation import analyze_execution

    g = WorkflowGraph(
        nodes=[GraphNode(id="bad", algorithm_name="test", algorithm_version="1.0.0", params={"skip": 0})]
    )
    catalog = [{
        "name": "test", "version": "1.0.0",
        "params": {"skip": {"type": "int", "default": 1, "min": 1}},
    }]
    monkeypatch.setattr("hololab.gateway.canvas_jobs.canvas_jobs", AsyncMock(return_value=[]))
    app = SimpleNamespace(state=SimpleNamespace(
        registry=SimpleNamespace(catalog_json=lambda: catalog),
        snapshot_jobs=SimpleNamespace(get_latest_snapshot_for_workflow=AsyncMock(return_value=None)),
    ))

    result = await analyze_execution(app, "w", g, display_only=True)

    assert result["staleness"]["bad"] == {
        "kind": "invalid_params",
        "title": "参数不合法：parameter 'skip': must be >= 1, got 0",
        "changeReasons": ["参数不合法：parameter 'skip': must be >= 1, got 0"],
    }
    assert result["issues"][0]["node_id"] == "bad"


@pytest.mark.asyncio
async def test_display_analysis_isolates_zero_candidate_edge(monkeypatch, device_resolution):
    from tests.test_array_view_planner import fixture
    from hololab.gateway.preparation import analyze_execution
    g, catalog = fixture()
    catalog[0]['outputs']['out'] = dict(tags=['image'], arrayed=False)
    before = g.model_dump()
    monkeypatch.setattr('hololab.gateway.canvas_jobs.canvas_jobs', AsyncMock(return_value=[]))
    app = SimpleNamespace(state=SimpleNamespace(
        registry=SimpleNamespace(catalog_json=lambda: catalog, get_session=lambda _: None),
        snapshot_jobs=SimpleNamespace(get_latest_snapshot_for_workflow=AsyncMock(return_value=None)),
    ))
    result = await analyze_execution(app, 'w', g, display_only=True)
    assert g.model_dump() == before
    assert result['issues'][0]['edge_id'] == 'a'
    assert 'got 0' in result['staleness']['slice']['title']
    assert 'source' in result['staleness']
    assert result['canvas_jobs'] == []


def test_canvas_state_returns_200_with_invalid_edge_and_other_jobs(monkeypatch, tmp_path, device_resolution):
    from fastapi.testclient import TestClient
    from hololab.gateway.app import create_app
    from tests.test_array_view_planner import fixture
    g, catalog = fixture()
    catalog[0]['outputs']['out'] = dict(tags=['image'], arrayed=False)
    app = create_app(db_path=tmp_path / 'unused.sqlite')
    app.state.registry = SimpleNamespace(catalog_json=lambda: catalog, get_session=lambda _: None)
    app.state.workflows = SimpleNamespace(get_draft=AsyncMock(return_value=SimpleNamespace(graph=g, updated_ts=1)))
    app.state.snapshot_jobs = SimpleNamespace(get_latest_snapshot_for_workflow=AsyncMock(return_value=None))
    selected = dict(job_id='retained', graph_node_id='source', state='done', created_ts=1)
    monkeypatch.setattr('hololab.gateway.canvas_jobs.canvas_jobs', AsyncMock(return_value=[selected]))
    client = TestClient(app, raise_server_exceptions=True)
    response = client.get('/api/workflows/w/canvas-state')
    client.close()
    assert response.status_code == 200
    body = response.json()
    assert body['issues'][0]['edge_id'] == 'a'
    assert 'got 0' in body['staleness']['slice']['title']
    assert body['canvas_jobs'][0]['job_id'] == 'retained'
    assert g.edges[0].source == 'source'
