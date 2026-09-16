"""Coverage for the ``hololab://`` reference scheme + /api/resolve.

Two layers of coverage:
  * :mod:`hololab.gateway.refs` — pure parse/format round-trip, one
    test per accepted kind + one per malformed input class.
  * The resolve endpoint — happy-path resolution for each kind plus
    the 400/404 rejection paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.handles import Handle
from hololab.gateway.jobs import Job, JobState
from hololab.gateway.refs import RefParseError, format_ref, parse_ref
from hololab.gateway.workflows import GraphEdge, GraphNode, WorkflowGraph

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_all_uuid_kinds() -> None:
    for kind in ("workflow", "run", "job", "handle", "node"):
        uid = "7f6248ac-be3c-4b4b-9cd3-18dd20a2051b"
        r = parse_ref(f"hololab://{kind}/{uid}")
        assert r.kind == kind
        assert r.id == uid
        assert r.comment is None


def test_parse_pack_uses_name_at_version() -> None:
    r = parse_ref("hololab://pack/stg-to-splatv@0.1.0")
    assert r.kind == "pack"
    assert r.id == "stg-to-splatv@0.1.0"


def test_parse_strips_comment_tail() -> None:
    """The copy button appends '  # human context' — resolver must ignore it."""

    token = (
        "hololab://job/7f6248ac-be3c-4b4b-9cd3-18dd20a2051b  # stg-train · done · in run c72b7e5c"
    )
    r = parse_ref(token)
    assert r.kind == "job"
    assert r.id == "7f6248ac-be3c-4b4b-9cd3-18dd20a2051b"
    assert r.comment == "stg-train · done · in run c72b7e5c"
    # Canonical form drops the comment.
    assert r.canonical() == "hololab://job/7f6248ac-be3c-4b4b-9cd3-18dd20a2051b"


def test_parse_tolerates_leading_and_trailing_whitespace() -> None:
    r = parse_ref("  hololab://run/7f6248ac-be3c-4b4b-9cd3-18dd20a2051b  ")
    assert r.kind == "run"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-ref",
        "hololab://",  # no kind/id
        "hololab://job",  # no id
        "hololab://job/",  # empty id
        "hololab://bogus/7f6248ac-be3c-4b4b-9cd3-18dd20a2051b",  # unknown kind
        "hololab://job/not-a-uuid",
        "hololab://pack/bad_no_at_version",
    ],
)
def test_parse_rejects_malformed(bad: str) -> None:
    with pytest.raises(RefParseError):
        parse_ref(bad)


def test_format_round_trip() -> None:
    """format_ref then parse_ref returns the same fields (canonical)."""

    for kind, ident in [
        ("workflow", "7f6248ac-be3c-4b4b-9cd3-18dd20a2051b"),
        ("run", "7f6248ac-be3c-4b4b-9cd3-18dd20a2051b"),
        ("pack", "demo-echo@0.1.0"),
    ]:
        emitted = format_ref(kind, ident, comment="some context")
        # The comment is embedded but stripped on parse.
        assert emitted.startswith(f"hololab://{kind}/{ident}")
        assert " # some context" in emitted
        back = parse_ref(emitted)
        assert back.kind == kind
        assert back.id == ident
        assert back.comment == "some context"


# ---------------------------------------------------------------------------
# /api/resolve — endpoint
# ---------------------------------------------------------------------------


def _sample_graph() -> WorkflowGraph:
    return WorkflowGraph(
        nodes=[
            GraphNode(
                id="n1",
                algorithm_name="demo-echo",
                algorithm_version="0.1.0",
                params={"message": "hi"},
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


def _seed_wf_and_run(client: TestClient, workflow_id: str = "cc-e2e-refs") -> tuple[str, str]:
    """Returns (snapshot_id, job_id)."""

    graph = _sample_graph()

    async def _seed() -> tuple[str, str]:
        await client.app.state.workflows.save_draft(
            workflow_id=workflow_id, name="cc-e2e-refs", graph=graph
        )
        snap = await client.app.state.workflows.create_snapshot(
            workflow_id=workflow_id, graph=graph
        )
        job = Job(
            job_id="7f6248ac-be3c-4b4b-9cd3-18dd20a2051b",
            snapshot_id=snap.snapshot_id,
            workflow_id=workflow_id,
            node_id="node-a",
            graph_node_id="n1",
            algorithm_name="demo-echo",
            algorithm_version="0.1.0",
            params={"message": "hi"},
            input_handles={},
            state=JobState.DONE,
        )
        await client.app.state.jobs_store.create(job)
        return snap.snapshot_id, job.job_id

    return client.portal.call(_seed)


def test_resolve_workflow(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "rw.sqlite")
    with TestClient(app) as client:
        # The parser requires a UUID for workflow ids — seed with one and
        # skip the default ``cc-e2e-refs`` id from the helper.

        async def _reseed() -> None:
            await client.app.state.workflows.save_draft(
                workflow_id="11111111-1111-1111-1111-111111111111",
                name="cc-e2e-refs",
                graph=_sample_graph(),
            )

        client.portal.call(_reseed)
        r = client.get(
            "/api/resolve",
            params={"ref": "hololab://workflow/11111111-1111-1111-1111-111111111111"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "workflow"
        assert body["resource"]["name"] == "cc-e2e-refs"
        assert "runs" in body["related"]
        # agent-shaped graph is present.
        assert body["resource"]["graph"]["is_dag"] is True


def test_resolve_run(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "rr.sqlite")
    with TestClient(app) as client:
        # Save with UUID workflow_id so we can resolve it and its snapshot.
        wid = "22222222-2222-2222-2222-222222222222"

        async def _seed() -> tuple[str, str]:
            await client.app.state.workflows.save_draft(
                workflow_id=wid, name="cc-e2e-refs", graph=_sample_graph()
            )
            snap = await client.app.state.workflows.create_snapshot(
                workflow_id=wid, graph=_sample_graph()
            )
            return snap.snapshot_id, ""

        snap_id, _ = client.portal.call(_seed)
        r = client.get("/api/resolve", params={"ref": f"hololab://run/{snap_id}"})
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "run"
        assert body["resource"]["snapshot_id"] == snap_id
        # wait_for_done link is one of the hop targets.
        assert "wait_for_done" in body["related"]


def test_resolve_job_with_comment_tail(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "rj.sqlite")
    with TestClient(app) as client:
        wid = "33333333-3333-3333-3333-333333333333"

        async def _seed() -> str:
            await client.app.state.workflows.save_draft(
                workflow_id=wid, name="cc-e2e-refs", graph=_sample_graph()
            )
            snap = await client.app.state.workflows.create_snapshot(
                workflow_id=wid, graph=_sample_graph()
            )
            job = Job(
                job_id="7f6248ac-be3c-4b4b-9cd3-18dd20a2051b",
                snapshot_id=snap.snapshot_id,
                workflow_id=wid,
                node_id="node-a",
                graph_node_id="n1",
                algorithm_name="demo-echo",
                algorithm_version="0.1.0",
                params={"message": "hi"},
                input_handles={},
                state=JobState.DONE,
            )
            await client.app.state.jobs_store.create(job)
            return job.job_id

        job_id = client.portal.call(_seed)

        # The copied token from the UI includes the human context — the
        # endpoint must accept it verbatim.
        ref = f"hololab://job/{job_id}  # demo-echo · done · in run …"
        r = client.get("/api/resolve", params={"ref": ref})
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "job"
        assert body["resource"]["job_id"] == job_id
        assert body["resource"]["state"] == "done"
        # Related includes log + snapshot + workflow — all the 1-hop moves
        # an agent might want to make next.
        assert body["related"]["log"] == f"/api/jobs/{job_id}/log"
        assert "snapshot" in body["related"]
        assert body["related"]["workflow"] == f"/api/workflows/{wid}"


def test_resolve_handle(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "rh.sqlite")
    with TestClient(app) as client:

        async def _seed() -> str:
            handle_id = "44444444-4444-4444-4444-444444444444"
            await client.app.state.handles.register(
                Handle(
                    handle_id=handle_id,
                    node_id="node-a",
                    storage="file",
                    tags=["splatv"],
                    path="/tmp/nowhere.splatv",
                    size_bytes=1234,
                    job_id="55555555-5555-5555-5555-555555555555",
                    output_port_name="splatv",
                )
            )
            return handle_id

        handle_id = client.portal.call(_seed)
        r = client.get("/api/resolve", params={"ref": f"hololab://handle/{handle_id}"})
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "handle"
        assert body["resource"]["handle_id"] == handle_id
        assert body["related"]["summary"] == f"/api/handles/{handle_id}/summary"
        assert body["related"]["job"] == "/api/jobs/55555555-5555-5555-5555-555555555555"
        assert body["related"]["bytes"].startswith("/proxy/node-a/")


def test_resolve_rejects_malformed(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "rbad.sqlite")
    with TestClient(app) as client:
        r = client.get("/api/resolve", params={"ref": "not-a-ref"})
        assert r.status_code == 400
        assert "hololab://" in r.json()["detail"]


def test_resolve_404_on_unknown_id(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "r404.sqlite")
    with TestClient(app) as client:
        r = client.get(
            "/api/resolve",
            params={"ref": "hololab://job/99999999-9999-9999-9999-999999999999"},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# graph-node — the "position on the canvas" kind
# ---------------------------------------------------------------------------


def test_parse_graph_node_compound_id() -> None:
    """The compound id ``<workflow_uuid>/<graph_node_id>`` round-trips."""

    r = parse_ref(
        "hololab://graph-node/11111111-1111-1111-1111-111111111111/d1src"
    )
    assert r.kind == "graph-node"
    assert r.id == "11111111-1111-1111-1111-111111111111/d1src"
    assert r.comment is None


def test_parse_graph_node_with_comment_tail() -> None:
    ref = (
        "hololab://graph-node/11111111-1111-1111-1111-111111111111/d1src"
        "  # single-video-source · done"
    )
    r = parse_ref(ref)
    assert r.kind == "graph-node"
    assert r.comment == "single-video-source · done"
    assert r.canonical() == (
        "hololab://graph-node/11111111-1111-1111-1111-111111111111/d1src"
    )


@pytest.mark.parametrize(
    "bad",
    [
        # Missing the graph_node_id segment.
        "hololab://graph-node/11111111-1111-1111-1111-111111111111",
        # workflow_id not a UUID.
        "hololab://graph-node/nope/d1src",
        # graph_node_id has an illegal character (space).
        "hololab://graph-node/11111111-1111-1111-1111-111111111111/d1 src",
    ],
)
def test_parse_graph_node_rejects_malformed(bad: str) -> None:
    with pytest.raises(RefParseError):
        parse_ref(bad)


def test_resolve_graph_node_self_describing(tmp_path: Path) -> None:
    """Resolving a graph-node ref returns the algorithm/params of the
    draft node plus one-hop links to the latest run + job at that
    position, so an agent can go from paste to full context in one call."""

    app = create_app(db_path=tmp_path / "rgn.sqlite")
    with TestClient(app) as client:
        wid = "66666666-6666-6666-6666-666666666666"

        async def _seed() -> tuple[str, str]:
            await client.app.state.workflows.save_draft(
                workflow_id=wid, name="cc-e2e-gnode", graph=_sample_graph()
            )
            snap = await client.app.state.workflows.create_snapshot(
                workflow_id=wid, graph=_sample_graph()
            )
            job = Job(
                job_id="88888888-8888-8888-8888-888888888888",
                snapshot_id=snap.snapshot_id,
                workflow_id=wid,
                node_id="node-a",
                graph_node_id="n1",
                algorithm_name="demo-echo",
                algorithm_version="0.1.0",
                params={"message": "hi"},
                input_handles={},
                state=JobState.DONE,
            )
            await client.app.state.jobs_store.create(job)
            await client.app.state.handles.register(
                Handle(
                    handle_id="99999999-9999-9999-9999-999999999999",
                    node_id="node-a",
                    storage="file",
                    tags=["demo"],
                    path="/tmp/n1.out",
                    job_id=job.job_id,
                    output_port_name="out",
                )
            )
            return snap.snapshot_id, job.job_id

        snap_id, job_id = client.portal.call(_seed)

        ref = f"hololab://graph-node/{wid}/n1  # demo-echo · done"
        r = client.get("/api/resolve", params={"ref": ref})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "graph-node"

        resource = body["resource"]
        assert resource["workflow_id"] == wid
        assert resource["workflow_name"] == "cc-e2e-gnode"
        assert resource["graph_node_id"] == "n1"
        assert resource["algorithm_name"] == "demo-echo"
        assert resource["algorithm_version"] == "0.1.0"
        assert resource["params"] == {"message": "hi"}
        assert resource["assigned_node_id"] == "node-a"
        # Edge topology summary — n1 has one downstream (to n2) and no
        # upstream in the sample graph.
        assert resource["upstream"] == []
        assert resource["downstream"] == [
            {"port": "out", "target_graph_node": "n2", "target_port": "in"}
        ]
        # Latest attribution surfaces the recorded snapshot + job.
        assert resource["latest_snapshot_id"] == snap_id
        assert resource["latest_job_id"] == job_id
        assert resource["latest_output_handles"] == {
            "out": "99999999-9999-9999-9999-999999999999"
        }

        # One-hop related links let an agent drill without guessing shapes.
        related = body["related"]
        assert related["workflow"] == f"/api/workflows/{wid}"
        assert related["dispatch"] == f"/api/workflows/{wid}/dispatch/n1"
        assert related["latest_run"] == f"/api/snapshots/{snap_id}"
        assert related["latest_job"] == f"/api/jobs/{job_id}"
        assert related["latest_job_log"] == f"/api/jobs/{job_id}/log"


def test_resolve_graph_node_404_unknown_workflow(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "rgn404.sqlite")
    with TestClient(app) as client:
        r = client.get(
            "/api/resolve",
            params={
                "ref": (
                    "hololab://graph-node/99999999-9999-9999-9999-999999999999/n1"
                )
            },
        )
        assert r.status_code == 404


def test_resolve_graph_node_404_unknown_gnode(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "rgn404g.sqlite")
    with TestClient(app) as client:
        wid = "77777777-7777-7777-7777-777777777777"

        async def _seed() -> None:
            await client.app.state.workflows.save_draft(
                workflow_id=wid, name="cc-e2e-gnode-missing", graph=_sample_graph()
            )

        client.portal.call(_seed)
        r = client.get(
            "/api/resolve",
            params={"ref": f"hololab://graph-node/{wid}/nope"},
        )
        assert r.status_code == 404
        assert "nope" in r.json()["detail"]


# ---------------------------------------------------------------------------
# workflows / artifacts — index (collection) kinds with NO id
# ---------------------------------------------------------------------------


def test_parse_workflows_index_no_id() -> None:
    """The token ``hololab://workflows`` (no ``/id``) round-trips."""

    r = parse_ref("hololab://workflows")
    assert r.kind == "workflows"
    assert r.id == ""
    assert r.canonical() == "hololab://workflows"


