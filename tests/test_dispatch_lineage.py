"""Dispatch (Continue/Fork) endpoint and artifact lineage API.

Covers the V8 lineage-first snapshot model:

* ``POST /api/workflows/{wid}/dispatch/{gnode}`` — routes to either a
  Continue (extend the base snapshot) or a Fork (create a child
  snapshot inheriting parent attributions except downstream of the
  fork point). Both operations are exercised with a single seeded run
  as the base.
* ``GET /api/artifacts/{handle_id}/lineage`` — walks the provenance
  DAG: producing job's ``input_handles`` supply the ancestor edges,
  scanning ``jobs.input_handles_json`` for descendants.
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
    # AsyncMock for ws so ``await ws.send_text(...)`` inside the
    # dispatch path resolves cleanly — MagicMock returns a MagicMock,
    # which trips ``TypeError: object MagicMock can't be used in
    # 'await' expression`` on the send.
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


def _seed_snapshot_with_jobs(
    client: TestClient,
    *,
    workflow_id: str,
    graph: WorkflowGraph,
    done_at: list[str],
) -> str:
    """Save a draft, create a snapshot, seed one done Job per graph node
    listed in ``done_at``, and register one output handle per done job."""

    async def _seed() -> str:
        await client.app.state.workflows.save_draft(
            workflow_id=workflow_id, name="cc-e2e-dispatch", graph=graph
        )
        snap = await client.app.state.workflows.create_snapshot(
            workflow_id=workflow_id, graph=graph
        )
        gnode_by_id = {n.id: n for n in graph.nodes}
        for gid in done_at:
            gn = gnode_by_id[gid]
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
# Continue: dispatching an empty slot in an existing snapshot
# ---------------------------------------------------------------------------


def test_dispatch_continues_when_slot_empty(tmp_path: Path) -> None:
    """A snapshot with A + B done, dispatching C consumes B's artifact
    and *extends* the same snapshot — no fork, snapshot_id unchanged."""

    app = create_app(db_path=tmp_path / "cont.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "11111111-1111-1111-1111-111111111111"
        base = _seed_snapshot_with_jobs(
            client, workflow_id=wid, graph=_linear_graph(), done_at=["A", "B"]
        )

        r = client.post(f"/api/workflows/{wid}/dispatch/C", params={"base_snapshot_id": base})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["operation"] == "continue"
        assert body["snapshot_id"] == base
        assert "parent_snapshot_id" not in body
        assert body["job_id"]  # a fresh job id was minted


def test_dispatch_forks_when_slot_taken(tmp_path: Path) -> None:
    """A snapshot with A + B + C done. Dispatching B (fork) creates a
    NEW snapshot; the new snapshot inherits A but drops B + C (C is
    downstream of B in the current graph)."""

    app = create_app(db_path=tmp_path / "fork.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "22222222-2222-2222-2222-222222222222"
        base = _seed_snapshot_with_jobs(
            client, workflow_id=wid, graph=_linear_graph(), done_at=["A", "B", "C"]
        )

        r = client.post(f"/api/workflows/{wid}/dispatch/B", params={"base_snapshot_id": base})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["operation"] == "fork"
        assert body["snapshot_id"] != base
        assert body["parent_snapshot_id"] == base
        assert body["forked_at"] == "B"

        # The new snapshot inherits A (upstream of the fork) but not B
        # nor C (fork point + downstream). B has the newly dispatched
        # job that hasn't finished yet, so it's a running/pending row
        # not an attribution.
        detail = client.get(f"/api/snapshots/{body['snapshot_id']}").json()
        by_gnode = {j["graph_node_id"]: j for j in detail["jobs"]}
        # A is inherited from the base — attributed to the origin job.
        assert "A" in by_gnode
        assert by_gnode["A"]["state"] == "done"
        assert by_gnode["A"]["job_id"] == f"{base[:8]}-A"
        # C is NOT inherited (it's downstream of the fork point).
        assert "C" not in by_gnode


def test_dispatch_no_base_snapshot_creates_fresh(tmp_path: Path) -> None:
    """When ``base_snapshot_id`` is omitted (and the workflow has no
    prior snapshot), the endpoint synthesises a fresh one and Continues
    into it."""

    app = create_app(db_path=tmp_path / "fresh.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "33333333-3333-3333-3333-333333333333"
        graph = _linear_graph()
        # No prior run — just save the draft.

        async def _save() -> None:
            await client.app.state.workflows.save_draft(
                workflow_id=wid, name="cc-e2e-fresh", graph=graph
            )

        client.portal.call(_save)

        # Dispatch A. No inputs required (A has no upstream edges) so
        # this succeeds even without any pre-attributed artifacts.
        r = client.post(f"/api/workflows/{wid}/dispatch/A")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["operation"] == "continue"
        # A brand-new snapshot was created (nothing to inherit from).
        assert body["snapshot_id"]


def test_dispatch_missing_upstream_400(tmp_path: Path) -> None:
    """Dispatching C on a snapshot where B hasn't produced an artifact
    yet returns 400 with a human-readable message pointing at B."""

    app = create_app(db_path=tmp_path / "up.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "44444444-4444-4444-4444-444444444444"
        base = _seed_snapshot_with_jobs(
            client, workflow_id=wid, graph=_linear_graph(), done_at=["A"]
        )
        r = client.post(f"/api/workflows/{wid}/dispatch/C", params={"base_snapshot_id": base})
        assert r.status_code == 400
        assert "B" in r.json()["detail"]


def _seed_attributed_job(
    client: TestClient,
    *,
    workflow_id: str,
    snapshot_id: str,
    graph_node_id: str,
    job_id: str,
    state: JobState,
    algorithm_name: str = "demo-echo",
    algorithm_version: str = "0.1.0",
    progress_current: int | None = None,
    progress_total: int | None = None,
    expected_shards: int | None = None,
) -> None:
    """Seed a job row + its ``snapshot_jobs`` attribution row.

    Mirrors the 2026-09-22 "attribute-at-creation" invariant: every
    job that belongs to a snapshot has an attribution row from the
    moment it's created, not later on DONE. Tests use this to stage
    the various states (RUNNING / FAILED / CANCELLED) the resolver
    now branches on.
    """

    async def _seed() -> None:
        job = Job(
            job_id=job_id,
            snapshot_id=snapshot_id,
            workflow_id=workflow_id,
            node_id="node-a",
            graph_node_id=graph_node_id,
            algorithm_name=algorithm_name,
            algorithm_version=algorithm_version,
            params={},
            input_handles={},
            state=state,
            progress_current=progress_current,
            progress_total=progress_total,
            expected_shards=expected_shards,
        )
        await client.app.state.jobs_store.create(job)
        await client.app.state.snapshot_jobs.attribute(snapshot_id, job_id, graph_node_id)

    client.portal.call(_seed)


def test_dispatch_upstream_running_reports_shard_progress(tmp_path: Path) -> None:
    """When the upstream job is attributed but still RUNNING, dispatching
    the downstream must say "still running (X/Y shards)" — not the flat
    "no produced artifact; run it first".

    Regression for 2026-09-22: attribution is now written at parent
    job creation (``_prepare_fanout`` + ``_dispatch_job``), so the
    resolver sees a filled slot mid-fan-out and can inspect the job's
    state directly. Under the old "attribute-on-DONE" model the slot
    read as empty during the fan-out window and the operator was told
    to "run it first" — misleading since they already had.
    """

    app = create_app(db_path=tmp_path / "inflight.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "66666666-6666-6666-6666-666666666666"
        base = _seed_snapshot_with_jobs(
            client, workflow_id=wid, graph=_linear_graph(), done_at=["A"]
        )
        _seed_attributed_job(
            client,
            workflow_id=wid,
            snapshot_id=base,
            graph_node_id="B",
            job_id="b-parent",
            state=JobState.RUNNING,
            progress_current=7,
            progress_total=10,
            expected_shards=10,
        )

        r = client.post(f"/api/workflows/{wid}/dispatch/C", params={"base_snapshot_id": base})
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert "'B'" in detail, detail
        assert "running" in detail, detail
        assert "7/10 shards" in detail, detail
        # Old flat message must not leak — user should wait, not re-run.
        assert "run it first" not in detail, detail
        assert "no produced artifact" not in detail, detail


def test_dispatch_upstream_failed_reports_reruns_message(tmp_path: Path) -> None:
    """FAILED upstream (attribution kept per the "keep attribution on
    failure" policy) must produce "last attempt failed; re-run it" —
    NOT the "still running" wait message and NOT the "no produced
    artifact" empty-slot message.
    """

    app = create_app(db_path=tmp_path / "failed.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "77777777-7777-7777-7777-777777777777"
        base = _seed_snapshot_with_jobs(
            client, workflow_id=wid, graph=_linear_graph(), done_at=["A"]
        )
        _seed_attributed_job(
            client,
            workflow_id=wid,
            snapshot_id=base,
            graph_node_id="B",
            job_id="b-failed",
            state=JobState.FAILED,
        )

        r = client.post(f"/api/workflows/{wid}/dispatch/C", params={"base_snapshot_id": base})
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert "'B'" in detail, detail
        assert "failed" in detail.lower(), detail
        assert "re-run" in detail, detail
        # Distinguish from the RUNNING / EMPTY branches.
        assert "still running" not in detail, detail
        assert "no produced artifact" not in detail, detail


def test_dispatch_continue_when_upstream_slot_holds_failed_only(tmp_path: Path) -> None:
    """Continue-vs-Fork must treat a FAILED slot as empty so a re-run of
    the failed slot stays a Continue (not a Fork).

    Attribute-at-creation means the failed job's attribution row is
    still in ``snapshot_jobs``; a naive ``get_job_at`` returns it and
    the pre-refactor code would Fork. Post-refactor, ``dispatch_graph_node``
    checks the candidate's state and skips terminal-fail states, so a
    dispatch onto the same graph_node stays a Continue and the fresh
    attempt appends a new attribution row (newer ``created_ts``) that
    ``get_job_at`` will now prefer.
    """

    app = create_app(db_path=tmp_path / "cont-fail.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "88888888-8888-8888-8888-888888888888"
        base = _seed_snapshot_with_jobs(client, workflow_id=wid, graph=_linear_graph(), done_at=[])
        # A slot for B: FAILED attribution + no downstream.
        _seed_attributed_job(
            client,
            workflow_id=wid,
            snapshot_id=base,
            graph_node_id="B",
            job_id="b-failed",
            state=JobState.FAILED,
        )
        # A is not a real prerequisite here (B has no upstream in
        # ``_linear_graph`` beyond A). Seed A DONE so B's re-dispatch
        # can resolve its input.
        _seed_attributed_job(
            client,
            workflow_id=wid,
            snapshot_id=base,
            graph_node_id="A",
            job_id="a-done",
            state=JobState.DONE,
        )

        async def _seed_a_handle() -> None:
            await client.app.state.handles.register(
                Handle(
                    handle_id="h-A",
                    node_id="node-a",
                    storage="file",
                    tags=["demo-echo"],
                    path="/tmp/A.out",
                    job_id="a-done",
                    output_port_name="out",
                )
            )

        client.portal.call(_seed_a_handle)

        r = client.post(f"/api/workflows/{wid}/dispatch/B", params={"base_snapshot_id": base})
        assert r.status_code == 200, r.text
        body = r.json()
        # Same snapshot, Continue not Fork.
        assert body["operation"] == "continue", body
        assert body["snapshot_id"] == base, body
        assert "parent_snapshot_id" not in body, body


def test_dispatch_attribution_written_at_creation(tmp_path: Path) -> None:
    """After dispatching a graph node, ``snapshot_jobs`` must already
    hold an attribution row for it — before the job reaches DONE.

    Regression for 2026-09-22: attribution used to land on WS
    ``job_done``; downstream dispatches during the running window
    couldn't find the slot. Post-refactor the row is written inside
    ``_dispatch_job`` right after ``store.create``, so a same-tick
    ``get_job_at`` returns it.
    """

    app = create_app(db_path=tmp_path / "attr-early.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "99999999-9999-9999-9999-999999999999"
        graph = _linear_graph()

        async def _save() -> None:
            await client.app.state.workflows.save_draft(
                workflow_id=wid, name="cc-e2e-attr-early", graph=graph
            )

        client.portal.call(_save)

        r = client.post(f"/api/workflows/{wid}/dispatch/A")
        assert r.status_code == 200, r.text
        body = r.json()
        snap = body["snapshot_id"]
        job_id = body["job_id"]

        async def _check() -> str | None:
            return await client.app.state.snapshot_jobs.get_job_at(snap, "A")

        attributed = client.portal.call(_check)
        # The attribution IS the freshly-minted job — even though the
        # job has only just been ASSIGNED (no DONE frame yet).
        assert attributed == job_id, (attributed, job_id)


def test_dispatch_unknown_graph_node_404(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "unk.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "55555555-5555-5555-5555-555555555555"
        graph = _linear_graph()

        async def _save() -> None:
            await client.app.state.workflows.save_draft(
                workflow_id=wid, name="cc-e2e-unk", graph=graph
            )

        client.portal.call(_save)

        r = client.post(f"/api/workflows/{wid}/dispatch/nope")
        # 400 (dispatcher raises DispatchError) — 404 would need
        # separate handling in the endpoint layer; the current
        # implementation collapses both into 400 with a clear message.
        assert r.status_code in (400, 404)
        assert "nope" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Provenance API
# ---------------------------------------------------------------------------


def test_lineage_ancestor_walk(tmp_path: Path) -> None:
    """h-C's ancestors are the artifacts fed into C's producing job.

    Seed the DAG: A produces h-A, B (consumed h-A) produces h-B, C
    (consumed h-B) produces h-C. Asking for h-C's lineage up returns
    h-B and h-A (transitively) with a single edge per hop.
    """

    app = create_app(db_path=tmp_path / "lin.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        graph = _linear_graph()

        async def _seed() -> None:
            await client.app.state.workflows.save_draft(
                workflow_id=wid, name="cc-e2e-lineage", graph=graph
            )
            snap = await client.app.state.workflows.create_snapshot(workflow_id=wid, graph=graph)
            for gid, upstream_port_map in [
                ("A", {}),
                ("B", {"in": "h-A"}),
                ("C", {"in": "h-B"}),
            ]:
                gn = next(n for n in graph.nodes if n.id == gid)
                job = Job(
                    job_id=f"j-{gid}",
                    snapshot_id=snap.snapshot_id,
                    workflow_id=wid,
                    node_id="node-a",
                    graph_node_id=gid,
                    algorithm_name=gn.algorithm_name,
                    algorithm_version=gn.algorithm_version,
                    params={},
                    input_handles=upstream_port_map,
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

        client.portal.call(_seed)

        r = client.get("/api/artifacts/h-C/lineage", params={"direction": "up"})
        assert r.status_code == 200, r.text
        body = r.json()
        node_ids = {n["artifact_id"] for n in body["nodes"]}
        assert node_ids == {"h-A", "h-B", "h-C"}
        edges = {(e["parent"], e["child"]) for e in body["edges"]}
        assert ("h-A", "h-B") in edges
        assert ("h-B", "h-C") in edges


def test_lineage_descendant_walk(tmp_path: Path) -> None:
    """h-A's descendants include everything downstream: h-B and h-C."""

    app = create_app(db_path=tmp_path / "des.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        graph = _linear_graph()

        async def _seed() -> None:
            await client.app.state.workflows.save_draft(
                workflow_id=wid, name="cc-e2e-desc", graph=graph
            )
            snap = await client.app.state.workflows.create_snapshot(workflow_id=wid, graph=graph)
            for gid, upstream_port_map in [
                ("A", {}),
                ("B", {"in": "h-A"}),
                ("C", {"in": "h-B"}),
            ]:
                gn = next(n for n in graph.nodes if n.id == gid)
                job = Job(
                    job_id=f"j-{gid}",
                    snapshot_id=snap.snapshot_id,
                    workflow_id=wid,
                    node_id="node-a",
                    graph_node_id=gid,
                    algorithm_name=gn.algorithm_name,
                    algorithm_version=gn.algorithm_version,
                    params={},
                    input_handles=upstream_port_map,
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

        client.portal.call(_seed)

        r = client.get("/api/artifacts/h-A/lineage", params={"direction": "down"})
        assert r.status_code == 200, r.text
        body = r.json()
        node_ids = {n["artifact_id"] for n in body["nodes"]}
        assert node_ids == {"h-A", "h-B", "h-C"}


def test_lineage_missing_artifact_404(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "miss.sqlite")
    with TestClient(app) as client:
        r = client.get("/api/artifacts/does-not-exist/lineage")
        assert r.status_code == 404
