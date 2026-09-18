"""Job state machine.

States and legal transitions:

    pending  ─► assigned ─► running ──► done
                    │           │
                    │           ├──► failed
                    │           └──► cancelled
                    │
                    └────────────► cancelled  (before running)

An ``orphaned`` marker layer exists on top: any job in ``assigned`` or
``running`` whose owning node's session dropped is set ``orphaned``. Re-sync on
node reconnect resolves the orphan (either back to ``running`` or to
``failed``).

The transition function :func:`JobStateMachine.transition` is a pure predicate
- persistence and events are handled by the caller.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from hololab.protocol.messages import JobFailReason


class JobState(str, Enum):
    """Every legal job state. Values are stored verbatim in SQLite.

    ``orphaned`` vs ``interrupted`` — both mark work that couldn't complete,
    but they name different fault modes:
      * ``orphaned`` — the node's WS session dropped mid-flight while the
        gateway was alive. The job may resume if the node reconnects and
        the subprocess is still running (state can go orphaned → running).
      * ``interrupted`` — the gateway itself restarted with the job still
        in-flight. When the gateway wakes it can no longer trust any
        pending/assigned/running row: the subprocess may or may not still
        exist, and there's no reliable protocol to reconcile. Terminal.
    """

    PENDING = "pending"  # queued, waiting for a node
    ASSIGNED = "assigned"  # accepted by a node (job_ack received)
    RUNNING = "running"  # started (first job_progress or first log line)
    DONE = "done"  # completed successfully
    FAILED = "failed"  # non-zero exit or system error
    CANCELLED = "cancelled"  # user-requested stop
    ORPHANED = "orphaned"  # owning node dropped; may re-sync
    INTERRUPTED = "interrupted"  # gateway restarted mid-flight; terminal


# Adjacency list of legal state transitions.
_LEGAL: dict[JobState, frozenset[JobState]] = {
    JobState.PENDING: frozenset(
        {JobState.ASSIGNED, JobState.CANCELLED, JobState.FAILED, JobState.INTERRUPTED}
    ),
    JobState.ASSIGNED: frozenset(
        {
            JobState.RUNNING,
            JobState.CANCELLED,
            JobState.FAILED,
            JobState.ORPHANED,
            JobState.INTERRUPTED,
        }
    ),
    JobState.RUNNING: frozenset(
        {
            JobState.DONE,
            JobState.FAILED,
            JobState.CANCELLED,
            JobState.ORPHANED,
            JobState.INTERRUPTED,
        }
    ),
    JobState.ORPHANED: frozenset(
        {JobState.RUNNING, JobState.FAILED, JobState.CANCELLED, JobState.INTERRUPTED}
    ),
    JobState.DONE: frozenset(),
    JobState.FAILED: frozenset(),
    JobState.CANCELLED: frozenset(),
    JobState.INTERRUPTED: frozenset(),
}


@dataclass
class Job:
    """A snapshot-materialized job.

    ``graph_node_id`` — the ID of the workflow-graph node this job belongs
    to (see docs/workflow-schema.md). It lets the frontend map WS job events
    back to the canvas node without having to reconstruct the mapping from
    (workflow, algorithm, order). Ad-hoc jobs (POST /api/jobs/run without a
    workflow) leave it unset.
    """

    job_id: str
    workflow_id: str
    snapshot_id: str | None
    algorithm_name: str
    algorithm_version: str
    params: dict[str, Any] = field(default_factory=dict)
    input_handles: dict[str, str] = field(default_factory=dict)  # port → handle_id
    state: JobState = JobState.PENDING
    node_id: str | None = None
    graph_node_id: str | None = None
    progress_current: int | None = None
    progress_total: int | None = None
    fail_reason: JobFailReason | None = None
    fail_exit_code: int | None = None
    fail_message: str | None = None
    created_ts: float = field(default_factory=time.time)
    updated_ts: float = field(default_factory=time.time)
    # Populated only for rerun-from-node reuse. Points at the original
    # job whose output handles this row is inheriting. When non-null
    # the executor does NOT dispatch this job — it's already done at
    # creation time and downstream reads the old outputs through it.
    reused_from_job_id: str | None = None
    # Set on *shard* jobs (arrayed<T> fan-out) — points at the parent
    # job that coordinates this shard's siblings. NULL on parent jobs
    # and on all regular (non-fan-out) jobs. See docs/pack-spec.md
    # #arrayed-and-arrayable.
    parent_job_id: str | None = None
    # Set on shard jobs — the sorted subdir name (element key) this
    # shard is processing (e.g. ``cam_A`` for a per-camera fan-out).
    # NULL on parent + regular jobs.
    shard_element_id: str | None = None


class IllegalTransition(RuntimeError):
    """Raised when a state transition is not permitted."""


class JobStateMachine:
    """Pure state-transition predicate (no side effects).

    Callers own persistence and event emission — this class only says whether
    a transition is legal and computes the resulting :class:`Job` value.
    """

    @staticmethod
    def can_transition(current: JobState, target: JobState) -> bool:
        """Return ``True`` iff ``current → target`` is allowed."""

        return target in _LEGAL[current]

    @staticmethod
    def transition(
        job: Job,
        target: JobState,
        *,
        node_id: str | None = None,
        progress_current: int | None = None,
        progress_total: int | None = None,
        fail_reason: JobFailReason | None = None,
        fail_exit_code: int | None = None,
        fail_message: str | None = None,
    ) -> Job:
        """Compute the post-transition :class:`Job`.

        Raises:
            IllegalTransition: if the transition is not permitted.
        """

        if not JobStateMachine.can_transition(job.state, target):
            raise IllegalTransition(
                f"illegal transition for job {job.job_id}: {job.state.value} → {target.value}"
            )

        new = Job(
            job_id=job.job_id,
            workflow_id=job.workflow_id,
            snapshot_id=job.snapshot_id,
            algorithm_name=job.algorithm_name,
            algorithm_version=job.algorithm_version,
            params=dict(job.params),
            input_handles=dict(job.input_handles),
            state=target,
            node_id=node_id if node_id is not None else job.node_id,
            graph_node_id=job.graph_node_id,
            progress_current=progress_current
            if progress_current is not None
            else job.progress_current,
            progress_total=progress_total if progress_total is not None else job.progress_total,
            fail_reason=fail_reason if fail_reason is not None else job.fail_reason,
            fail_exit_code=fail_exit_code if fail_exit_code is not None else job.fail_exit_code,
            fail_message=fail_message if fail_message is not None else job.fail_message,
            created_ts=job.created_ts,
            updated_ts=time.time(),
            reused_from_job_id=job.reused_from_job_id,
            parent_job_id=job.parent_job_id,
            shard_element_id=job.shard_element_id,
        )
        return new


def event_from_transition(prev: Job, curr: Job) -> tuple[str, str]:
    """Render a persistable event record: ``(kind, payload_json)``.

    The gateway persists these via the ``job_events`` table so the full job
    history can be replayed for recovery views.
    """

    payload: dict[str, Any] = {"to": curr.state.value, "from": prev.state.value}
    if curr.node_id != prev.node_id:
        payload["node_id"] = curr.node_id
    if curr.progress_current != prev.progress_current or curr.progress_total != prev.progress_total:
        payload["progress"] = [curr.progress_current, curr.progress_total]
    if curr.fail_reason is not None and curr.fail_reason != prev.fail_reason:
        payload["fail_reason"] = curr.fail_reason.value
        payload["fail_exit_code"] = curr.fail_exit_code
        payload["fail_message"] = curr.fail_message
    return f"transition:{curr.state.value}", json.dumps(payload)