def test_parse_workflows_index_trailing_slash_accepted() -> None:
    """Trailing slash is tolerated so users don't have to remember it."""

    r = parse_ref("hololab://workflows/")
    assert r.kind == "workflows"
    assert r.id == ""


def test_parse_artifacts_index_no_id() -> None:
    r = parse_ref("hololab://artifacts")
    assert r.kind == "artifacts"
    assert r.id == ""
    assert r.canonical() == "hololab://artifacts"


def test_parse_index_with_comment_tail() -> None:
    r = parse_ref("hololab://workflows  # 4 workflows on kiri4090")
    assert r.kind == "workflows"
    assert r.id == ""
    assert r.comment == "4 workflows on kiri4090"


def test_parse_workflows_rejects_id_segment() -> None:
    """Index kinds must NOT carry an id — that would collide with the
    ``hololab://workflow/<uuid>`` singular kind and hide the mistake."""

    with pytest.raises(RefParseError):
        parse_ref(
            "hololab://workflows/11111111-1111-1111-1111-111111111111"
        )


def test_format_workflows_index_round_trip() -> None:
    emitted = format_ref("workflows", "", comment="4 workflows")
    assert emitted == "hololab://workflows  # 4 workflows"
    back = parse_ref(emitted)
    assert back.kind == "workflows"
    assert back.id == ""
    assert back.comment == "4 workflows"


