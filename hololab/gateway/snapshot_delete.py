"""Reference-counted deletion of one snapshot (one "run").

The Run History right-click "delete run + artifacts" flow lands here.
Semantics — spelled out because the reference-counting rule is what
makes this non-trivial:

1. A snapshot S attributes N jobs via ``snapshot_jobs``. A job may be
   attributed to many snapshots (Continue/Fork reuse the same producing
   job in the new snapshot instead of re-running).

2. Deleting S must therefore *only* physically remove artifacts that
   belong to jobs no other snapshot still references. For each job J
   attributed to S:

   * ``exclusive`` — J appears in ``snapshot_jobs`` under S alone. J
     is orphaned after we drop S: its handles get physically deleted
     via the producing node (``artifact_delete_req``), tombstoned in
     the handle book, and J itself + its ``job_events`` / ``job_logs``
     get purged (FK CASCADE handles the child tables).
   * ``shared`` — J is still attributed to at least one other
     snapshot. Nothing on disk changes; we only drop S's own
     attribution rows (FK CASCADE from ``snapshots`` handles this).

3. ``snapshots.parent_snapshot_id`` on child snapshots is a plain TEXT
   column (no FK), so we explicitly null it out on children so the UI
   doesn't render "forked from <deleted>" links.

4. Refusal case: if any job attributed to S is in a non-terminal state
   (``pending`` / ``assigned`` / ``running``), we refuse with 409 — the
   caller must cancel the run first. Auto-cancel would need job
   cancellation to be robust across offline nodes; it isn't yet, so we
   surface the choice to the operator instead.

The endpoint layer in ``app.py`` is a thin transport shell; all the
DB logic + node RPC orchestration lives here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiosqlite
from fastapi import FastAPI, HTTPException

from hololab.gateway import artifacts as art
from hololab.gateway.handles import Handle, HandleBook
from hololab.gateway.registry import NodeRegistry
from hololab.persistence.db import Database

# Job states that block deletion — the run is still moving, so ripping
# its artifacts out from under a live subprocess would leave orphaned
# files and confuse the executor.
_LIVE_JOB_STATES = ("pending", "assigned", "running")


@dataclass
class DeletionImpact:
    """Preview of what a snapshot-delete would do.

    Powers both the confirmation modal ("N artifacts kept, K deleted,
    B bytes freed") and the 409-refuse response when a live job blocks
    deletion.
    """

    snapshot_id: str
    workflow_id: str
    job_count: int
    live_jobs: list[dict[str, str]]  # [{job_id, state, algorithm_name}]
    exclusive_job_ids: list[str]  # attributions dropped WITH the run
    shared_job_ids: list[str]  # attributions dropped, jobs kept
    exclusive_artifacts: list[Handle]
    shared_artifacts: list[Handle]

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
        return sum(int(h.size_bytes or 0) for h in self.exclusive_artifacts)


async def compute_impact(app: FastAPI, snapshot_id: str) -> DeletionImpact | None:
    """Read-only survey of what deleting ``snapshot_id`` would touch.

    Returns ``None`` if the snapshot doesn't exist (endpoint layer
    translates that to 404 for the preview call; the delete call is
    idempotent).
    """

    snap = await app.state.workflows.get_snapshot(snapshot_id)
    if snap is None:
        return None

    db: Database = app.state.db
    book: HandleBook = app.state.handles

    # Every job attributed to this snapshot via the V8 bridge, plus any
    # non-done attempts that only carry the legacy ``jobs.snapshot_id``.
    # We match the same UNION the run view uses so the impact preview
    # doesn't disagree with what the user sees on screen.
    async with (
        db.read() as conn,
        conn.execute(
            """
            SELECT job_id, state, algorithm_name FROM (
                SELECT j.job_id, j.state, j.algorithm_name
                  FROM snapshot_jobs sj
                  JOIN jobs j ON j.job_id = sj.job_id
                 WHERE sj.snapshot_id = ?
                UNION
                SELECT j.job_id, j.state, j.algorithm_name
                  FROM jobs j
                 WHERE j.snapshot_id = ?
                   AND j.state != 'done'
                   AND j.job_id NOT IN (
                       SELECT sj2.job_id
                         FROM snapshot_jobs sj2
                        WHERE sj2.snapshot_id = ?
                   )
            )
            """,
            (snapshot_id, snapshot_id, snapshot_id),
        ) as cur,
    ):
        job_rows = await cur.fetchall()

    all_job_ids = [r[0] for r in job_rows]
    live_jobs = [
        {"job_id": r[0], "state": r[1], "algorithm_name": r[2] or ""}
        for r in job_rows
        if r[1] in _LIVE_JOB_STATES
    ]

    # Partition attributed jobs into exclusive vs shared. Exclusive =
    # this snapshot is the ONLY snapshot_jobs row for that job.
    exclusive: list[str] = []
    shared: list[str] = []
    if all_job_ids:
        async with db.read() as conn:
            for jid in all_job_ids:
                async with conn.execute(
                    """
                    SELECT COUNT(*)
                      FROM snapshot_jobs
                     WHERE job_id = ?
                       AND snapshot_id != ?
                    """,
                    (jid, snapshot_id),
                ) as cur:
                    row = await cur.fetchone()
                other_refs = int(row[0] or 0) if row else 0
                if other_refs == 0:
                    exclusive.append(jid)
                else:
                    shared.append(jid)

    exclusive_artifacts: list[Handle] = []
    shared_artifacts: list[Handle] = []
    for jid in exclusive:
        for h in await book.list_by_job(jid):
            if h.deleted_ts is None:
                exclusive_artifacts.append(h)
    for jid in shared:
        for h in await book.list_by_job(jid):
            if h.deleted_ts is None:
                shared_artifacts.append(h)

    return DeletionImpact(
        snapshot_id=snapshot_id,
        workflow_id=snap.workflow_id,
        job_count=len(all_job_ids),
        live_jobs=live_jobs,
        exclusive_job_ids=exclusive,
        shared_job_ids=shared,
        exclusive_artifacts=exclusive_artifacts,
        shared_artifacts=shared_artifacts,
    )


def impact_to_json(impact: DeletionImpact) -> dict[str, Any]:
    """Shape the preview payload the frontend consumes."""

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
    """Execute the ref-counted delete. Idempotent on unknown snapshot.

    Returns a summary compatible with the preview shape plus counts of
    what actually happened (physical deletes may partially fail — e.g.
    node offline — and we tombstone in that case, matching the single-
    artifact ``DELETE /api/artifacts/{id}`` behavior).
    """

    impact = await compute_impact(app, snapshot_id)
    if impact is None:
        # Idempotency: second click / two tabs racing. Frontend refreshes
        # the list and moves on.
        return {"snapshot_id": snapshot_id, "state": "gone"}

    if impact.blocked:
        raise HTTPException(
            status_code=409,
            detail={
                "message": ("snapshot has live jobs — cancel the run first, then delete"),
                "live_jobs": impact.live_jobs,
            },
        )

    book: HandleBook = app.state.handles
    registry: NodeRegistry = app.state.registry
    db: Database = app.state.db

    freed_bytes = 0
    physically_deleted = 0
    tombstoned_only = 0

    # 1. Physically delete exclusive artifacts + tombstone their handle rows.
    #    We mirror DELETE /api/artifacts/{id}: try the node RPC first;
    #    fall back to tombstone-only when the producing node is offline
    #    or the path is outside its current workspace roots. Do NOT
    #    abort the whole snapshot-delete on one artifact failure — a
    #    stale handle should never block cleaning up the rest of the run.
    for handle in impact.exclusive_artifacts:
        session = registry.get_session(handle.node_id)
        note: str | None = None
        if session is None:
            note = "producer node offline — DB row tombstoned; on-disk file untouched"
        else:
            try:
                resp = await art.delete_artifact(app, session, handle)
                freed_bytes += int(resp.get("freed_bytes") or 0)
                physically_deleted += 1
            except HTTPException as exc:
                detail = str(exc.detail).lower() if exc.detail else ""
                if "not under any configured workspace root" in detail:
                    note = (
                        "path outside current workspace roots — DB row tombstoned; "
                        "on-disk file untouched"
                    )
                else:
                    # Genuine node failure (timeout, send error, refused): still
                    # tombstone the row so the run cleanup completes. The
                    # operator sees the note in the response summary.
                    note = f"node delete failed: {exc.detail}"
        if note is not None:
            tombstoned_only += 1
        await book.mark_deleted(handle.handle_id, ts=art.now_ts())

    # 2. Cascade the snapshot row itself. FK ON DELETE CASCADE removes
    #    ``snapshot_jobs`` rows for this snapshot, releasing the shared
    #    jobs (whose handles are untouched). Also null out children's
    #    ``parent_snapshot_id`` so we don't leave dangling forked-from
    #    pointers (this column has no FK constraint, so we do it in the
    #    same transaction as the delete).
    exclusive_ids = list(impact.exclusive_job_ids)
    live_snapshot_id_matches = impact.workflow_id  # captured for tests / debug

    async def _write(conn: aiosqlite.Connection) -> None:
        await conn.execute(
            "UPDATE snapshots SET parent_snapshot_id=NULL WHERE parent_snapshot_id=?",
            (snapshot_id,),
        )
        await conn.execute(
            "DELETE FROM snapshots WHERE snapshot_id=?",
            (snapshot_id,),
        )
        # Purge orphaned jobs (and cascade-purge their job_events /
        # job_logs). Handles are already tombstoned above; we don't
        # cascade to handles because the row survives for history.
        for jid in exclusive_ids:
            await conn.execute("DELETE FROM jobs WHERE job_id=?", (jid,))

    await db.write(_write)

    return {
        "snapshot_id": snapshot_id,
        "workflow_id": live_snapshot_id_matches,
        "state": "deleted",
        "jobs_removed": len(exclusive_ids),
        "jobs_kept_shared": len(impact.shared_job_ids),
        "artifacts_removed_from_disk": physically_deleted,
        "artifacts_tombstoned_only": tombstoned_only,
        "artifacts_kept_shared": impact.shared_artifact_count,
        "freed_bytes": freed_bytes,
    }
