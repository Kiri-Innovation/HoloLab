"""Fan-out parent inherits infra-class fail_reason from its shards.

Before this: any shard-authored failure in a fan-out marked the parent
``USER_ERROR`` — even when every shard failed with the same
``gateway unreachable`` transport error during a CPU-starved gateway
episode. That misled retry policy (transient blamed on the workflow)
and painted the wrong colour on the run history.

Now: :func:`_aggregate_parent_fail_reason` inspects the collected
shard reasons and picks ``SYSTEM_ERROR`` iff every failure was
infrastructure-class (``SYSTEM_ERROR`` or ``OOM``). Any mixed or
user-authored failure downgrades the aggregate back to ``USER_ERROR``
so real bad-input cases aren't misrouted as transient.
"""

from __future__ import annotations

from hololab.gateway.execution import _aggregate_parent_fail_reason
from hololab.protocol.messages import JobFailReason


def test_all_transport_failures_become_system_error() -> None:
    """The bug scenario: 100 shards all failed with the same gateway
    unreachable error. The parent must be flagged SYSTEM_ERROR so the
    UI + retry classifier don't blame the workflow.
    """

    reasons = [JobFailReason.SYSTEM_ERROR] * 100
    assert _aggregate_parent_fail_reason(reasons) is JobFailReason.SYSTEM_ERROR


def test_all_oom_failures_become_system_error() -> None:
    """OOM is infra-class (host doesn't have enough RAM), not user-class."""

    reasons = [JobFailReason.OOM, JobFailReason.SYSTEM_ERROR, JobFailReason.OOM]
    assert _aggregate_parent_fail_reason(reasons) is JobFailReason.SYSTEM_ERROR


def test_mixed_failures_default_to_user_error() -> None:
    """One real bad-input case among transient failures must downgrade
    the aggregate. Otherwise a user-authored bug hidden behind a
    flaky gateway would be marked "retry me" and lie to operators.
    """

    reasons = [JobFailReason.SYSTEM_ERROR, JobFailReason.USER_ERROR]
    assert _aggregate_parent_fail_reason(reasons) is JobFailReason.USER_ERROR


def test_all_user_failures_stay_user_error() -> None:
    """The pre-fix default — every shard hit the same bad input,
    parent inherits USER_ERROR."""

    reasons = [JobFailReason.USER_ERROR] * 3
    assert _aggregate_parent_fail_reason(reasons) is JobFailReason.USER_ERROR


def test_all_algo_failures_stay_user_error() -> None:
    """Algorithm/subprocess non-zero exits are workflow-authored, not
    infra. They must not be misrouted as transient."""

    reasons = [JobFailReason.ALGO_ERROR] * 4
    assert _aggregate_parent_fail_reason(reasons) is JobFailReason.USER_ERROR


def test_empty_list_falls_back_to_user_error() -> None:
    """Defensive: we only call the helper when there's at least one
    failure, but if a caller somehow passes an empty list the fallback
    is the historical default rather than an assertion.
    """

    assert _aggregate_parent_fail_reason([]) is JobFailReason.USER_ERROR
