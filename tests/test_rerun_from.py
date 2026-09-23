"""Rerun-from-node + orphaned/interrupted-job semantics.

Covers:
  * JobState.INTERRUPTED is terminal (no outbound transitions).
  * ``mark_stuck_jobs_orphaned`` (gateway startup) flips assigned/running
    jobs to ``orphaned`` so the register-time reconciler can hoist them
    back to ``running``; ``pending`` and terminal jobs are left alone.
  * ``downstream_closure`` returns start + transitive downstream.
  * ``POST /api/snapshots/{sid}/rerun-from/{gid}`` — happy path (reused
    upstream + re-executed downstream) plus 404/400 rejection paths.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.handles import Handle
from hololab.gateway.jobs import IllegalTransition, Job, JobState, JobStateMachine
from hololab.gateway.registry import NodeSession, mark_stuck_jobs_orphaned
from hololab.gateway.workflows import GraphEdge, GraphNode, WorkflowGraph, downstream_closure
from hololab.persistence.db import open_database
from hololab.protocol.messages import GpuInfo

# ---------------------------------------------------------------------------
# JobState.INTERRUPTED — terminal
# ---------------------------------------------------------------------------


def test_interrupted_is_terminal_no_outbound_transitions() -> None:
    job = Job(
        job_id="j",
        workflow_id="w",
        snapshot_id="s",
        algorithm_name="demo-echo",
        algorithm_version="0.1.0",
        state=JobState.INTERRUPTED,
    )
    for target in (
        JobState.PENDING,
        JobState.ASSIGNED,
        JobState.RUNNING,
        JobState.DONE,
        JobState.FAILED,
        JobState.ORPHANED,
    ):
        with pytest.raises(IllegalTransition):
            JobStateMachine.transition(job, target)


def test_running_can_transition_to_interrupted() -> None:
    job = Job(
        job_id="j",
        workflow_id="w",
        snapshot_id="s",
        algorithm_name="demo-echo",
        algorithm_version="0.1.0",
        state=JobState.RUNNING,
    )
    new = JobStateMachine.transition(job, JobState.INTERRUPTED)
    assert new.state is JobState.INTERRUPTED


def test_orphaned_can_transition_to_done() -> None:
    """Reconciliation relies on ``orphaned → done`` being legal so a
    terminal frame that races past the register-time hoist still
    lands cleanly instead of being rejected as illegal."""

    job = Job(
        job_id="j",
        workflow_id="w",
        snapshot_id="s",
        algorithm_name="demo-echo",
        algorithm_version="0.1.0",
        state=JobState.ORPHANED,
    )
    new = JobStateMachine.transition(job, JobState.DONE)
    assert new.state is JobState.DONE


# ---------------------------------------------------------------------------
# Startup sweep
# ---------------------------------------------------------------------------


async def test_mark_stuck_jobs_orphaned_flips_in_flight_only(tmp_path: Path) -> None:
    """assigned/running → orphaned; pending, orphaned, and terminals untouched.

    ``pending`` is deliberately left alone (it wasn't in-flight, just
    queued — the scheduler picks it up on the next tick). ``orphaned``
    rows from before the restart also stay as-is; the sweeper's grace
    window is the only path that flips them further.
    """

    import time as _time

    db = await open_database(tmp_path / "sweep.sqlite")
    try:
        rows = [
            ("pj", "pending"),
            ("aj", "assigned"),
            ("rj", "running"),
            ("oj", "orphaned"),
            ("dj", "done"),
            ("fj", "failed"),
            ("cj", "cancelled"),
            ("ij", "interrupted"),
        ]

        async def _seed(conn) -> None:
            for jid, state in rows:
                await conn.execute(
                    "INSERT INTO jobs (job_id, workflow_id, algorithm_name, algorithm_version, "
                    "params_json, input_handles_json, state, created_ts, updated_ts) "
                    "VALUES (?, 'w', 'demo-echo', '0.1.0', '{}', '{}', ?, ?, ?)",
                    (jid, state, _time.time(), _time.time()),
                )

        await db.write(_seed)

        n = await mark_stuck_jobs_orphaned(db)
        # assigned + running flip; pending/orphaned/terminals untouched.
        assert n == 2

        async with (
            db.read() as conn,
            conn.execute("SELECT job_id, state FROM jobs ORDER BY job_id") as cur,
        ):
            after = {jid: state for jid, state in await cur.fetchall()}

        assert after == {
            "aj": "orphaned",
            "cj": "cancelled",
            "dj": "done",
            "fj": "failed",
            "ij": "interrupted",
            "oj": "orphaned",
            "pj": "pending",  # <-- deliberately preserved
            "rj": "orphaned",
        }
    finally:
        await db.close()


async def test_mark_stuck_jobs_orphaned_noop_on_clean_db(tmp_path: Path) -> None:
    db = await open_database(tmp_path / "clean.sqlite")
    try:
        assert await mark_stuck_jobs_orphaned(db) == 0
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# downstream_closure
# ---------------------------------------------------------------------------


def _linear_graph() -> WorkflowGraph:
    """single-video-source → video-to-colmap → stg-train → stg-to-splatv."""

    return WorkflowGraph(
        nodes=[
            GraphNode(
                id="n1",
                algorithm_name="single-video-source",
                algorithm_version="0.1.0",
                assigned_node_id="node-a",
            ),
            GraphNode(
                id="n2",
                algorithm_name="video-to-colmap",
                algorithm_version="0.1.0",
                assigned_node_id="node-a",
            ),
            GraphNode(
                id="n3",
                algorithm_name="stg-train",
                algorithm_version="0.1.0",
                assigned_node_id="node-a",
            ),
            GraphNode(
                id="n4",
                algorithm_name="stg-to-splatv",
                algorithm_version="0.1.0",
                assigned_node_id="node-a",
            ),
        ],
        edges=[
            GraphEdge(id="e1", source="n1", sourceHandle="out", target="n2", targetHandle="in"),
            GraphEdge(id="e2", source="n2", sourceHandle="out", target="n3", targetHandle="in"),
            GraphEdge(id="e3", source="n3", sourceHandle="out", target="n4", targetHandle="in"),
        ],
    )


def test_downstream_closure_linear() -> None:
    graph = _linear_graph()
    assert downstream_closure(graph, "n1") == {"n1", "n2", "n3", "n4"}
    assert downstream_closure(graph, "n2") == {"n2", "n3", "n4"}
    assert downstream_closure(graph, "n3") == {"n3", "n4"}
    assert downstream_closure(graph, "n4") == {"n4"}
    assert downstream_closure(graph, "unknown") == set()


# ---------------------------------------------------------------------------
# /api/snapshots/{sid}/rerun-from/{gid} endpoint
# ---------------------------------------------------------------------------


def _seed_run(
    client: TestClient,
    *,
    workflow_id: str,
    graph: WorkflowGraph,
    job_states: dict[str, str],
) -> str:
    """Insert a snapshot + a Job per graph node with the given state map.

    Returns the new snapshot_id. All jobs are attached to compute node
    ``node-a``, which the test fakes as online below.
    """

    async def _seed() -> str:
        await client.app.state.workflows.save_draft(
            workflow_id=workflow_id, name="cc-e2e-rerun", graph=graph
        )
        snap = await client.app.state.workflows.create_snapshot(
            workflow_id=workflow_id, graph=graph
        )
        for gn in graph.nodes:
            state = job_states.get(gn.id, "done")
            job = Job(
                job_id=f"{snap.snapshot_id[:8]}-{gn.id}",
                snapshot_id=snap.snapshot_id,
                workflow_id=workflow_id,
                node_id="node-a",
                graph_node_id=gn.id,
                algorithm_name=gn.algorithm_name,
                algorithm_version=gn.algorithm_version,
                params=dict(gn.params),
                input_handles={},
                state=JobState(state),
            )
            await client.app.state.jobs_store.create(job)
            if state == "done":
                await client.app.state.handles.register(
                    Handle(
                        handle_id=f"h-{gn.id}",
                        node_id="node-a",
                        storage="file",
                        tags=[gn.algorithm_name],
                        path=f"/tmp/{gn.id}.out",
                        job_id=job.job_id,
                        output_port_name="out",
                    )
                )
        return snap.snapshot_id

    return client.portal.call(_seed)


def _fake_online_node(client: TestClient) -> None:
    """Register a MagicMock session so the endpoint sees ``node-a`` as online."""

    session = NodeSession(
        node_id="node-a",
        session_id="sess",
        node_name="node-a-name",
        ws=MagicMock(),
        advertised_url=None,
        gpu=GpuInfo(),
        packs=[],
        protocol_v=1,
    )
    client.app.state.registry._sessions[session.node_id] = session


def test_rerun_from_node_reuses_upstream_creates_new_snapshot(tmp_path: Path) -> None:
    """Happy path: rerun from n3 → n1+n2 attributed to the new snapshot
    via the V8 snapshot_jobs bridge, referencing the ORIGINAL producing
    jobs directly (no synthetic reused_from bookkeeping rows).

    Pre-V8 the endpoint used to synthesise a stand-in job row with
    ``reused_from_job_id`` pointing at the origin. Under the lineage-
    first model the bridge captures the sharing directly, so the run
    view shows the origin jobs verbatim — real state, real timestamps,
    real handle attribution. That's more truthful and lets a job
    belong to multiple snapshots without duplication."""

    app = create_app(db_path=tmp_path / "rr.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        old_snap = _seed_run(
            client,
            workflow_id="11111111-1111-1111-1111-111111111111",
            graph=_linear_graph(),
            job_states={"n1": "done", "n2": "done", "n3": "done", "n4": "done"},
        )

        r = client.post(f"/api/snapshots/{old_snap}/rerun-from/n3")
        assert r.status_code == 200
        body = r.json()
        assert body["original_snapshot_id"] == old_snap
        assert body["new_snapshot_id"] != old_snap
        # n3 + n4 to re-run, n1 + n2 reused.
        assert body["rerun_graph_node_ids"] == ["n3", "n4"]
        assert body["reused_graph_node_ids"] == ["n1", "n2"]
        # reused_job_ids point at the original snapshot's jobs.
        assert len(body["reused_job_ids"]) == 2
        assert all(jid.startswith(old_snap[:8]) for jid in body["reused_job_ids"])

        # The new snapshot's detail shows n1 + n2 directly attributed
        # to their origin jobs (no synthetic reused rows). Under V8
        # the ``reused_from_job_id`` field on these rows IS None (the
        # origin job never had a reused_from pointer of its own), yet
        # the origin's ``job_id`` matches what the endpoint reported.
        detail = client.get(f"/api/snapshots/{body['new_snapshot_id']}").json()
        by_gnode = {j["graph_node_id"]: j for j in detail["jobs"]}
        assert by_gnode["n1"]["state"] == "done"
        assert by_gnode["n1"]["job_id"] == body["reused_job_ids"][0]
        assert by_gnode["n1"]["output_handles"] == {"out": "h-n1"}
        assert by_gnode["n2"]["state"] == "done"
        assert by_gnode["n2"]["job_id"] == body["reused_job_ids"][1]
        assert by_gnode["n2"]["output_handles"] == {"out": "h-n2"}


def test_rerun_from_node_404_unknown_snapshot(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "r404s.sqlite")
    with TestClient(app) as client:
        r = client.post("/api/snapshots/nope/rerun-from/anything")
        assert r.status_code == 404


def test_rerun_from_node_404_unknown_graph_node(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "r404g.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        snap = _seed_run(
            client,
            workflow_id="22222222-2222-2222-2222-222222222222",
            graph=_linear_graph(),
            job_states={"n1": "done", "n2": "done", "n3": "done", "n4": "done"},
        )
        r = client.post(f"/api/snapshots/{snap}/rerun-from/nope")
        assert r.status_code == 404
        assert "not in this snapshot" in r.json()["detail"]


def test_rerun_from_node_400_reused_upstream_not_done(tmp_path: Path) -> None:
    """If a to_reuse node isn't done, the endpoint rejects — the user
    can either fix the upstream first or re-run from an earlier node."""

    app = create_app(db_path=tmp_path / "rbad.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        snap = _seed_run(
            client,
            workflow_id="33333333-3333-3333-3333-333333333333",
            graph=_linear_graph(),
            job_states={"n1": "failed", "n2": "done", "n3": "done", "n4": "done"},
        )
        # rerun from n3 wants to reuse n1 (which is failed) → 400.
        r = client.post(f"/api/snapshots/{snap}/rerun-from/n3")
        assert r.status_code == 400
        assert "cannot reuse" in r.json()["detail"]
        assert "n1" in r.json()["detail"]


def test_rerun_from_node_400_producer_offline(tmp_path: Path) -> None:
    """If the producer of a reused handle is offline, /proxy can't
    serve its bytes — reject with a clear message rather than a failed
    downstream run."""

    app = create_app(db_path=tmp_path / "roff.sqlite")
    with TestClient(app) as client:
        # Don't fake node-a as online — it stays offline.
        snap = _seed_run(
            client,
            workflow_id="44444444-4444-4444-4444-444444444444",
            graph=_linear_graph(),
            job_states={"n1": "done", "n2": "done", "n3": "done", "n4": "done"},
        )
        r = client.post(f"/api/snapshots/{snap}/rerun-from/n3")
        assert r.status_code == 400
        assert "not currently online" in r.json()["detail"]


def test_rerun_from_a_previous_rerun_resolves_origin_handles(tmp_path: Path) -> None:
    """Re-running a snapshot whose upstream was ITSELF the product of a
    rerun-from used to abort with
    ``upstream job produced no output for port 'out'`` because the
    reused row has no handles registered under its own job_id — the
    handles live on the origin job. The fix walks
    ``reused_from_job_id`` back to the origin. Regression coverage.
    """

    app = create_app(db_path=tmp_path / "chain.sqlite")
    graph = _linear_graph()
    with TestClient(app) as client:
        _fake_online_node(client)
        old_snap = _seed_run(
            client,
            workflow_id="55555555-5555-5555-5555-555555555555",
            graph=graph,
            job_states={"n1": "done", "n2": "done", "n3": "done", "n4": "done"},
        )

        # Manually seed snap2 as if a rerun-from-n3 already ran to
        # completion. n1/n2 are reused rows pointing at old_snap; n3/n4
        # are freshly-executed done jobs with their own handles. This
        # matches exactly what the executor would leave behind after a
        # successful first rerun; we bypass the actual /rerun-from POST
        # here because the TestClient's mocked node can't complete the
        # background dispatch.
        async def _build_snap2() -> str:
            wf_id = "55555555-5555-5555-5555-555555555555"
            snap = await client.app.state.workflows.create_snapshot(workflow_id=wf_id, graph=graph)
            # Reused rows for n1/n2 — point at the original snap1 jobs.
            for gid in ("n1", "n2"):
                await client.app.state.jobs_store.create(
                    Job(
                        job_id=f"snap2-reused-{gid}",
                        snapshot_id=snap.snapshot_id,
                        workflow_id=wf_id,
                        node_id="node-a",
                        graph_node_id=gid,
                        algorithm_name={"n1": "single-video-source", "n2": "video-to-colmap"}[gid],
                        algorithm_version="0.1.0",
                        params={},
                        input_handles={},
                        state=JobState.DONE,
                        reused_from_job_id=f"{old_snap[:8]}-{gid}",
                    )
                )
            # Fresh-executed rows for n3/n4 with their own handles.
            for gid in ("n3", "n4"):
                jid = f"snap2-fresh-{gid}"
                await client.app.state.jobs_store.create(
                    Job(
                        job_id=jid,
                        snapshot_id=snap.snapshot_id,
                        workflow_id=wf_id,
                        node_id="node-a",
                        graph_node_id=gid,
                        algorithm_name={"n3": "stg-train", "n4": "stg-to-splatv"}[gid],
                        algorithm_version="0.1.0",
                        params={},
                        input_handles={},
                        state=JobState.DONE,
                    )
                )
                await client.app.state.handles.register(
                    Handle(
                        handle_id=f"h2-{gid}",
                        node_id="node-a",
                        storage="file",
                        tags=["snap2"],
                        path=f"/tmp/{gid}-snap2.out",
                        job_id=jid,
                        output_port_name="out",
                    )
                )
            return snap.snapshot_id

        snap2 = client.portal.call(_build_snap2)

        # Second rerun: from n4 in snap2. This must reuse snap2's n1/n2/n3
        # rows. n1 and n2 in snap2 are themselves REUSED rows — their
        # own job_id has no handles; the handles live on the original
        # snap1 jobs. Without the fix this used to raise
        # ``upstream job produced no output for port 'out'``.
        second = client.post(f"/api/snapshots/{snap2}/rerun-from/n4")
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["reused_graph_node_ids"] == ["n1", "n2", "n3"]

        # Under V8, no synthetic bookkeeping rows are created at all —
        # the new snapshot ``snap3`` attributes n1/n2/n3 directly to
        # their ORIGINAL producing jobs via ``snapshot_jobs``. So the
        # row returned by ``list_by_snapshot`` for gid=n1 IS the origin
        # snap1 job (with ``reused_from_job_id`` = None, since the
        # origin never had a reused pointer of its own).
        snap3 = body["new_snapshot_id"]
        detail = client.get(f"/api/snapshots/{snap3}").json()
        by_gnode = {j["graph_node_id"]: j for j in detail["jobs"]}
        for gid in ("n1", "n2"):
            row = by_gnode[gid]
            # Direct attribution to snap1's original job — prefixed with old_snap[:8].
            assert row["job_id"].startswith(old_snap[:8]), (
                f"attribution not flattened to origin: job_id={row['job_id']} for gid={gid}"
            )
            assert row["state"] == "done"
        # And n1/n2 output_handles resolve to snap1's registered handles.
        assert by_gnode["n1"]["output_handles"] == {"out": "h-n1"}
        assert by_gnode["n2"]["output_handles"] == {"out": "h-n2"}


def test_rerun_from_prefers_fanout_parent_over_shard(tmp_path: Path) -> None:
    """When a to_reuse gnode has both a parent (aggregate) and shards
    attributed, rerun-from must pick the PARENT — otherwise downstream
    inherits a single-slice handle and the fan-out either finds the
    wrong tree or degrades to a 0-shard scalar no-op.

    Repro: n2 is an arrayable node whose parent produces the aggregate
    handle ``h-n2-parent`` (path=.../out) and whose 2 shards each
    produce a per-element slice (path=.../out/frame_0000 and
    .../out/frame_0001). Shards are created AFTER the parent — a naive
    newest-by-created_ts selection lands on a shard. The rerun's new
    snapshot must attribute n2 to the parent and inherit ``h-n2-parent``
    (aggregate), not ``h-n2-s1`` (last slice).
    """

    app = create_app(db_path=tmp_path / "rr-fanout.sqlite")
    graph = _linear_graph()
    with TestClient(app) as client:
        _fake_online_node(client)
        wf_id = "88888888-8888-8888-8888-888888888888"

        async def _seed() -> str:
            await client.app.state.workflows.save_draft(
                workflow_id=wf_id, name="cc-e2e-fanout", graph=graph
            )
            snap = await client.app.state.workflows.create_snapshot(workflow_id=wf_id, graph=graph)
            # n1 — a scalar upstream, needed so rerun-from-n3 has an
            # in-graph to_reuse set that includes both n1 and n2.
            n1_job = Job(
                job_id=f"{snap.snapshot_id[:8]}-n1",
                snapshot_id=snap.snapshot_id,
                workflow_id=wf_id,
                node_id="node-a",
                graph_node_id="n1",
                algorithm_name="single-video-source",
                algorithm_version="0.1.0",
                params={},
                input_handles={},
                state=JobState.DONE,
            )
            await client.app.state.jobs_store.create(n1_job)
            await client.app.state.handles.register(
                Handle(
                    handle_id="h-n1",
                    node_id="node-a",
                    storage="file",
                    tags=["single-video-source"],
                    path="/tmp/n1.out",
                    job_id=n1_job.job_id,
                    output_port_name="out",
                )
            )

            # n2 — fanout parent + 2 shards. Parent registered FIRST
            # (older created_ts); shards later. Newest-by-created_ts
            # would pick a shard.
            parent = Job(
                job_id="n2-parent",
                snapshot_id=snap.snapshot_id,
                workflow_id=wf_id,
                node_id="node-a",
                graph_node_id="n2",
                algorithm_name="video-to-colmap",
                algorithm_version="0.1.0",
                params={},
                input_handles={},
                state=JobState.DONE,
                created_ts=1000.0,
                updated_ts=1000.0,
                expected_shards=2,
            )
            await client.app.state.jobs_store.create(parent)
            await client.app.state.handles.register(
                Handle(
                    handle_id="h-n2-parent",
                    node_id="node-a",
                    storage="dir",
                    tags=["video-to-colmap"],
                    path="/tmp/n2/out",
                    job_id=parent.job_id,
                    output_port_name="out",
                )
            )
            for idx in range(2):
                shard = Job(
                    job_id=f"n2-s{idx}",
                    snapshot_id=snap.snapshot_id,
                    workflow_id=wf_id,
                    node_id="node-a",
                    graph_node_id="n2",
                    algorithm_name="video-to-colmap",
                    algorithm_version="0.1.0",
                    params={},
                    input_handles={},
                    state=JobState.DONE,
                    parent_job_id=parent.job_id,
                    shard_element_id=f"frame_{idx:04d}",
                    created_ts=2000.0 + idx,
                    updated_ts=2000.0 + idx,
                )
                await client.app.state.jobs_store.create(shard)
                await client.app.state.handles.register(
                    Handle(
                        handle_id=f"h-n2-s{idx}",
                        node_id="node-a",
                        storage="dir",
                        tags=["video-to-colmap"],
                        path=f"/tmp/n2/out/frame_{idx:04d}",
                        job_id=shard.job_id,
                        output_port_name="out",
                    )
                )

            # n3, n4 — scalars so downstream_closure(n3) = {n3, n4}
            # and to_reuse = {n1, n2}.
            for gid, algo in (("n3", "stg-train"), ("n4", "stg-to-splatv")):
                job = Job(
                    job_id=f"{snap.snapshot_id[:8]}-{gid}",
                    snapshot_id=snap.snapshot_id,
                    workflow_id=wf_id,
                    node_id="node-a",
                    graph_node_id=gid,
                    algorithm_name=algo,
                    algorithm_version="0.1.0",
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
                        tags=[algo],
                        path=f"/tmp/{gid}.out",
                        job_id=job.job_id,
                        output_port_name="out",
                    )
                )
            return snap.snapshot_id

        old_snap = client.portal.call(_seed)

        r = client.post(f"/api/snapshots/{old_snap}/rerun-from/n3")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["reused_graph_node_ids"] == ["n1", "n2"]
        # The reused_job_ids for n2 must be the parent, NOT any shard.
        assert "n2-parent" in body["reused_job_ids"], body["reused_job_ids"]
        assert not any(jid.startswith("n2-s") for jid in body["reused_job_ids"]), body[
            "reused_job_ids"
        ]

        # And the new snapshot's n2 attribution + output handle resolves
        # to the aggregate (h-n2-parent), not a slice.
        detail = client.get(f"/api/snapshots/{body['new_snapshot_id']}").json()
        by_gnode = {j["graph_node_id"]: j for j in detail["jobs"]}
        assert by_gnode["n2"]["job_id"] == "n2-parent"
        assert by_gnode["n2"]["output_handles"] == {"out": "h-n2-parent"}


async def test_resolve_origin_job_id_hops_shard_to_parent(tmp_path: Path) -> None:
    """A rerun-from that picks a SHARD row (either because the caller
    dropped a shard in from ``old_jobs_by_gnode`` or because the source
    snapshot's bridge attributed only shards) must still resolve to
    the parent's aggregate handle downstream — otherwise the reused
    output map lands on a per-element slice and fan-out collapses.

    ``_resolve_origin_job_id`` now hops shard → parent up front, then
    walks the usual ``reused_from_job_id`` chain from the parent. This
    heals corrupt snapshots (pre-fix rerun-from selections that
    attributed only shards) retroactively — no data migration.
    """

    from hololab.gateway.execution import _resolve_origin_job_id
    from hololab.gateway.jobs import Job, JobState
    from hololab.gateway.registry import JobsStore

    db = await open_database(tmp_path / "resolve.sqlite")
    try:
        store = JobsStore(db)
        parent = Job(
            job_id="p",
            snapshot_id="s",
            workflow_id="w",
            algorithm_name="demo-echo",
            algorithm_version="0.1.0",
            state=JobState.DONE,
            created_ts=1.0,
            updated_ts=1.0,
        )
        shard = Job(
            job_id="sh",
            snapshot_id="s",
            workflow_id="w",
            algorithm_name="demo-echo",
            algorithm_version="0.1.0",
            state=JobState.DONE,
            parent_job_id="p",
            shard_element_id="frame_0000",
            created_ts=2.0,
            updated_ts=2.0,
        )
        await store.create(parent)
        await store.create(shard)

        got = await _resolve_origin_job_id(
            store,
            {
                "job_id": shard.job_id,
                "reused_from_job_id": None,
                "parent_job_id": shard.parent_job_id,
            },
        )
        assert got == "p", f"expected shard→parent hop, got {got!r}"

        # Scalar (no parent) still returns its own id.
        got_scalar = await _resolve_origin_job_id(
            store,
            {"job_id": "p", "reused_from_job_id": None, "parent_job_id": None},
        )
        assert got_scalar == "p"
    finally:
        await db.close()


async def test_get_job_at_falls_back_to_shard_parent(tmp_path: Path) -> None:
    """When only shard rows are attributed (a corrupt snapshot left by
    pre-fix rerun-from), ``get_job_at`` must fall back to the shard's
    ``parent_job_id`` so downstream input resolution still finds the
    aggregate handle. Heals existing broken snapshots retroactively
    without a data migration.
    """

    from hololab.gateway.jobs import Job, JobState
    from hololab.gateway.registry import JobsStore, SnapshotJobsStore
    from hololab.gateway.workflows import WorkflowStore

    db = await open_database(tmp_path / "gja.sqlite")
    try:
        store = JobsStore(db)
        bridge = SnapshotJobsStore(db)
        workflows = WorkflowStore(db)
        graph = _linear_graph()
        wf_id = "99999999-9999-9999-9999-999999999999"
        await workflows.save_draft(workflow_id=wf_id, name="gja", graph=graph)
        snap = await workflows.create_snapshot(workflow_id=wf_id, graph=graph)

        # Seed parent + shard, both DONE — but only attribute the SHARD.
        # The parent job row still exists in the ``jobs`` table (that's
        # the point: it's the corrupt-attribution scenario, not a
        # missing-job scenario).
        parent = Job(
            job_id="p",
            snapshot_id=snap.snapshot_id,
            workflow_id=wf_id,
            node_id="node-a",
            graph_node_id="n1",
            algorithm_name="single-video-source",
            algorithm_version="0.1.0",
            params={},
            input_handles={},
            state=JobState.DONE,
            created_ts=1.0,
            updated_ts=1.0,
        )
        shard = Job(
            job_id="s",
            snapshot_id=snap.snapshot_id,
            workflow_id=wf_id,
            node_id="node-a",
            graph_node_id="n1",
            algorithm_name="single-video-source",
            algorithm_version="0.1.0",
            params={},
            input_handles={},
            state=JobState.DONE,
            parent_job_id="p",
            shard_element_id="frame_0000",
            created_ts=2.0,
            updated_ts=2.0,
        )
        await store.create(parent)
        # ``JobsStore.create`` auto-attributes DONE rows; drop the parent's
        # attribution to simulate the pre-fix corruption.
        await db.write(
            lambda conn: conn.execute(
                "DELETE FROM snapshot_jobs WHERE job_id=? AND snapshot_id=?",
                (parent.job_id, snap.snapshot_id),
            )
        )
        await store.create(shard)

        got = await bridge.get_job_at(snap.snapshot_id, "n1")
        assert got == "p", f"expected fallback to parent, got {got!r}"
    finally:
        await db.close()
