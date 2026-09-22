"""REST coverage for the agent-shaped API surface.

Covers:
    GET /api/overview                      — one-call system snapshot
    GET /api/jobs?workflow_id=&state=…     — filtered listing
    GET /api/snapshots/{id}?wait_for_state — long-poll variant
    GET /api/jobs/{id}/log                 — persisted stdout/stderr tail
    GET /api/handles/{id}/summary          — server-parsed metadata
    GET /api/workflows/{id}                — agent_graph_dict on the graph
    GET /api/snapshots/{id}                — agent_graph_dict on the graph

These are the additions that let an agent complete "list workflows →
find one → run it → wait for done → read the output metadata" in ≤6
REST calls without a client-side polling loop.
"""

from __future__ import annotations

import asyncio
import struct
import time
from pathlib import Path

from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.handles import Handle
from hololab.gateway.jobs import Job, JobState
from hololab.gateway.workflows import (
    GraphEdge,
    GraphNode,
    WorkflowGraph,
    _mermaid_labels,
    _topology_text,
)

# ---------------------------------------------------------------------------
# Seeding helpers — same shape as the run-history tests
# ---------------------------------------------------------------------------


def _linear_graph(iter_value: int = 100) -> WorkflowGraph:
    return WorkflowGraph(
        nodes=[
            GraphNode(
                id="n1",
                algorithm_name="single-video-source",
                algorithm_version="0.1.0",
                params={"iterations": iter_value},
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
        ],
        edges=[
            GraphEdge(
                id="e1",
                source="n1",
                sourceHandle="videos_dir",
                target="n2",
                targetHandle="videos_dir",
            ),
            GraphEdge(
                id="e2", source="n2", sourceHandle="colmap", target="n3", targetHandle="colmap"
            ),
        ],
    )


def _seed_run(
    client: TestClient,
    *,
    workflow_id: str,
    graph: WorkflowGraph,
    job_states: list[str],
) -> str:
    async def _seed() -> str:
        draft = await client.app.state.workflows.get_draft(workflow_id)
        if draft is None:
            await client.app.state.workflows.save_draft(
                workflow_id=workflow_id, name="cc-e2e-agent", graph=graph
            )
        snap = await client.app.state.workflows.create_snapshot(
            workflow_id=workflow_id, graph=graph
        )
        for i, state in enumerate(job_states):
            gn = graph.nodes[i % len(graph.nodes)]
            job = Job(
                job_id=f"{snap.snapshot_id[:8]}-j{i}",
                snapshot_id=snap.snapshot_id,
                workflow_id=workflow_id,
                node_id="node-a",
                graph_node_id=gn.id,
                algorithm_name=gn.algorithm_name,
                algorithm_version=gn.algorithm_version,
                params=gn.params,
                input_handles={},
                state=JobState(state),
            )
            await client.app.state.jobs_store.create(job)
        return snap.snapshot_id

    return client.portal.call(_seed)


# ---------------------------------------------------------------------------
# /api/overview
# ---------------------------------------------------------------------------


def test_overview_answers_system_state_in_one_call(tmp_path: Path) -> None:
    """One GET returns nodes + workflow count-by-state + job counts."""

    app = create_app(db_path=tmp_path / "ov.sqlite")
    with TestClient(app) as client:
        _seed_run(
            client,
            workflow_id="wf-done",
            graph=_linear_graph(10),
            job_states=["done", "done", "done"],
        )
        _seed_run(
            client,
            workflow_id="wf-fail",
            graph=_linear_graph(20),
            job_states=["done", "failed", "pending"],
        )

        r = client.get("/api/overview")
        assert r.status_code == 200
        body = r.json()

        assert body["version"]
        assert isinstance(body["ts"], (int, float))
        # No real nodes are connected in this TestClient — that's expected;
        # the field is present and typed.
        assert body["nodes"] == []

        # Workflow rollup: two drafts, one done, one failed.
        assert body["workflows"]["total"] == 2
        assert body["workflows"]["by_last_run_state"]["done"] == 1
        assert body["workflows"]["by_last_run_state"]["failed"] == 1

        # Job counts across recent — 3 done + 1 failed + 1 pending = 5,
        # but recent only pulls newest so counts might exclude older items.
        # The important invariant: counts_by_state's sum equals recent length.
        counts = body["jobs"]["counts_by_state"]
        assert sum(counts.values()) == len(body["jobs"]["recent"])
        assert counts.get("done", 0) >= 1
        assert counts.get("failed", 0) == 1


# ---------------------------------------------------------------------------
# /api/jobs — filtering
# ---------------------------------------------------------------------------


def test_jobs_filter_by_workflow_and_state(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "jobs.sqlite")
    with TestClient(app) as client:
        _seed_run(client, workflow_id="wf-a", graph=_linear_graph(), job_states=["done", "done"])
        _seed_run(client, workflow_id="wf-b", graph=_linear_graph(), job_states=["done", "failed"])

        # Filter by workflow only.
        r = client.get("/api/jobs?workflow_id=wf-b").json()
        assert len(r) == 2
        assert all(j["workflow_id"] == "wf-b" for j in r)

        # Combine workflow + state.
        r = client.get("/api/jobs?workflow_id=wf-b&state=failed").json()
        assert len(r) == 1
        assert r[0]["state"] == "failed"

        # order=asc flips chronology.
        rows_desc = client.get("/api/jobs?workflow_id=wf-a").json()
        rows_asc = client.get("/api/jobs?workflow_id=wf-a&order=asc").json()
        assert [j["job_id"] for j in rows_desc] == list(reversed([j["job_id"] for j in rows_asc]))


# ---------------------------------------------------------------------------
# /api/snapshots/{id}?wait_for_state=
# ---------------------------------------------------------------------------


def test_snapshot_wait_for_done_returns_immediately_when_already_done(
    tmp_path: Path,
) -> None:
    """No-poll happy path: run already done → returns in one round-trip."""

    app = create_app(db_path=tmp_path / "wait.sqlite")
    with TestClient(app) as client:
        snap_id = _seed_run(
            client, workflow_id="wf-x", graph=_linear_graph(), job_states=["done", "done", "done"]
        )
        started = time.time()
        body = client.get(f"/api/snapshots/{snap_id}?wait_for_state=done&timeout=5").json()
        elapsed = time.time() - started
        assert elapsed < 1.5  # no meaningful wait
        assert body["waited"] is True
        assert body["wait_timed_out"] is False
        assert all(j["state"] == "done" for j in body["jobs"])


def test_snapshot_wait_for_state_times_out_cleanly(tmp_path: Path) -> None:
    """Not-yet-done run + short timeout → returns wait_timed_out=True with current state."""

    app = create_app(db_path=tmp_path / "waittimeout.sqlite")
    with TestClient(app) as client:
        snap_id = _seed_run(
            client, workflow_id="wf-y", graph=_linear_graph(), job_states=["done", "running"]
        )
        body = client.get(f"/api/snapshots/{snap_id}?wait_for_state=done&timeout=1").json()
        assert body["waited"] is True
        assert body["wait_timed_out"] is True
        # Payload still returns the current state so the agent can decide.
        states = {j["state"] for j in body["jobs"]}
        assert "running" in states


def test_snapshot_wait_for_done_does_not_match_partial_dispatch(tmp_path: Path) -> None:
    """Regression: executor dispatches jobs one at a time in topological
    order. When only the first job exists and is done, the whole DAG is
    NOT done — the wait must keep polling (or time out), not return
    immediately with a misleading success.
    """

    app = create_app(db_path=tmp_path / "waitpartial.sqlite")
    with TestClient(app) as client:
        # 3-node graph but only 1 job seeded → partial dispatch.
        snap_id = _seed_run(
            client, workflow_id="wf-partial", graph=_linear_graph(), job_states=["done"]
        )
        body = client.get(f"/api/snapshots/{snap_id}?wait_for_state=done&timeout=1").json()
        assert body["waited"] is True
        # Should have timed out — the run isn't actually complete.
        assert body["wait_timed_out"] is True
        assert len(body["jobs"]) == 1  # nothing else came in during the 1s window


def test_snapshot_wait_for_failed_matches_immediately_on_any_failure(
    tmp_path: Path,
) -> None:
    """failed short-circuits — matches as soon as ANY job fails,
    even if other nodes haven't dispatched yet."""

    app = create_app(db_path=tmp_path / "waitfailed.sqlite")
    with TestClient(app) as client:
        snap_id = _seed_run(
            client, workflow_id="wf-early-fail", graph=_linear_graph(), job_states=["failed"]
        )
        started = time.time()
        body = client.get(f"/api/snapshots/{snap_id}?wait_for_state=failed&timeout=5").json()
        assert time.time() - started < 1.5  # no waiting
        assert body["wait_timed_out"] is False
        assert any(j["state"] == "failed" for j in body["jobs"])


def test_snapshot_wait_rejects_unknown_state(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "waitbad.sqlite")
    with TestClient(app) as client:
        snap_id = _seed_run(client, workflow_id="wf-z", graph=_linear_graph(), job_states=["done"])
        r = client.get(f"/api/snapshots/{snap_id}?wait_for_state=lolwut&timeout=0.5")
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Agent-shaped graph on /api/workflows/{id} and /api/snapshots/{id}
# ---------------------------------------------------------------------------


def test_workflow_detail_graph_carries_topology_text_and_labels(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "gd.sqlite")
    with TestClient(app) as client:

        async def _seed() -> None:
            await client.app.state.workflows.save_draft(
                workflow_id="wf-graph", name="cc-e2e-agent", graph=_linear_graph()
            )

        client.portal.call(_seed)

        body = client.get("/api/workflows/wf-graph").json()
        g = body["graph"]

        assert g["is_dag"] is True
        # topological order: n1, n2, n3.
        assert [n["id"] for n in g["nodes"]] == ["n1", "n2", "n3"]
        # edges are denormalized with algorithm names.
        first_edge = g["edges"][0]
        assert first_edge["source_label"] == "single-video-source"
        assert first_edge["target_label"] == "video-to-colmap"
        # topology_text is a compact chain — contains all three algorithm names.
        text = g["topology_text"]
        assert "single-video-source" in text
        assert "video-to-colmap" in text
        assert "stg-train" in text
        assert "→" in text  # arrow character used as separator


def test_workflow_get_then_post_roundtrips_verbatim(tmp_path: Path) -> None:
    """Regression: the agent-friendly extras on the GET response
    (source_label / target_label on edges, is_dag / topology_text on the
    graph) must not make the body unusable as a POST input.

    The natural agent flow is ``GET workflow → mutate → POST back``.
    Before this fix the four denormalised fields were extras that
    pydantic's ``extra=forbid`` rejected with ``extra_forbidden`` — the
    caller had to hand-strip them before writing back.
    """

    app = create_app(db_path=tmp_path / "rt.sqlite")
    with TestClient(app) as client:

        async def _seed() -> None:
            await client.app.state.workflows.save_draft(
                workflow_id="wf-rt", name="cc-e2e-test", graph=_linear_graph()
            )

        client.portal.call(_seed)

        # 1) GET carries the denormalised fields.
        got = client.get("/api/workflows/wf-rt").json()
        assert "is_dag" in got["graph"]
        assert "topology_text" in got["graph"]
        assert "source_label" in got["graph"]["edges"][0]

        # 2) POST that same body back verbatim (omit workflow_id so a
        #    fresh row is minted — the point is that the graph body is
        #    accepted at all, not that the id is preserved).
        r = client.post(
            "/api/workflows",
            json={"name": "cc-e2e-test-copy", "graph": got["graph"]},
        )
        assert r.status_code == 200, r.text
        new_id = r.json()["workflow_id"]

        # 3) The reloaded graph is semantically equivalent — the on-disk
        #    canonical form drops the denormalised fields, and the next
        #    GET re-derives them, so a nested equality on the important
        #    fields holds.
        reloaded = client.get(f"/api/workflows/{new_id}").json()["graph"]
        assert [n["id"] for n in reloaded["nodes"]] == [n["id"] for n in got["graph"]["nodes"]]
        assert [
            (e["source"], e["sourceHandle"], e["target"], e["targetHandle"])
            for e in reloaded["edges"]
        ] == [
            (e["source"], e["sourceHandle"], e["target"], e["targetHandle"])
            for e in got["graph"]["edges"]
        ]

        # 4) Cleanup — don't leave the fixture workflow behind.
        client.delete(f"/api/workflows/{new_id}")


def test_workflow_post_still_rejects_typos(tmp_path: Path) -> None:
    """The roundtrip fix widens *specific* known-output fields, not
    ``extra="ignore"`` globally. A misspelled field must still 400 so
    users get a meaningful error instead of silent drops.
    """

    app = create_app(db_path=tmp_path / "typo.sqlite")
    with TestClient(app) as client:
        r = client.post(
            "/api/workflows",
            json={
                "name": "cc-e2e-test-typo",
                "graph": {
                    "nodes": [],
                    "edges": [],
                    "topology_txt": "typo — missing an 'e'",
                },
            },
        )
        assert r.status_code == 400
        assert "extra_forbidden" in r.text


def test_snapshot_detail_graph_also_agent_shaped(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "sgd.sqlite")
    with TestClient(app) as client:
        snap_id = _seed_run(
            client, workflow_id="wf-sgd", graph=_linear_graph(), job_states=["done", "done", "done"]
        )
        body = client.get(f"/api/snapshots/{snap_id}").json()
        assert body["graph"]["is_dag"] is True
        assert body["graph"]["topology_text"]


def test_topology_text_tolerates_dangling_edge_references() -> None:
    """Regression: _topology_text must not raise KeyError when an edge
    references a node that is no longer in graph.nodes.

    This happens when a node is deleted on the frontend but the DELETE
    payload doesn't cascade to the edges array (race, partial save, or
    legacy import).  The linear-chain walk previously did ``labels[cur]``
    with a bare dict access; once ``cur`` became a dangling target the
    lookup crashed with KeyError.  topological_order already silently drops
    dangling edges (line 414 guard); _topology_text must match that behaviour.
    """

    # Three nodes: A → B → (ghost), where 'ghost' is the deleted node that
    # still appears as an edge target.
    nodes = [
        GraphNode(id="A", algorithm_name="source-pack", algorithm_version="0.1.0"),
        GraphNode(id="B", algorithm_name="mid-pack", algorithm_version="0.1.0"),
    ]
    edges = [
        GraphEdge(id="e1", source="A", sourceHandle="out", target="B", targetHandle="in"),
        GraphEdge(id="e2", source="B", sourceHandle="out", target="ghost", targetHandle="in"),
    ]
    graph = WorkflowGraph(nodes=nodes, edges=edges)
    labels = _mermaid_labels(graph)

    # Must not raise.
    text = _topology_text(graph, labels)

    # The two real nodes must appear in the output; the dangling target is
    # silently dropped so the agent gets a useful (if incomplete) topology.
    assert "source-pack" in text
    assert "mid-pack" in text
    assert "ghost" not in text


def test_agent_api_returns_200_for_workflow_with_dangling_edges(tmp_path: Path) -> None:
    """GET /api/workflows/{id} must return 200 even when graph.edges contains
    references to deleted nodes.  Before the fix this produced a 500 with
    KeyError: '<node-id>' from _topology_text's linear-chain walk.
    """

    dangling_graph = WorkflowGraph(
        nodes=[
            GraphNode(id="n1", algorithm_name="source-pack", algorithm_version="0.1.0"),
            GraphNode(id="n2", algorithm_name="mid-pack", algorithm_version="0.1.0"),
        ],
        edges=[
            GraphEdge(id="e1", source="n1", sourceHandle="out", target="n2", targetHandle="in"),
            # n3 was deleted; the edge pointing to it was not cleaned up.
            GraphEdge(
                id="e2", source="n2", sourceHandle="out", target="n3-deleted", targetHandle="in"
            ),
        ],
    )

    app = create_app(db_path=tmp_path / "dangling.sqlite")
    with TestClient(app) as client:

        async def _seed() -> None:
            await client.app.state.workflows.save_draft(
                workflow_id="wf-dangling", name="dangling-edge-repro", graph=dangling_graph
            )

        client.portal.call(_seed)

        r = client.get("/api/workflows/wf-dangling")
        assert r.status_code == 200, r.text
        g = r.json()["graph"]
        assert "source-pack" in g["topology_text"]
        assert "mid-pack" in g["topology_text"]


# ---------------------------------------------------------------------------
# /api/jobs/{id}/log — persisted stdout/stderr tail
# ---------------------------------------------------------------------------


def test_job_log_tail_stores_and_returns_lines(tmp_path: Path) -> None:
    """LogStore.append persists; the endpoint returns the tail."""

    app = create_app(db_path=tmp_path / "logs.sqlite")
    with TestClient(app) as client:
        snap_id = _seed_run(
            client, workflow_id="wf-log", graph=_linear_graph(), job_states=["done", "done", "done"]
        )
        job_id = f"{snap_id[:8]}-j0"

        async def _append() -> None:
            await client.app.state.logs.append(
                job_id, "stdout", [f"line {i}" for i in range(1, 51)]
            )
            await client.app.state.logs.append(
                job_id, "stderr", ["warn: something", "error: badness"]
            )

        client.portal.call(_append)

        # Tail 10 lines of stdout only.
        r = client.get(f"/api/jobs/{job_id}/log?tail=10&stream=stdout").json()
        assert r["job_id"] == job_id
        assert r["total_returned"] == 10
        assert r["truncated"] is True
        assert r["lines"][0]["line"] == "line 41"
        assert r["lines"][-1]["line"] == "line 50"
        assert all(entry["stream"] == "stdout" for entry in r["lines"])

        # Both interleaved in insertion order — stdout batch first, then stderr.
        r = client.get(f"/api/jobs/{job_id}/log?tail=200&stream=both").json()
        assert r["total_returned"] == 52
        assert r["truncated"] is False
        # Stderr batch was appended after stdout batch → appears at the end.
        assert r["lines"][-1]["line"] == "error: badness"
        assert r["lines"][-1]["stream"] == "stderr"


def test_job_log_tail_404_on_unknown_job(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "logs404.sqlite")
    with TestClient(app) as client:
        assert client.get("/api/jobs/nope/log").status_code == 404


# ---------------------------------------------------------------------------
# /api/handles/{id}/summary — server-side splatv / image / text / dir parse
# ---------------------------------------------------------------------------


def _write_fake_splatv(
    path: Path, texw: int = 4096, gaussians: int = 1000, cameras: int = 5
) -> int:
    """Emit a minimal but structurally valid splatv file.

    Layout: magic (uint16 LE = 0x674B) + 2 pad + json_len (uint32 LE) +
    JSON header + a small texture blob. The summary parser only looks at
    the JSON, so the texture bytes don't need meaningful content.
    """

    size_stride = 16 * gaussians  # stride-4 rgba per gaussian
    header = [
        {
            "type": "splat",
            "texwidth": texw,
            "size": size_stride,
            "cameras": [{"id": i} for i in range(cameras)],
        }
    ]
    import json as _json

    header_bytes = _json.dumps(header).encode("utf-8")
    with path.open("wb") as f:
        f.write(struct.pack("<H", 0x674B))  # magic
        f.write(b"\x00\x00")  # 2-byte pad
        f.write(struct.pack("<I", len(header_bytes)))  # json_len
        f.write(header_bytes)
        f.write(b"\x00" * 32)  # small texture stub
    return path.stat().st_size


def test_handle_summary_splatv(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "sum.sqlite")
    with TestClient(app) as client:
        splatv_path = tmp_path / "iteration_200.splatv"
        _write_fake_splatv(splatv_path, texw=4096, gaussians=1234, cameras=7)

        async def _seed() -> None:
            await client.app.state.handles.register(
                Handle(
                    handle_id="h-splatv",
                    node_id="node-a",
                    storage="file",
                    tags=["splatv"],
                    path=str(splatv_path),
                    size_bytes=splatv_path.stat().st_size,
                    output_port_name="splatv",
                )
            )

        client.portal.call(_seed)

        body = client.get("/api/handles/h-splatv/summary").json()
        assert body["kind"] == "splatv"
        f = body["fields"]
        assert f["magic_hex"] == "0x674b"
        assert f["texture_width"] == 4096
        assert f["gaussian_count"] == 1234
        assert f["camera_count"] == 7
        # size_bytes flows through from the Handle row.
        assert body["size_bytes"] == splatv_path.stat().st_size
        # proxy_url is populated for downstream streaming.
        assert body["proxy_url"].startswith("/proxy/node-a/")


def test_handle_summary_text(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "sumt.sqlite")
    with TestClient(app) as client:
        p = tmp_path / "log.txt"
        p.write_text("first\nsecond\nthird\n")

        async def _seed() -> None:
            await client.app.state.handles.register(
                Handle(
                    handle_id="h-txt",
                    node_id="node-a",
                    storage="file",
                    tags=["log"],
                    path=str(p),
                    size_bytes=p.stat().st_size,
                    output_port_name="log",
                )
            )

        client.portal.call(_seed)

        body = client.get("/api/handles/h-txt/summary").json()
        assert body["kind"] == "text"
        assert body["fields"]["first_lines"][:3] == ["first", "second", "third"]


def test_handle_summary_dir(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "sumd.sqlite")
    with TestClient(app) as client:
        d = tmp_path / "outdir"
        d.mkdir()
        (d / "message.txt").write_text("hello")
        (d / "cameras.json").write_text("{}")
        (d / "subdir").mkdir()

        async def _seed() -> None:
            await client.app.state.handles.register(
                Handle(
                    handle_id="h-dir",
                    node_id="node-a",
                    storage="dir",
                    tags=["colmap"],
                    path=str(d),
                    size_bytes=None,
                    output_port_name="out_dir",
                )
            )

        client.portal.call(_seed)

        body = client.get("/api/handles/h-dir/summary").json()
        assert body["kind"] == "dir"
        names = {e["name"] for e in body["fields"]["entries"]}
        assert names == {"message.txt", "cameras.json", "subdir"}
        assert body["fields"]["entry_count"] == 3


def test_handle_summary_arrayed_layout_exposes_children(tmp_path: Path) -> None:
    """arrayed<T> handles put element data one level down
    (``videos_dir/cam00/cam00.mp4``). The dir summary enriches each
    directory entry with its immediate children so viewers like
    ``video-grid`` can compose per-element URLs without a second
    round-trip per element.
    """

    app = create_app(db_path=tmp_path / "suma.sqlite")
    with TestClient(app) as client:
        d = tmp_path / "videos_dir"
        d.mkdir()
        for cam in ("cam00", "cam01", "cam02"):
            sub = d / cam
            sub.mkdir()
            (sub / f"{cam}.mp4").write_bytes(b"\x00" * 42)

        async def _seed() -> None:
            await client.app.state.handles.register(
                Handle(
                    handle_id="h-arr",
                    node_id="node-a",
                    storage="dir",
                    tags=["video-source"],
                    path=str(d),
                    size_bytes=None,
                    output_port_name="videos_dir",
                )
            )

        client.portal.call(_seed)

        body = client.get("/api/handles/h-arr/summary").json()
        assert body["kind"] == "dir"
        entries = {e["name"]: e for e in body["fields"]["entries"]}
        assert set(entries) == {"cam00", "cam01", "cam02"}
        for cam, entry in entries.items():
            assert entry["is_dir"] is True
            child_names = {c["name"] for c in entry["children"]}
            assert child_names == {f"{cam}.mp4"}
            [leaf] = entry["children"]
            assert leaf["is_dir"] is False
            assert leaf["size_bytes"] == 42


def test_handle_summary_frames_subdir_gets_second_level_drill(
    tmp_path: Path,
) -> None:
    """Legacy ``arrayed<image>`` wraps images in a ``frames/`` subdir
    (``<element>/frames/<image>``, pre flatten-migration
    ``frame-extraction`` / ``regroup-by-frame``). The dir summary drills
    one more level into any such subdir so ``NestedFrameSequencePreview``
    can pick thumbnails without an extra fetch per element. Kept as a
    read-side backward-compat contract even though new producers write
    files directly at the element root.
    """

    app = create_app(db_path=tmp_path / "sumf.sqlite")
    with TestClient(app) as client:
        d = tmp_path / "by_frame"
        d.mkdir()
        # Two element dirs, each with a ``frames/`` subdir holding
        # per-camera images (regroup-by-frame's transposed layout).
        for frame in ("frame_000000", "frame_000001"):
            f = d / frame / "frames"
            f.mkdir(parents=True)
            for cam in ("cam00", "cam01"):
                (f / f"{cam}.png").write_bytes(b"\x89PNG" + b"\x00" * 20)

        async def _seed() -> None:
            await client.app.state.handles.register(
                Handle(
                    handle_id="h-nest",
                    node_id="node-a",
                    storage="dir",
                    tags=["frame_sequence"],
                    path=str(d),
                    size_bytes=None,
                    output_port_name="by_frame",
                )
            )

        client.portal.call(_seed)

        body = client.get("/api/handles/h-nest/summary").json()
        entries = {e["name"]: e for e in body["fields"]["entries"]}
        assert set(entries) == {"frame_000000", "frame_000001"}
        for entry in entries.values():
            assert entry["is_dir"] is True
            children = {c["name"]: c for c in entry["children"]}
            assert "frames" in children
            frames = children["frames"]
            assert frames["is_dir"] is True
            # 2nd-level drill exposes the per-camera image files.
            leaf_names = {c["name"] for c in frames["children"]}
            assert leaf_names == {"cam00.png", "cam01.png"}
            for leaf in frames["children"]:
                assert leaf["is_dir"] is False
                assert leaf["size_bytes"] > 0
            # NestedFrameSequencePreview's per-card badge reads
            # ``entry_count`` off the drilled ``frames/`` entry so it
            # can show the true frame count even when the children list
            # was capped. Here 2 == 2 (no cap hit) — the cap-exceeded
            # case is exercised by ``test_handle_summary_frames_drill_
            # entry_count_survives_cap``.
            assert frames["entry_count"] == 2


def test_handle_summary_flat_element_dir_caps_image_children(
    tmp_path: Path,
) -> None:
    """Post-flatten ``arrayed<arrayed<image>>`` puts image files directly
    under each element dir (no ``frames/`` wrapper). The summary caps
    the per-element children at _FRAMES_DRILL_CAP and sets
    ``entry_count`` on the element itself so NestedFrameSequence
    Preview's card badge still shows the real count.
    """

    from hololab.gateway.handle_summary import _FRAMES_DRILL_CAP

    app = create_app(db_path=tmp_path / "sumflat.sqlite")
    with TestClient(app) as client:
        d = tmp_path / "by_frame_flat"
        d.mkdir()
        n_frames = _FRAMES_DRILL_CAP * 3 + 1
        # Two per-frame elements, each holding many per-cam PNGs
        # directly at the element root (the new layout).
        for frame in ("frame_0000", "frame_0001"):
            e = d / frame
            e.mkdir()
            for i in range(n_frames):
                (e / f"cam_{i:04d}.png").write_bytes(b"\x89PNG" + b"\x00" * 20)

        async def _seed() -> None:
            await client.app.state.handles.register(
                Handle(
                    handle_id="h-flat",
                    node_id="node-a",
                    storage="dir",
                    tags=["image"],
                    path=str(d),
                    size_bytes=None,
                    output_port_name="by_frame",
                )
            )

        client.portal.call(_seed)

        body = client.get("/api/handles/h-flat/summary").json()
        entries = {e["name"]: e for e in body["fields"]["entries"]}
        for entry in entries.values():
            assert entry["is_dir"] is True
            # Element children are the image files directly (no frames/).
            assert all(not c["is_dir"] for c in entry["children"])
            # Capped at _FRAMES_DRILL_CAP but entry_count carries the truth.
            assert len(entry["children"]) == _FRAMES_DRILL_CAP
            assert entry["entry_count"] == n_frames


def test_handle_summary_frames_drill_entry_count_survives_cap(
    tmp_path: Path,
) -> None:
    """When a ``frames/`` subdir has more images than the drill cap,
    the returned ``children`` list is truncated but ``entry_count`` on
    the frames entry still reports the true total. NestedFrameSequence
    Preview relies on this so the badge shows e.g. 100 frames, not the
    8-image cap.
    """

    from hololab.gateway.handle_summary import _FRAMES_DRILL_CAP

    app = create_app(db_path=tmp_path / "sumf2.sqlite")
    with TestClient(app) as client:
        d = tmp_path / "by_cam"
        d.mkdir()
        # One element with well over the drill cap of images.
        n_frames = _FRAMES_DRILL_CAP * 3 + 5  # 29
        f = d / "cam00" / "frames"
        f.mkdir(parents=True)
        for i in range(n_frames):
            (f / f"f{i:04}.png").write_bytes(b"\x89PNG" + b"\x00" * 20)

        async def _seed() -> None:
            await client.app.state.handles.register(
                Handle(
                    handle_id="h-cap",
                    node_id="node-a",
                    storage="dir",
                    tags=["frame_sequence"],
                    path=str(d),
                    size_bytes=None,
                    output_port_name="by_cam",
                )
            )

        client.portal.call(_seed)

        body = client.get("/api/handles/h-cap/summary").json()
        entry = body["fields"]["entries"][0]
        frames = next(c for c in entry["children"] if c["name"] == "frames")
        assert len(frames["children"]) == _FRAMES_DRILL_CAP
        assert frames["entry_count"] == n_frames


def test_handle_summary_unknown_returns_gracefully(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "sumu.sqlite")
    with TestClient(app) as client:
        p = tmp_path / "random.bin"
        p.write_bytes(b"\x00" * 128)

        async def _seed() -> None:
            await client.app.state.handles.register(
                Handle(
                    handle_id="h-unk",
                    node_id="node-a",
                    storage="file",
                    tags=["mystery"],
                    path=str(p),
                    size_bytes=128,
                )
            )

        client.portal.call(_seed)

        body = client.get("/api/handles/h-unk/summary").json()
        assert body["kind"] == "unknown"
        assert body["fields"] == {}


# ---------------------------------------------------------------------------
# POST /api/workflows optimistic-lock — end-to-end conflict semantics
# ---------------------------------------------------------------------------


def _minimal_graph_body(name: str = "w") -> dict[str, object]:
    return {
        "name": name,
        "graph": {"nodes": [], "edges": []},
    }


def test_save_workflow_conflict_returns_409_with_current_row(tmp_path: Path) -> None:
    """POST /api/workflows with a stale ``base_updated_ts`` → 409 + current row.

    Reproduces the "stale Chrome tab autosaves over an agent's edit" bug:
    Tab A opens draft at ts=T0, agent bumps to ts=T1, Tab A's autosave fires
    with base=T0 → server refuses with 409 and returns the T1 row so the
    tab can reconcile instead of clobbering.
    """
    app = create_app(db_path=tmp_path / "olock.sqlite")
    with TestClient(app) as client:
        # First save creates the row and returns updated_ts=T0.
        r0 = client.post("/api/workflows", json=_minimal_graph_body("w"))
        assert r0.status_code == 200, r0.text
        wid = r0.json()["workflow_id"]
        t0 = r0.json()["updated_ts"]

        # Agent (or another tab) bumps the row → ts moves to T1.
        # Under the new 428 rule the update MUST carry either a
        # base_updated_ts or an explicit overwrite flag; a well-behaved
        # agent knows the current ts, so it threads it through.
        r1 = client.post(
            "/api/workflows",
            json={
                "workflow_id": wid,
                "name": "by-agent",
                "graph": {"nodes": [], "edges": []},
                "base_updated_ts": t0,
            },
        )
        assert r1.status_code == 200
        t1 = r1.json()["updated_ts"]
        assert t1 >= t0

        # Stale tab (still holding t0) tries to save → 409.
        r2 = client.post(
            "/api/workflows",
            json={
                "workflow_id": wid,
                "name": "by-stale-tab",
                "graph": {"nodes": [], "edges": []},
                "base_updated_ts": t0,
            },
        )
        assert r2.status_code == 409, r2.text
        detail = r2.json()["detail"]
        assert detail["code"] == "workflow_conflict"
        assert detail["base_updated_ts"] == t0
        assert detail["current"]["name"] == "by-agent"
        assert detail["current"]["updated_ts"] == t1

        # The row must NOT reflect the stale save.
        r3 = client.get(f"/api/workflows/{wid}")
        assert r3.status_code == 200
        assert r3.json()["name"] == "by-agent"


def test_save_workflow_with_matching_base_ts_proceeds(tmp_path: Path) -> None:
    """POST with a matching ``base_updated_ts`` succeeds and returns the new ts."""
    app = create_app(db_path=tmp_path / "olock-ok.sqlite")
    with TestClient(app) as client:
        r0 = client.post("/api/workflows", json=_minimal_graph_body("w"))
        wid = r0.json()["workflow_id"]
        t0 = r0.json()["updated_ts"]

        r1 = client.post(
            "/api/workflows",
            json={
                "workflow_id": wid,
                "name": "w-renamed",
                "graph": {"nodes": [], "edges": []},
                "base_updated_ts": t0,
            },
        )
        assert r1.status_code == 200, r1.text
        assert r1.json()["updated_ts"] >= t0


def test_save_workflow_rejects_non_numeric_base_ts(tmp_path: Path) -> None:
    """Bad ``base_updated_ts`` type is a 400, not a 500."""
    app = create_app(db_path=tmp_path / "olock-bad.sqlite")
    with TestClient(app) as client:
        r = client.post(
            "/api/workflows",
            json={
                "name": "w",
                "graph": {"nodes": [], "edges": []},
                "base_updated_ts": "not-a-number",
            },
        )
        assert r.status_code == 400, r.text


def test_save_workflow_update_without_base_ts_returns_428(tmp_path: Path) -> None:
    """Layer-2: update on an existing row without ``base_updated_ts`` → 428.

    Reproduces the "stale bundle Chrome tab clobbers fresh edits" bug:
    a tab still running the pre-optimistic-lock code sends no
    base_updated_ts. Previously the server logged a warning and let the
    write clobber the row. Now the server refuses with 428 and returns
    the current row, so the stale tab surfaces the conflict instead of
    silently overwriting a fresh agent / other-tab edit.
    """
    app = create_app(db_path=tmp_path / "precond.sqlite")
    with TestClient(app) as client:
        r0 = client.post("/api/workflows", json=_minimal_graph_body("w"))
        assert r0.status_code == 200
        wid = r0.json()["workflow_id"]
        t0 = r0.json()["updated_ts"]

        # Stale bundle: no base_updated_ts on an update → 428.
        r1 = client.post(
            "/api/workflows",
            json={
                "workflow_id": wid,
                "name": "by-stale-bundle",
                "graph": {"nodes": [], "edges": []},
            },
        )
        assert r1.status_code == 428, r1.text
        detail = r1.json()["detail"]
        assert detail["code"] == "workflow_precondition_required"
        assert detail["current"]["name"] == "w"
        assert detail["current"]["updated_ts"] == t0

        # Row must be unchanged.
        r2 = client.get(f"/api/workflows/{wid}")
        assert r2.status_code == 200
        assert r2.json()["name"] == "w"


def test_save_workflow_overwrite_flag_bypasses_precondition(tmp_path: Path) -> None:
    """``overwrite: true`` is the documented opt-out for trusted callers.

    Scripts / agents that intentionally clobber (e.g. an ops tool restoring
    a graph verbatim) don't need to thread base_updated_ts through; they
    send ``overwrite: true`` and the CAS is skipped. The broadcast still
    fires so live watchers re-hydrate — the opt-out doesn't silently hide
    the write from other tabs.
    """
    app = create_app(db_path=tmp_path / "overwrite.sqlite")
    with TestClient(app) as client:
        r0 = client.post("/api/workflows", json=_minimal_graph_body("w"))
        wid = r0.json()["workflow_id"]

        r1 = client.post(
            "/api/workflows",
            json={
                "workflow_id": wid,
                "name": "clobber",
                "graph": {"nodes": [], "edges": []},
                "overwrite": True,
            },
        )
        assert r1.status_code == 200, r1.text

        r2 = client.get(f"/api/workflows/{wid}")
        assert r2.json()["name"] == "clobber"


def test_save_workflow_new_id_without_base_ts_is_allowed(tmp_path: Path) -> None:
    """Minting a NEW workflow (workflow_id absent) does not require base_ts.

    The 428 gate only fires on an UPDATE where the row already exists.
    Fresh drafts (Gallery "new workflow" / a first-time script POST) go
    through as 200 so we don't regress the create path.
    """
    app = create_app(db_path=tmp_path / "mint.sqlite")
    with TestClient(app) as client:
        r = client.post("/api/workflows", json=_minimal_graph_body("w"))
        assert r.status_code == 200, r.text
        assert r.json()["workflow_id"]


def test_save_workflow_broadcasts_workflow_updated(tmp_path: Path) -> None:
    """Layer-3: every successful save fires a ``workflow_updated`` broadcast.

    Verifies the frontend-fanout hook — we don't spin up a real WS
    client here; instead we monkey-patch ``FrontendHub.broadcast`` and
    assert the envelope was enqueued with the expected payload
    (workflow_id / updated_ts / echoed origin).
    """
    app = create_app(db_path=tmp_path / "broadcast.sqlite")
    with TestClient(app) as client:
        captured: list[str] = []
        original_broadcast = app.state.hub.broadcast

        def _capture(frame: str) -> None:
            captured.append(frame)
            original_broadcast(frame)

        app.state.hub.broadcast = _capture  # type: ignore[assignment]

        r0 = client.post(
            "/api/workflows",
            json={**_minimal_graph_body("w"), "origin": "tab-A"},
        )
        assert r0.status_code == 200

        # Filter to workflow_updated frames — the same hub also carries
        # node lifecycle events on other tests, but this test's DB is
        # fresh and no nodes are registered.
        wf_frames = []
        for f in captured:
            data = _json_loads(f)
            if data.get("kind") == "workflow_updated":
                wf_frames.append(data)
        assert len(wf_frames) == 1
        payload = wf_frames[0]["payload"]
        assert payload["workflow_id"] == r0.json()["workflow_id"]
        assert payload["updated_ts"] == r0.json()["updated_ts"]
        assert payload["origin"] == "tab-A"
        assert payload["name"] == "w"


def _json_loads(raw: str) -> dict[str, object]:
    import json as _json

    return _json.loads(raw)


def test_save_workflow_origin_type_validation(tmp_path: Path) -> None:
    """A non-string ``origin`` field is a 400, not a 500."""
    app = create_app(db_path=tmp_path / "origin-bad.sqlite")
    with TestClient(app) as client:
        r = client.post(
            "/api/workflows",
            json={
                "name": "w",
                "graph": {"nodes": [], "edges": []},
                "origin": 123,
            },
        )
        assert r.status_code == 400, r.text


# Silence "unused" warning for helper that's here for readability.
_ = asyncio
