"""Cosmetic patch endpoints — the observer-state channel.

Preview-drawer open / node position are *cosmetic* fields on a graph
node: they don't participate in dispatch, so mutating them doesn't
Fork a snapshot. Two endpoints implement the boundary:

* ``PATCH /api/workflows/{wid}/graph-nodes/{gnid}/cosmetic`` — updates
  the draft AND mirrors to the workflow's most recent snapshot so
  switching to the "last run" view keeps the drawer state.
* ``PATCH /api/snapshots/{sid}/graph-nodes/{gnid}/cosmetic`` — updates
  a specific snapshot only.

Both endpoints reject any field outside the cosmetic allowlist —
that's what enforces "structural is truly immutable in snapshots"
without having to bake the allowlist into the schema layer.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.workflows import GraphEdge, GraphNode, WorkflowGraph


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


def _seed_workflow(client: TestClient, workflow_id: str) -> None:
    async def _seed() -> None:
        await client.app.state.workflows.save_draft(
            workflow_id=workflow_id,
            name="cc-e2e-cosmetic",
            graph=_sample_graph(),
        )

    client.portal.call(_seed)


def _seed_workflow_with_snapshot(
    client: TestClient, workflow_id: str
) -> str:
    async def _seed() -> str:
        await client.app.state.workflows.save_draft(
            workflow_id=workflow_id,
            name="cc-e2e-cosmetic",
            graph=_sample_graph(),
        )
        snap = await client.app.state.workflows.create_snapshot(
            workflow_id=workflow_id, graph=_sample_graph()
        )
        return snap.snapshot_id

    return client.portal.call(_seed)


# ---------------------------------------------------------------------------
# Workflow endpoint
# ---------------------------------------------------------------------------


def test_workflow_cosmetic_patch_updates_draft(tmp_path: Path) -> None:
    """The most basic path: PATCH → draft's graph node gets the field."""

    app = create_app(db_path=tmp_path / "wc.sqlite")
    with TestClient(app) as client:
        wid = "11111111-1111-1111-1111-111111111111"
        _seed_workflow(client, wid)

        r = client.patch(
            f"/api/workflows/{wid}/graph-nodes/n1/cosmetic",
            json={"preview_open": "out"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["workflow_id"] == wid
        assert body["graph_node_id"] == "n1"
        assert body["applied"] == {"preview_open": "out"}
        # No snapshot exists — mirrored_to is null, not an error.
        assert body["mirrored_to"] is None

        # Round-trip: GET the workflow, verify the field survived
        # persistence and shows up on the loaded graph.
        r = client.get(f"/api/workflows/{wid}")
        assert r.status_code == 200
        graph = r.json()["graph"]
        n1 = next(n for n in graph["nodes"] if n["id"] == "n1")
        assert n1["preview_open"] == "out"


def test_workflow_cosmetic_patch_mirrors_to_latest_snapshot(
    tmp_path: Path,
) -> None:
    """When the workflow has a run, cosmetic edits back-propagate onto
    the newest snapshot so the "last run" view keeps the state."""

    app = create_app(db_path=tmp_path / "wcm.sqlite")
    with TestClient(app) as client:
        wid = "22222222-2222-2222-2222-222222222222"
        snap_id = _seed_workflow_with_snapshot(client, wid)

        r = client.patch(
            f"/api/workflows/{wid}/graph-nodes/n1/cosmetic",
            json={"preview_open": "out"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mirrored_to"] == snap_id

        # Snapshot's frozen graph now carries the mirrored cosmetic —
        # SnapshotCanvas will hydrate its own drawer from this field.
        r = client.get(f"/api/snapshots/{snap_id}")
        assert r.status_code == 200
        graph = r.json()["graph"]
        n1 = next(n for n in graph["nodes"] if n["id"] == "n1")
        assert n1["preview_open"] == "out"


def test_workflow_cosmetic_patch_rejects_structural_field(
    tmp_path: Path,
) -> None:
    """The whole point of a cosmetic-only endpoint is that you can't
    smuggle a structural change through it. ``params`` is structural
    (feeds executors + defines data lineage); the endpoint must 400."""

    app = create_app(db_path=tmp_path / "wcbad.sqlite")
    with TestClient(app) as client:
        wid = "33333333-3333-3333-3333-333333333333"
        _seed_workflow(client, wid)

        r = client.patch(
            f"/api/workflows/{wid}/graph-nodes/n1/cosmetic",
            json={"params": {"message": "malicious"}},
        )
        assert r.status_code == 400
        assert "cosmetic" in r.json()["detail"]


def test_workflow_cosmetic_patch_null_preview_open_clears_it(
    tmp_path: Path,
) -> None:
    """Closing a drawer is ``{preview_open: null}`` — the endpoint
    must accept it and persist the null (drawer shows closed on
    subsequent hydration)."""

    app = create_app(db_path=tmp_path / "wcnull.sqlite")
    with TestClient(app) as client:
        wid = "44444444-4444-4444-4444-444444444444"
        _seed_workflow(client, wid)

        # Open first, then close.
        client.patch(
            f"/api/workflows/{wid}/graph-nodes/n1/cosmetic",
            json={"preview_open": "out"},
        )
        r = client.patch(
            f"/api/workflows/{wid}/graph-nodes/n1/cosmetic",
            json={"preview_open": None},
        )
        assert r.status_code == 200

        r = client.get(f"/api/workflows/{wid}")
        n1 = next(
            n for n in r.json()["graph"]["nodes"] if n["id"] == "n1"
        )
        assert n1["preview_open"] is None


def test_workflow_cosmetic_patch_404_unknown_workflow(
    tmp_path: Path,
) -> None:
    app = create_app(db_path=tmp_path / "w404.sqlite")
    with TestClient(app) as client:
        r = client.patch(
            "/api/workflows/99999999-9999-9999-9999-999999999999"
            "/graph-nodes/n1/cosmetic",
            json={"preview_open": "out"},
        )
        assert r.status_code == 404


def test_workflow_cosmetic_patch_404_unknown_graph_node(
    tmp_path: Path,
) -> None:
    app = create_app(db_path=tmp_path / "w404g.sqlite")
    with TestClient(app) as client:
        wid = "55555555-5555-5555-5555-555555555555"
        _seed_workflow(client, wid)

        r = client.patch(
            f"/api/workflows/{wid}/graph-nodes/nope/cosmetic",
            json={"preview_open": "out"},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Snapshot endpoint
# ---------------------------------------------------------------------------


def test_snapshot_cosmetic_patch_updates_frozen_graph(tmp_path: Path) -> None:
    """The snapshot's own graph_json gets the field — no propagation
    to the draft or to other snapshots."""

    app = create_app(db_path=tmp_path / "sc.sqlite")
    with TestClient(app) as client:
        wid = "66666666-6666-6666-6666-666666666666"
        snap_id = _seed_workflow_with_snapshot(client, wid)

        r = client.patch(
            f"/api/snapshots/{snap_id}/graph-nodes/n1/cosmetic",
            json={"preview_open": "out"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["applied"] == {"preview_open": "out"}

        r = client.get(f"/api/snapshots/{snap_id}")
        n1 = next(
            n for n in r.json()["graph"]["nodes"] if n["id"] == "n1"
        )
        assert n1["preview_open"] == "out"

        # Draft did NOT change — snapshot edits do not back-propagate.
        r = client.get(f"/api/workflows/{wid}")
        n1_draft = next(
            n for n in r.json()["graph"]["nodes"] if n["id"] == "n1"
        )
        assert n1_draft.get("preview_open") is None


def test_snapshot_cosmetic_patch_rejects_structural(tmp_path: Path) -> None:
    """Snapshots are structurally immutable — trying to change
    ``params`` on a snapshot via the cosmetic endpoint must 400."""

    app = create_app(db_path=tmp_path / "scbad.sqlite")
    with TestClient(app) as client:
        wid = "77777777-7777-7777-7777-777777777777"
        snap_id = _seed_workflow_with_snapshot(client, wid)

        r = client.patch(
            f"/api/snapshots/{snap_id}/graph-nodes/n1/cosmetic",
            json={"params": {"message": "no"}},
        )
        assert r.status_code == 400


def test_snapshot_cosmetic_patch_404_unknown_snapshot(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "s404.sqlite")
    with TestClient(app) as client:
        r = client.patch(
            "/api/snapshots/99999999-9999-9999-9999-999999999999"
            "/graph-nodes/n1/cosmetic",
            json={"preview_open": "out"},
        )
        assert r.status_code == 404


def test_empty_patch_rejected(tmp_path: Path) -> None:
    """An empty body is a bug on the caller — reject rather than
    quietly no-op so it surfaces at development time."""

    app = create_app(db_path=tmp_path / "empty.sqlite")
    with TestClient(app) as client:
        wid = "88888888-8888-8888-8888-888888888888"
        _seed_workflow(client, wid)

        r = client.patch(
            f"/api/workflows/{wid}/graph-nodes/n1/cosmetic",
            json={},
        )
        assert r.status_code == 400
        assert "empty" in r.json()["detail"]


def test_workflow_cosmetic_patch_rejects_flops_executor_id(
    tmp_path: Path,
) -> None:
    """Guardrail: after refactoring ``flops_executor_id`` onto the
    compute-node's config.yaml (see docs/cobrowser-integration.md),
    the cosmetic endpoint must reject it — that keeps a stale
    frontend from silently succeeding against a rebooted server that
    no longer expects graph-node-level values.
    """

    app = create_app(db_path=tmp_path / "wcfl_rej.sqlite")
    with TestClient(app) as client:
        wid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        _seed_workflow(client, wid)
        r = client.patch(
            f"/api/workflows/{wid}/graph-nodes/n1/cosmetic",
            json={"flops_executor_id": "dev_abc12345"},
        )
        assert r.status_code == 400
        assert "cosmetic" in r.json()["detail"]
