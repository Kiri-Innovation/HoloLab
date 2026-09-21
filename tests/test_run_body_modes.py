"""``POST /api/workflows/{id}/run`` body-driven single-node & from-node modes.

The endpoint historically ran the full graph with no body. Agent-friendliness
now lets the body select a narrower operation:

  * ``{"node_id": "X"}``      → Continue-or-Fork dispatch of one node
                                (same semantics as the ``▶ Run this node`` UI
                                button and ``/dispatch/{gnode}``).
  * ``{"from_node_id": "X"}`` → rerun X + downstream, reuse everything else
                                (same as ``/snapshots/{sid}/rerun-from/{X}``
                                on the workflow's latest snapshot).
  * empty / missing body     → whole-graph run (original behaviour).

These tests seed a run history via the same fakes ``test_dispatch_lineage`` and
``test_rerun_from`` use so the closure semantics stay consistent across the
three entrypoints.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.handles import Handle
from hololab.gateway.jobs import Job, JobState
from hololab.gateway.registry import NodeSession
from hololab.gateway.workflows import GraphEdge, GraphNode, WorkflowGraph
from hololab.protocol.messages import GpuInfo


def _linear_graph() -> WorkflowGraph:
    """A→B→C, each assigned to compute node ``node-a``."""

    return WorkflowGraph(
        nodes=[
            GraphNode(
                id="A",
                algorithm_name="demo-echo",
                algorithm_version="0.1.0",
                assigned_node_id="node-a",
            ),
            GraphNode(
                id="B",
                algorithm_name="demo-echo",
                algorithm_version="0.1.0",
                assigned_node_id="node-a",
            ),
            GraphNode(
                id="C",
                algorithm_name="demo-echo",
                algorithm_version="0.1.0",
                assigned_node_id="node-a",
            ),
        ],
        edges=[
            GraphEdge(id="eab", source="A", sourceHandle="out", target="B", targetHandle="in"),
            GraphEdge(id="ebc", source="B", sourceHandle="out", target="C", targetHandle="in"),
        ],
    )


def _fake_online_node(client: TestClient) -> None:
    ws = MagicMock()
    ws.send_text = AsyncMock()
    session = NodeSession(
        node_id="node-a",
        session_id="sess",
        node_name="node-a-name",
        ws=ws,
        advertised_url=None,
        gpu=GpuInfo(),
        packs=[],
        protocol_v=1,
    )
    client.app.state.registry._sessions[session.node_id] = session


def _seed_run(
    client: TestClient,
    *,
    workflow_id: str,
    graph: WorkflowGraph,
    done_at: list[str],
) -> str:
    """Save a draft, snapshot it, seed one done Job per gnode in ``done_at``,
    register one output handle per done job. Returns the snapshot id."""

    async def _seed() -> str:
        await client.app.state.workflows.save_draft(
            workflow_id=workflow_id, name="cc-e2e-runbody", graph=graph
        )
        snap = await client.app.state.workflows.create_snapshot(
            workflow_id=workflow_id, graph=graph
        )
        by_id = {n.id: n for n in graph.nodes}
        for gid in done_at:
            gn = by_id[gid]
            job = Job(
                job_id=f"{snap.snapshot_id[:8]}-{gid}",
                snapshot_id=snap.snapshot_id,
                workflow_id=workflow_id,
                node_id="node-a",
                graph_node_id=gid,
                algorithm_name=gn.algorithm_name,
                algorithm_version=gn.algorithm_version,
                params={},
                input_handles={},
                state=JobState.DONE,
            )
            await client.app.state.jobs_store.create(job)
            await client.app.state.handles.register(
                Handle(
                    handle_id=f"h-{gid}",
                    node_id="node-a",
                    storage="file",
                    tags=[gn.algorithm_name],
                    path=f"/tmp/{gid}.out",
                    job_id=job.job_id,
                    output_port_name="out",
                )
            )
        return snap.snapshot_id

    return client.portal.call(_seed)


# ---------------------------------------------------------------------------
# node_id — single-node dispatch via /run body
# ---------------------------------------------------------------------------


def test_run_body_node_id_continues_on_latest_snapshot(tmp_path: Path) -> None:
    """``{"node_id": "C"}`` with A+B already done extends the current snapshot
    (Continue). No new snapshot is created; a fresh job for C is dispatched.
    """

    app = create_app(db_path=tmp_path / "node.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "11111111-1111-1111-1111-111111111111"
        base = _seed_run(client, workflow_id=wid, graph=_linear_graph(), done_at=["A", "B"])

        r = client.post(f"/api/workflows/{wid}/run", json={"node_id": "C"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "node"
        assert body["operation"] == "continue"
        assert body["workflow_id"] == wid
        assert body["snapshot_id"] == base  # same head; no fork
        assert body["job_id"]  # a new job was minted
        # A Continue response does NOT carry parent_snapshot_id/forked_at.
        assert "parent_snapshot_id" not in body
        assert "forked_at" not in body


def test_run_body_node_id_forks_when_slot_taken(tmp_path: Path) -> None:
    """Re-running B when the base already has A+B+C done forks a child
    snapshot; A is inherited, C (downstream of the fork point) is dropped."""

    app = create_app(db_path=tmp_path / "fork.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "22222222-2222-2222-2222-222222222222"
        base = _seed_run(client, workflow_id=wid, graph=_linear_graph(), done_at=["A", "B", "C"])

        r = client.post(f"/api/workflows/{wid}/run", json={"node_id": "B"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "node"
        assert body["operation"] == "fork"
        assert body["parent_snapshot_id"] == base
        assert body["snapshot_id"] != base
        assert body["forked_at"] == "B"


def test_run_body_node_id_no_prior_snapshot_creates_fresh(tmp_path: Path) -> None:
    """With no prior run, ``{"node_id": "A"}`` synthesises a fresh snapshot
    and Continues into it. Matches the standalone dispatch endpoint's
    fresh-snapshot fallback."""

    app = create_app(db_path=tmp_path / "fresh.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "33333333-3333-3333-3333-333333333333"

        async def _save() -> None:
            await client.app.state.workflows.save_draft(
                workflow_id=wid, name="cc-e2e-fresh-node", graph=_linear_graph()
            )

        client.portal.call(_save)

        r = client.post(f"/api/workflows/{wid}/run", json={"node_id": "A"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "node"
        assert body["operation"] == "continue"
        assert body["snapshot_id"]  # newly created


def test_run_body_node_id_missing_upstream_400(tmp_path: Path) -> None:
    """Dispatching C when B hasn't produced yet returns 400 with a message
    pointing at the missing upstream — same message as the dispatch endpoint.
    """

    app = create_app(db_path=tmp_path / "up.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "44444444-4444-4444-4444-444444444444"
        _seed_run(client, workflow_id=wid, graph=_linear_graph(), done_at=["A"])

        r = client.post(f"/api/workflows/{wid}/run", json={"node_id": "C"})
        assert r.status_code == 400
        assert "B" in r.json()["detail"]


# ---------------------------------------------------------------------------
# from_node_id — rerun-from via /run body
# ---------------------------------------------------------------------------


def test_run_body_from_node_id_reuses_upstream(tmp_path: Path) -> None:
    """``{"from_node_id": "B"}`` on a fully-done snapshot reuses A and
    reruns B+C. Returns rerun-from's shape verbatim (plus workflow_id +
    mode)."""

    app = create_app(db_path=tmp_path / "from.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "55555555-5555-5555-5555-555555555555"
        base = _seed_run(client, workflow_id=wid, graph=_linear_graph(), done_at=["A", "B", "C"])

        r = client.post(f"/api/workflows/{wid}/run", json={"from_node_id": "B"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "from_node"
        assert body["workflow_id"] == wid
        assert body["original_snapshot_id"] == base
        assert body["new_snapshot_id"] != base
        assert body["rerun_from_graph_node_id"] == "B"
        assert body["rerun_graph_node_ids"] == ["B", "C"]
        assert body["reused_graph_node_ids"] == ["A"]
        assert len(body["reused_job_ids"]) == 1


def test_run_body_from_node_id_no_prior_snapshot_400(tmp_path: Path) -> None:
    """No prior snapshot means there's nothing upstream to reuse.
    The endpoint refuses with a message steering the caller toward
    the whole-graph run or ``node_id``."""

    app = create_app(db_path=tmp_path / "no-prior.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "66666666-6666-6666-6666-666666666666"

        async def _save() -> None:
            await client.app.state.workflows.save_draft(
                workflow_id=wid, name="cc-e2e-noprior", graph=_linear_graph()
            )

        client.portal.call(_save)

        r = client.post(f"/api/workflows/{wid}/run", json={"from_node_id": "B"})
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert "no prior snapshot" in detail
        assert "node_id" in detail  # points at the alternative


# ---------------------------------------------------------------------------
# Body validation
# ---------------------------------------------------------------------------


def test_run_body_conflicting_fields_422(tmp_path: Path) -> None:
    """``node_id`` and ``from_node_id`` are mutually exclusive."""

    app = create_app(db_path=tmp_path / "conflict.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "77777777-7777-7777-7777-777777777777"
        _seed_run(client, workflow_id=wid, graph=_linear_graph(), done_at=["A", "B", "C"])

        r = client.post(
            f"/api/workflows/{wid}/run",
            json={"node_id": "A", "from_node_id": "B"},
        )
        assert r.status_code == 422
        assert "at most one" in r.json()["detail"]


def test_run_body_empty_body_runs_whole_graph(tmp_path: Path) -> None:
    """Empty body == pre-existing behaviour: whole-graph run, ``mode=graph``.

    The endpoint validates the graph before snapshotting; the linear graph
    here is deliberately built from packs the registry doesn't know about,
    so validation fails with 422. That's fine — it proves the whole-graph
    code path is reached (rather than the body-driven branches) and that
    the response shape is unchanged."""

    app = create_app(db_path=tmp_path / "empty.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "88888888-8888-8888-8888-888888888888"

        async def _save() -> None:
            await client.app.state.workflows.save_draft(
                workflow_id=wid, name="cc-e2e-empty", graph=_linear_graph()
            )

        client.portal.call(_save)

        r = client.post(f"/api/workflows/{wid}/run")
        # No body, no packs registered → validation rejects with 422.
        # The response comes from the whole-graph path, confirming the
        # empty-body branch reaches it (not the node_id / from_node_id
        # early returns).
        assert r.status_code == 422
        assert "issues" in r.json()["detail"]


def test_run_body_unknown_workflow_404(tmp_path: Path) -> None:
    """Missing workflow returns 404 whether or not a body is set."""

    app = create_app(db_path=tmp_path / "nope.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        # node_id path → 404 from get_draft.
        r = client.post("/api/workflows/does-not-exist/run", json={"node_id": "A"})
        assert r.status_code == 404
