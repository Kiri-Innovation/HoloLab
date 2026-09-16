"""JobStateMachine — legal / illegal transitions."""

from __future__ import annotations

import pytest

from hololab.gateway.jobs import (
    IllegalTransition,
    Job,
    JobState,
    JobStateMachine,
    event_from_transition,
)
from hololab.protocol.messages import JobFailReason


def _new_job() -> Job:
    return Job(
        job_id="j1",
        workflow_id="w1",
        snapshot_id=None,
        algorithm_name="demo",
        algorithm_version="0.1.0",
    )


def test_pending_to_assigned_ok() -> None:
    j = _new_job()
    j2 = JobStateMachine.transition(j, JobState.ASSIGNED, node_id="n1")
    assert j2.state is JobState.ASSIGNED
    assert j2.node_id == "n1"


def test_pending_to_running_illegal() -> None:
    with pytest.raises(IllegalTransition):
        JobStateMachine.transition(_new_job(), JobState.RUNNING)


def test_transition_preserves_graph_node_id() -> None:
    """graph_node_id is set at job creation and must survive every transition."""

    j = Job(
        job_id="j1",
        workflow_id="w1",
        snapshot_id="s1",
        algorithm_name="demo",
        algorithm_version="0.1.0",
        graph_node_id="canvas-node-42",
    )
    assigned = JobStateMachine.transition(j, JobState.ASSIGNED, node_id="n1")
    running = JobStateMachine.transition(assigned, JobState.RUNNING)
    done = JobStateMachine.transition(running, JobState.DONE)
    assert assigned.graph_node_id == "canvas-node-42"
    assert running.graph_node_id == "canvas-node-42"
    assert done.graph_node_id == "canvas-node-42"


def test_full_happy_path() -> None:
    j = _new_job()
    j = JobStateMachine.transition(j, JobState.ASSIGNED, node_id="n1")
    j = JobStateMachine.transition(j, JobState.RUNNING, progress_current=0, progress_total=100)
    j = JobStateMachine.transition(j, JobState.DONE)
    assert j.state is JobState.DONE
    assert j.progress_total == 100


def test_done_is_terminal() -> None:
    j = _new_job()
    j = JobStateMachine.transition(j, JobState.ASSIGNED)
    j = JobStateMachine.transition(j, JobState.RUNNING)
    j = JobStateMachine.transition(j, JobState.DONE)
    for target in (JobState.RUNNING, JobState.ASSIGNED, JobState.FAILED, JobState.CANCELLED):
        with pytest.raises(IllegalTransition):
            JobStateMachine.transition(j, target)


def test_running_to_failed_carries_reason() -> None:
    j = _new_job()
    j = JobStateMachine.transition(j, JobState.ASSIGNED)
    j = JobStateMachine.transition(j, JobState.RUNNING)
    j = JobStateMachine.transition(
        j,
        JobState.FAILED,
        fail_reason=JobFailReason.OOM,
        fail_exit_code=137,
        fail_message="cuda oom",
    )
    assert j.state is JobState.FAILED
    assert j.fail_reason is JobFailReason.OOM
    assert j.fail_exit_code == 137


def test_orphaned_recovery_path() -> None:
    j = _new_job()
    j = JobStateMachine.transition(j, JobState.ASSIGNED)
    j = JobStateMachine.transition(j, JobState.ORPHANED)
    j = JobStateMachine.transition(j, JobState.RUNNING)
    assert j.state is JobState.RUNNING


def test_event_from_transition_encodes_delta() -> None:
    j0 = _new_job()
    j1 = JobStateMachine.transition(j0, JobState.ASSIGNED, node_id="n1")
    kind, payload = event_from_transition(j0, j1)
    assert kind == "transition:assigned"
    assert '"to": "assigned"' in payload
    assert '"from": "pending"' in payload
    assert '"node_id": "n1"' in payload
