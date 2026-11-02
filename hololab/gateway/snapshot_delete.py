"""Delete a run only after preserving storage used by surviving results.

Snapshot membership protects the whole recorded parent/shard/reuse family.
Transitive producer inputs and overlapping address records protect storage too:
zero direct references to a producing job is insufficient permission to rm.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException

from hololab.gateway import artifacts as art
from hololab.gateway.artifact_lifecycle import LIVE_STATES, ArtifactIndex
from hololab.gateway.handles import Handle
from hololab.logging import get_logger

log = get_logger("gateway.snapshot_delete")


@dataclass
class DeletionImpact:
    snapshot_id: str
    workflow_id: str
    job_count: int
    live_jobs: list[dict[str, str]]
    exclusive_job_ids: list[str]
    shared_job_ids: list[str]
    exclusive_artifacts: list[Handle]
    shared_artifacts: list[Handle]
    physical_artifacts: list[Handle] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.live_jobs)

    @property
    def exclusive_artifact_count(self) -> int:
        return len(self.exclusive_artifacts)

    @property
    def shared_artifact_count(self) -> int:
        return len(self.shared_artifacts)

    @property
    def exclusive_bytes(self) -> int:
        return sum(int(h.size_bytes or 0) for h in self.physical_artifacts)


async def compute_impact(app: FastAPI, snapshot_id: str) -> DeletionImpact | None:
    snap = await app.state.workflows.get_snapshot(snapshot_id)
    if snap is None:
        return None
    index = await ArtifactIndex.load(app.state.db)
    candidates = index.snapshot_families(snapshot_id)
    retained, _ = index.retained(excluding_snapshot=snapshot_id)
    exclusive = sorted(j for root in candidates - retained for j in index.members[root])
    shared = sorted(j for root in candidates & retained for j in index.members[root])
    live = [
        index.jobs[j]
        for root in candidates
        for j in index.members[root]
        if index.jobs[j].state in LIVE_STATES
    ]
    exclusive_handles = [
        h for root in candidates - retained for h in index.outputs[root] if h.deleted_ts is None
    ]
    shared_handles = [
        h for root in candidates & retained for h in index.outputs[root] if h.deleted_ts is None
    ]
    return DeletionImpact(
        snapshot_id=snapshot_id,
        workflow_id=snap.workflow_id,
        job_count=len(exclusive) + len(shared),
        live_jobs=[
            {"job_id": j.job_id, "state": j.state, "algorithm_name": j.algorithm_name} for j in live
        ],
        exclusive_job_ids=exclusive,
        shared_job_ids=shared,
        exclusive_artifacts=index.logical(exclusive_handles),
        shared_artifacts=index.logical(shared_handles),
        physical_artifacts=index.deletion_roots(exclusive_handles),
    )


def impact_to_json(impact: DeletionImpact) -> dict[str, Any]:
    return {
        "snapshot_id": impact.snapshot_id,
        "workflow_id": impact.workflow_id,
        "job_count": impact.job_count,
        "live_jobs": impact.live_jobs,
        "blocked": impact.blocked,
        "artifacts": {
            "exclusive_count": impact.exclusive_artifact_count,
            "shared_count": impact.shared_artifact_count,
            "exclusive_bytes": impact.exclusive_bytes,
        },
        "jobs": {
            "exclusive_count": len(impact.exclusive_job_ids),
            "shared_count": len(impact.shared_job_ids),
        },
    }


async def delete_snapshot(app: FastAPI, snapshot_id: str) -> dict[str, Any]:
    # Serialize cleaners, but hold the write barrier only while surveying and
    # claiming. Blocking writes during RPC would stop node logs/progress ahead
    # of the delete ACK on the same websocket. Claims fence new references.
    async with app.state.db.artifact_cleanup():
        async with app.state.db.artifact_mutation():
            impact = await compute_impact(app, snapshot_id)
            if impact is None:
                return {"snapshot_id": snapshot_id, "state": "gone"}
            if impact.blocked:
                raise HTTPException(
                    409,
                    detail={
                        "message": "snapshot has live jobs — cancel the run first, then delete",
                        "live_jobs": impact.live_jobs,
                    },
                )
            from hololab.gateway.artifact_lifecycle import claim_cleanup

            await claim_cleanup(app.state.db, impact.physical_artifacts, snapshot_id=snapshot_id)
        freed_bytes = 0
        physically_deleted = 0
        for handle in impact.physical_artifacts:
            session = app.state.registry.get_session(handle.node_id)
            # A failure keeps the snapshot and family rows for retry. Already
            # acknowledged roots are tombstoned and skipped on the next plan.
            resp = await art.delete_artifact(app, session, handle, excluding_snapshot=snapshot_id)
            freed_bytes += int(resp.get("freed_bytes") or 0)
            physically_deleted += 1
            log.info(
                "artifact physically deleted",
                snapshot_id=snapshot_id,
                handle_id=handle.handle_id,
                node_id=handle.node_id,
                path=handle.path,
            )

        async def write(conn):
            await conn.execute(
                "UPDATE snapshots SET parent_snapshot_id=NULL WHERE parent_snapshot_id=?",
                (snapshot_id,),
            )
            await conn.execute("DELETE FROM snapshots WHERE snapshot_id=?", (snapshot_id,))
            await conn.executemany(
                "DELETE FROM jobs WHERE job_id=?", [(jid,) for jid in impact.exclusive_job_ids]
            )

        async with app.state.db.artifact_mutation():
            # A concurrent workflow removal may have released other families;
            # never purge a newly exclusive family we did not physically plan.
            current = await compute_impact(app, snapshot_id)
            if current is not None:
                impact.exclusive_job_ids = sorted(
                    set(impact.exclusive_job_ids) & set(current.exclusive_job_ids)
                )
            await app.state.db.write(write)
        return {
            "snapshot_id": snapshot_id,
            "workflow_id": impact.workflow_id,
            "state": "deleted",
            "jobs_removed": len(impact.exclusive_job_ids),
            "jobs_kept_shared": len(impact.shared_job_ids),
            "artifacts_removed_from_disk": physically_deleted,
            "artifacts_tombstoned_only": 0,
            "artifacts_kept_shared": impact.shared_artifact_count,
            "freed_bytes": freed_bytes,
            "artifact_tombstone_notes": [],
        }