def test_format_index_kind_rejects_id() -> None:
    with pytest.raises(RefParseError):
        format_ref("workflows", "not-empty", comment=None)


def test_resolve_workflows_returns_list_summary(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "rws.sqlite")
    with TestClient(app) as client:
        wid = "88888888-8888-8888-8888-888888888888"

        async def _seed() -> None:
            await client.app.state.workflows.save_draft(
                workflow_id=wid, name="cc-e2e-workflows-idx", graph=_sample_graph()
            )

        client.portal.call(_seed)
        r = client.get("/api/resolve", params={"ref": "hololab://workflows"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "workflows"
        assert body["ref"] == "hololab://workflows"
        assert body["resource"]["workflow_count"] == 1
        # Row shape carries the fields Gallery needs.
        row = body["resource"]["workflows"][0]
        assert row["workflow_id"] == wid
        assert row["name"] == "cc-e2e-workflows-idx"
        assert "node_count" in row
        # One-hop nav to the full list + the Artifacts index for symmetry.
        assert body["related"]["list"] == "/api/workflows"
        assert body["related"]["artifacts_index"] == "hololab://artifacts"


def test_resolve_artifacts_returns_summary(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "rart.sqlite")
    with TestClient(app) as client:
        # Seed nothing — an empty index still resolves cleanly.
        r = client.get("/api/resolve", params={"ref": "hololab://artifacts"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "artifacts"
        assert body["ref"] == "hololab://artifacts"
        # Same shape /api/artifacts/summary returns — no artifacts yet.
        assert body["resource"]["total_bytes"] == 0
        assert body["resource"]["total_count"] == 0
        assert body["related"]["summary"] == "/api/artifacts/summary"
        assert body["related"]["workflows_index"] == "hololab://workflows"


def test_resolve_workflows_rejects_id_form(tmp_path: Path) -> None:
    """The token ``hololab://workflows/<uuid>`` should reject at parse
    time — an index kind with an id is either a typo for ``workflow``
    (singular) or malformed."""

    app = create_app(db_path=tmp_path / "rwsbad.sqlite")
    with TestClient(app) as client:
        r = client.get(
            "/api/resolve",
            params={
                "ref": (
                    "hololab://workflows/11111111-1111-1111-1111-111111111111"
                )
            },
        )
        assert r.status_code == 400
