"""REST coverage for the Run history three-piece:

    GET  /api/workflows/{id}/runs                     — list of past runs
    GET  /api/snapshots/{id}                          — one run's frozen graph + jobs
    POST /api/workflows/{id}/restore-from-snapshot/{sid} — clone params back to draft

The endpoints are seeded by driving ``WorkflowStore`` and ``JobsStore``
directly through the TestClient portal so we don't need real nodes to
exercise the query + serialization paths.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.handles import Handle
from hololab.gateway.jobs import Job, JobState
from hololab.gateway.workflows import GraphEdge, GraphNode, WorkflowGraph

# ---------------------------------------------------------------------------
# Fixtures / seeding helpers
# ---------------------------------------------------------------------------


def _sample_graph(iter_value: int = 100) -> WorkflowGraph:
    """A minimal two-node graph — one param varies so we can prove
    ``restore-from-snapshot`` actually swapped things back."""

    return WorkflowGraph(
        nodes=[
            GraphNode(
                id="n1",
                algorithm_name="demo-echo",
                algorithm_version="0.1.0",
                params={"iterations": iter_value},
                assigned_node_id="node-a",
            ),
            GraphNode(
                id="n2",
                algorithm_name="demo-consume",
                algorithm_version="0.1.0",
                assigned_node_id="node-a",
            ),
        ],
        edges=[
            GraphEdge(id="e1", source="n1", sourceHandle="out", target="n2", targetHandle="in"),
        ],
    )


def _seed_snapshot_and_jobs(
    client: TestClient,
    *,
    workflow_id: str,
    graph: WorkflowGraph,
    job_states: list[str],
) -> str:
    """Insert a snapshot + one Job per state, via the app's portal.

    Returns the freshly-minted snapshot_id.
    """

    async def _seed() -> str:
        # Ensure a draft row exists so the "workflow not found" gate on
        # /api/workflows/{id}/runs doesn't fire.
        draft = await client.app.state.workflows.get_draft(workflow_id)
        if draft is None:
            await client.app.state.workflows.save_draft(
                workflow_id=workflow_id, name="test-wf", graph=graph
            )
        snap = await client.app.state.workflows.create_snapshot(
            workflow_id=workflow_id, graph=graph
        )
        for i, state in enumerate(job_states):
            job = Job(
                job_id=f"{snap.snapshot_id[:8]}-j{i}",
                snapshot_id=snap.snapshot_id,
                workflow_id=workflow_id,
                node_id="node-a",
                graph_node_id=graph.nodes[i % len(graph.nodes)].id,
                algorithm_name=graph.nodes[i % len(graph.nodes)].algorithm_name,
                algorithm_version=graph.nodes[i % len(graph.nodes)].algorithm_version,
                params=graph.nodes[i % len(graph.nodes)].params,
                input_handles={},
                state=JobState(state),
            )
            await client.app.state.jobs_store.create(job)
        return snap.snapshot_id

    return client.portal.call(_seed)


# ---------------------------------------------------------------------------
# GET /api/workflows/{id}/runs
# ---------------------------------------------------------------------------


def test_runs_list_rolls_up_states(tmp_path: Path) -> None:
    """State counts per snapshot + a single rollup that matches the badge palette."""

    app = create_app(db_path=tmp_path / "runs.sqlite")
    with TestClient(app) as client:
        wid = "wf-1"
        # Run A: everything done → "done".
        _seed_snapshot_and_jobs(
            client, workflow_id=wid, graph=_sample_graph(30), job_states=["done", "done"]
        )
        # Run B: one failed → "failed".
        _seed_snapshot_and_jobs(
            client, workflow_id=wid, graph=_sample_graph(50), job_states=["done", "failed"]
        )
        # Run C: still running → "running".
        _seed_snapshot_and_jobs(
            client, workflow_id=wid, graph=_sample_graph(80), job_states=["done", "running"]
        )

        r = client.get(f"/api/workflows/{wid}/runs")
        assert r.status_code == 200
        runs = r.json()
        assert len(runs) == 3

        # Newest-first ordering — C, B, A.
        by_state = [row["state"] for row in runs]
        assert by_state == ["running", "failed", "done"]

        # Every row carries counts and the frozen graph size.
        for row in runs:
            assert row["job_count"] == 2
            assert row["node_count"] == 2
            assert sum(row["state_counts"].values()) == 2


def test_runs_list_404_on_unknown_workflow(tmp_path: Path) -> None:
    """Distinguishes 'no runs yet' (200 + []) from 'no such workflow' (404)."""

    app = create_app(db_path=tmp_path / "runs.sqlite")
    with TestClient(app) as client:
        assert client.get("/api/workflows/does-not-exist/runs").status_code == 404


def test_runs_list_empty_for_draft_without_snapshots(tmp_path: Path) -> None:
    """A workflow that was saved but never Run returns an empty list, not 404."""

    app = create_app(db_path=tmp_path / "runs.sqlite")
    with TestClient(app) as client:

        async def _seed_draft() -> None:
            await client.app.state.workflows.save_draft(
                workflow_id="wf-empty", name="draft-only", graph=_sample_graph()
            )

        client.portal.call(_seed_draft)

        r = client.get("/api/workflows/wf-empty/runs")
        assert r.status_code == 200
        assert r.json() == []


# ---------------------------------------------------------------------------
# GET /api/snapshots/{id}
# ---------------------------------------------------------------------------


def test_snapshot_detail_returns_frozen_graph_and_jobs(tmp_path: Path) -> None:
    """The frozen graph AND its jobs come back together for the read-only view."""

    app = create_app(db_path=tmp_path / "snap.sqlite")
    with TestClient(app) as client:
        wid = "wf-detail"
        snap_id = _seed_snapshot_and_jobs(
            client, workflow_id=wid, graph=_sample_graph(200), job_states=["done", "done"]
        )

        r = client.get(f"/api/snapshots/{snap_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["snapshot_id"] == snap_id
        assert body["workflow_id"] == wid
        # Frozen graph is present, with the exact param we baked in.
        assert body["graph"]["nodes"][0]["params"]["iterations"] == 200
        # Jobs are attached and carry the frozen params for that step.
        assert len(body["jobs"]) == 2
        assert body["jobs"][0]["params"] == {"iterations": 200}


def test_snapshot_detail_includes_output_handles_per_job(tmp_path: Path) -> None:
    """Handles produced by each job show up as {port_name: handle_id} on the job.

    The read-only snapshot canvas uses these to mount preview drawers on
    nodes exactly the way the live canvas does. Jobs with no named output
    handles get ``output_handles = null`` (not an empty dict — matches the
    JobUpdate WS payload's convention).
    """

    app = create_app(db_path=tmp_path / "snap.sqlite")
    with TestClient(app) as client:
        wid = "wf-oh"
        snap_id = _seed_snapshot_and_jobs(
            client, workflow_id=wid, graph=_sample_graph(200), job_states=["done", "done"]
        )
        detail = client.get(f"/api/snapshots/{snap_id}").json()
        first_job_id = detail["jobs"][0]["job_id"]

        # Register two output handles on the first job: one named, one un-named
        # (the latter must not show up in output_handles).
        async def _seed_handles() -> None:
            await client.app.state.handles.register(
                Handle(
                    handle_id="h-named",
                    node_id="node-a",
                    storage="file",
                    path="/tmp/out.txt",
                    job_id=first_job_id,
                    output_port_name="out",
                )
            )
            await client.app.state.handles.register(
                Handle(
                    handle_id="h-unnamed",
                    node_id="node-a",
                    storage="file",
                    path="/tmp/scratch.bin",
                    job_id=first_job_id,
                    output_port_name=None,
                )
            )

        client.portal.call(_seed_handles)

        body = client.get(f"/api/snapshots/{snap_id}").json()
        jobs = {j["job_id"]: j for j in body["jobs"]}
        # The seeded job has one named handle only.
        assert jobs[first_job_id]["output_handles"] == {"out": "h-named"}
        # The other job produced no handles → null (not {}).
        other_job = next(j for jid, j in jobs.items() if jid != first_job_id)
        assert other_job["output_handles"] is None


def test_snapshot_detail_404_on_unknown(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "snap.sqlite")
    with TestClient(app) as client:
        assert client.get("/api/snapshots/none").status_code == 404


# ---------------------------------------------------------------------------
# POST /api/workflows/{id}/restore-from-snapshot/{sid}
# ---------------------------------------------------------------------------


def test_restore_from_snapshot_overwrites_draft_params(tmp_path: Path) -> None:
    """The classic loop: tweak draft, run, tweak again, want the old params back."""

    app = create_app(db_path=tmp_path / "restore.sqlite")
    with TestClient(app) as client:
        wid = "wf-restore"
        # Seed: run with iterations=30, then mutate draft to iterations=99.
        snap_id = _seed_snapshot_and_jobs(
            client, workflow_id=wid, graph=_sample_graph(30), job_states=["done", "done"]
        )

        async def _mutate_draft() -> None:
            await client.app.state.workflows.save_draft(
                workflow_id=wid, name="test-wf", graph=_sample_graph(99)
            )

        client.portal.call(_mutate_draft)

        # Restore: draft's iterations should snap back to 30.
        r = client.post(f"/api/workflows/{wid}/restore-from-snapshot/{snap_id}")
        assert r.status_code == 200
        assert r.json()["restored_from_snapshot_id"] == snap_id

        draft = client.get(f"/api/workflows/{wid}").json()
        assert draft["graph"]["nodes"][0]["params"]["iterations"] == 30


def test_workflows_list_includes_node_count_and_last_run(tmp_path: Path) -> None:
    """The Gallery landing page needs these per-row without a second round-trip."""

    app = create_app(db_path=tmp_path / "gal.sqlite")
    with TestClient(app) as client:
        # Workflow A: 2 nodes, one Run that's all-done.
        wid_a = "wf-a"
        _seed_snapshot_and_jobs(
            client, workflow_id=wid_a, graph=_sample_graph(10), job_states=["done", "done"]
        )
        # Workflow B: 2 nodes, one Run with a failure.
        wid_b = "wf-b"
        _seed_snapshot_and_jobs(
            client, workflow_id=wid_b, graph=_sample_graph(20), job_states=["done", "failed"]
        )

        # Workflow C: saved but never run — last_run must be null.
        async def _seed_c() -> None:
            await client.app.state.workflows.save_draft(
                workflow_id="wf-c", name="draft-only", graph=_sample_graph()
            )

        client.portal.call(_seed_c)

        rows = client.get("/api/workflows").json()
        by_id = {r["workflow_id"]: r for r in rows}

        # Node counts came from the frozen draft graph.
        assert by_id[wid_a]["node_count"] == 2
        assert by_id[wid_b]["node_count"] == 2
        assert by_id["wf-c"]["node_count"] == 2

        # last_run rollup follows the same rules as /runs.
        assert by_id[wid_a]["last_run"]["state"] == "done"
        assert by_id[wid_b]["last_run"]["state"] == "failed"
        assert by_id["wf-c"]["last_run"] is None

        # And carries counts so the pip can render immediately.
        assert by_id[wid_a]["last_run"]["job_count"] == 2
        assert by_id[wid_b]["last_run"]["state_counts"]["failed"] == 1


def test_restore_rejects_cross_workflow_snapshot(tmp_path: Path) -> None:
    """You cannot use a snapshot from workflow A to overwrite workflow B's draft."""

    app = create_app(db_path=tmp_path / "restore.sqlite")
    with TestClient(app) as client:

        async def _seed_wf(wid: str) -> None:
            await client.app.state.workflows.save_draft(
                workflow_id=wid, name=f"wf-{wid}", graph=_sample_graph()
            )

        client.portal.call(_seed_wf, "wf-a")
        client.portal.call(_seed_wf, "wf-b")
        snap_a = _seed_snapshot_and_jobs(
            client, workflow_id="wf-a", graph=_sample_graph(10), job_states=["done"]
        )

        r = client.post(f"/api/workflows/wf-b/restore-from-snapshot/{snap_a}")
        assert r.status_code == 400
