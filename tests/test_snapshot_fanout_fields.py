"""``GET /api/snapshots/{sid}`` must expose fan-out fields on job rows.

Regression guard for the bug where ``SnapshotJobOut`` was missing
``expected_shards``, ``parent_job_id``, and ``shard_element_id`` —
Pydantic silently stripped them before the response reached the frontend,
so ``aggregateProgress`` fell back to the growing shard-row-count as the
fan-out denominator instead of the stable planned total.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.jobs import Job, JobState
from hololab.gateway.workflows import GraphNode, WorkflowGraph


def _one_node_graph(gnid: str = "fan") -> WorkflowGraph:
    return WorkflowGraph(
        nodes=[
            GraphNode(
                id=gnid,
                algorithm_name="demo",
                algorithm_version="0.1.0",
                assigned_node_id=None,
            )
        ],
        edges=[],
    )


def _seed_fanout(client: TestClient, *, elements: list[str]) -> tuple[str, str, list[str]]:
    """Create a snapshot with one parent job + N shard jobs.

    Returns (snapshot_id, parent_job_id, list[shard_job_id]).
    """

    async def _seed() -> tuple[str, str, list[str]]:
        wf = await client.app.state.workflows.save_draft(
            workflow_id=None, name="fanout-test", graph=_one_node_graph()
        )
        snap = await client.app.state.workflows.create_snapshot(
            workflow_id=wf.workflow_id, graph=_one_node_graph()
        )

        parent = Job(
            job_id="parent-job",
            snapshot_id=snap.snapshot_id,
            workflow_id=wf.workflow_id,
            graph_node_id="fan",
            algorithm_name="demo",
            algorithm_version="0.1.0",
            state=JobState.RUNNING,
            expected_shards=len(elements),
        )
        await client.app.state.jobs_store.create(parent)

        shard_ids: list[str] = []
        for i, elem in enumerate(elements):
            shard = Job(
                job_id=f"shard-{elem}",
                snapshot_id=snap.snapshot_id,
                workflow_id=wf.workflow_id,
                graph_node_id="fan",
                algorithm_name="demo",
                algorithm_version="0.1.0",
                state=JobState.DONE,
                parent_job_id=parent.job_id,
                shard_element_id=elem,
                created_ts=parent.created_ts + 0.001 * (i + 1),
            )
            await client.app.state.jobs_store.create(shard)
            shard_ids.append(shard.job_id)

        return snap.snapshot_id, parent.job_id, shard_ids

    return client.portal.call(_seed)


def test_snapshot_detail_parent_carries_expected_shards(tmp_path: Path) -> None:
    """Parent job row from GET /api/snapshots/{sid} must include expected_shards."""

    app = create_app(db_path=tmp_path / "hl.db")
    with TestClient(app) as client:
        elements = ["cam_A", "cam_B", "cam_C"]
        snap_id, parent_id, _ = _seed_fanout(client, elements=elements)

        r = client.get(f"/api/snapshots/{snap_id}")
        assert r.status_code == 200

        jobs_by_id = {j["job_id"]: j for j in r.json()["jobs"]}
        parent_row = jobs_by_id[parent_id]

        assert parent_row["expected_shards"] == len(elements), (
            f"expected_shards should be {len(elements)} on the parent row, "
            f"got {parent_row['expected_shards']!r} — "
            "SnapshotJobOut was likely missing the field and Pydantic stripped it"
        )


def test_snapshot_detail_shard_carries_parent_job_id_and_element_id(tmp_path: Path) -> None:
    """Shard job rows must carry parent_job_id and shard_element_id.

    These fields are required by ``aggregateProgress`` to distinguish shards
    from the parent coordinator when computing the fan-out denominator.
    """

    app = create_app(db_path=tmp_path / "hl.db")
    with TestClient(app) as client:
        elements = ["elem_0", "elem_1"]
        snap_id, parent_id, shard_ids = _seed_fanout(client, elements=elements)

        r = client.get(f"/api/snapshots/{snap_id}")
        assert r.status_code == 200

        jobs_by_id = {j["job_id"]: j for j in r.json()["jobs"]}

        for sid, elem in zip(shard_ids, elements, strict=True):
            shard_row = jobs_by_id[sid]
            assert shard_row["parent_job_id"] == parent_id, (
                f"shard {sid}: expected parent_job_id={parent_id!r}, "
                f"got {shard_row['parent_job_id']!r}"
            )
            assert shard_row["shard_element_id"] == elem, (
                f"shard {sid}: expected shard_element_id={elem!r}, "
                f"got {shard_row['shard_element_id']!r}"
            )
            assert shard_row["expected_shards"] is None, (
                f"shard {sid}: expected_shards should be None on shard rows, "
                f"got {shard_row['expected_shards']!r}"
            )
