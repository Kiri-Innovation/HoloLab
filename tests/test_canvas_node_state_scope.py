"""Regression: canvas node state must be scoped to the visible snapshot.

The bug this defends against
----------------------------

Before this fix ``App.tsx`` seeded ``runtimeByGraphNode`` from
``GET /api/jobs`` on mount. That feed is workflow-scoped and spans every
snapshot — the seed loop wrote

    runtime[j.graph_node_id] = { state: j.state, ... }

for every returned row, last-write-wins on ``graph_node_id``. So on a
workflow whose current snapshot's 23 jobs were all ``done``, an older
snapshot's failed frame-extraction jobs (same graph_node_id, different
snapshot) would overwrite the current-done state → canvas rendered red
while Run History's "当前" chip pointed at a green snapshot. The
arrayed<T> fan-out (22 jobs per graph_node_id on one snapshot alone)
made the same collision recur inside a single snapshot.

The fix (see canvas/nodeRuntime.ts + App.tsx + SnapshotCanvas.tsx):

* Node runtime is DERIVED from the visible snapshot's ``jobs`` array —
  ``viewingSnapshot?.jobs ?? latestSnapshotJobs`` — via
  ``aggregateJobsToRuntime`` in canvas/nodeRuntime.ts.
* Aggregation rules for fan-out (2026-09 update — priority inverted so
  live progress isn't masked by stale failure): any-in-flight → running;
  else any-failed → failed; else all-done → done. The ``running`` case
  still surfaces the first failed shard's reason via ``fail_reason`` so
  the tooltip reads "running · <reason>", but the node border stays live.
* WS ``job_update`` merges into ``latestSnapshotJobs`` in place; the
  useMemo picks up the change and re-aggregates.
* The old flat map is gone — the getRecentJobs mount seed no longer
  writes runtime state.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_TSX = REPO_ROOT / "hololab" / "frontend" / "src" / "App.tsx"
NODE_RUNTIME_TS = REPO_ROOT / "hololab" / "frontend" / "src" / "canvas" / "nodeRuntime.ts"
SNAPSHOT_CANVAS_TSX = REPO_ROOT / "hololab" / "frontend" / "src" / "canvas" / "SnapshotCanvas.tsx"


# ---------------------------------------------------------------------------
# nodeRuntime.ts — the aggregation helper module
# ---------------------------------------------------------------------------


def test_node_runtime_module_exists_and_exports_aggregator() -> None:
    """The load-bearing shared helper. Deleting or renaming it silently
    would let the buggy last-write-wins loop grow back in App.tsx and
    SnapshotCanvas.tsx.
    """

    assert NODE_RUNTIME_TS.is_file(), (
        f"missing {NODE_RUNTIME_TS} — canvas node runtime aggregation must "
        "live in a shared helper so App.tsx (draft view) and "
        "SnapshotCanvas.tsx (snapshot view) can't diverge on the fan-out "
        "aggregation rules."
    )
    body = NODE_RUNTIME_TS.read_text(encoding="utf-8")
    assert "export function aggregateJobsToRuntime" in body, (
        "nodeRuntime.ts must export aggregateJobsToRuntime — that's the "
        "public surface App.tsx and SnapshotCanvas.tsx both call."
    )


def test_aggregation_rules_present() -> None:
    """The fan-out aggregation states are all handled:
    running / assigned / pending / failed / done.

    Priority order is asserted in the vitest sibling
    ``nodeRuntime.test.ts`` (behaviour test, not source-shape test): the
    invariant that in-flight beats failed lives there because a regex
    over the source would ossify the exact if-order syntax without
    catching whether the semantics actually match.
    """

    body = NODE_RUNTIME_TS.read_text(encoding="utf-8")
    # Failed handled.
    assert re.search(r'"failed"', body), "failed state must be emitted"
    assert re.search(r'\.state\s*===\s*"failed"', body), (
        "aggregator must check state === 'failed' explicitly"
    )
    # In-flight set covers running/assigned/pending.
    for s in ("running", "assigned", "pending"):
        assert f'"{s}"' in body, f"in-flight state {s!r} missing from aggregator"
    # Done rollup.
    assert re.search(r'"done"', body), "done state must be emitted"
    # The in-flight branch must execute BEFORE the failed branch — this is
    # the 2026-09 priority change ("live progress trumps stale failure"),
    # kept as a source-shape assertion because a regression to the old
    # ``if (hasFailed) return "failed"; if (hasInFlight) return "running";``
    # ordering wouldn't crash a test suite that doesn't hit the mixed
    # in-flight+failed shape — the vitest sibling covers behaviour but
    # this pytest guards the syntactic ordering that ships to prod. The
    # in-flight branch grew a nested parent-terminal short-circuit in
    # 2026-09-26 (see the sibling ``parent FAILED with shards stranded
    # PENDING → aggregate stays failed`` vitest), so we match the branch
    # opener + any body rather than the one-liner return.
    in_flight_branch = re.search(r"if\s*\(\s*hasInFlight\s*\)", body)
    failed_branch = re.search(r'if\s*\(\s*hasFailed\s*\)\s*return\s+"failed";', body)
    assert in_flight_branch is not None and failed_branch is not None, (
        "expected explicit priority branches for hasInFlight and hasFailed "
        "in canvas/nodeRuntime.ts — did the aggregator get refactored?"
    )
    assert in_flight_branch.start() < failed_branch.start(), (
        "in-flight branch must come before failed branch in "
        "canvas/nodeRuntime.ts aggregator — otherwise a fan-out with early "
        "shard failures + shards still running paints the node red while "
        "progress ticks up, exactly the bug this priority swap fixed."
    )
    # ...and the in-flight branch must actually still return "running" on
    # the happy path (a refactor that dropped the string would silently
    # break the "fresh dispatch reads as running" contract).
    assert re.search(
        r"if\s*\(\s*hasInFlight\s*\)\s*\{[^}]*return\s+\"running\"", body, re.DOTALL
    ), (
        "in-flight branch no longer returns 'running' — regressed the "
        "'live progress trumps stale failure' aggregate."
    )


# ---------------------------------------------------------------------------
# App.tsx — runtimeByGraphNode is derived, not seeded from workflow-scoped feed
# ---------------------------------------------------------------------------


def test_app_does_not_seed_runtime_from_recent_jobs() -> None:
    """The ``getRecentJobs`` mount handler must NOT call
    ``setRuntimeByGraphNode``. That seed loop, iterating a workflow-scoped
    feed with last-write-wins on graph_node_id, was the direct cause of the
    "canvas red but latest snapshot green" bug.

    Even the setter should not exist any more — runtime is a useMemo now,
    derived from the visible snapshot's jobs.
    """

    body = APP_TSX.read_text(encoding="utf-8")
    assert "setRuntimeByGraphNode" not in body, (
        "App.tsx still writes to setRuntimeByGraphNode — that setter should "
        "no longer exist. Node runtime must be a useMemo derived from "
        "``viewingSnapshot?.jobs ?? latestSnapshotJobs``; a WS ``job_update`` "
        "should mutate ``latestSnapshotJobs`` in place, not this map."
    )


def test_app_uses_aggregate_helper_for_runtime() -> None:
    """The runtime useMemo must call ``aggregateJobsToRuntime`` — that's
    the only place fan-out aggregation lives. Deriving from any other path
    would reintroduce the last-write-wins collision.
    """

    body = APP_TSX.read_text(encoding="utf-8")
    assert "aggregateJobsToRuntime" in body, (
        "App.tsx must import and call aggregateJobsToRuntime from "
        "./canvas/nodeRuntime — otherwise the derived runtimeByGraphNode "
        "either does the naive collision loop again or picks an arbitrary "
        "job per slot."
    )
    # And the derivation source is the visible snapshot's jobs.
    assert re.search(
        r"aggregateJobsToRuntime\(\s*viewingSnapshot\?\.jobs\s*\?\?\s*latestSnapshotJobs\s*\)",
        body,
    ), (
        "the aggregation source must be ``viewingSnapshot?.jobs ?? "
        "latestSnapshotJobs`` — snapshot view first, else latest snapshot. "
        "Any other source (e.g. jobsById, getRecentJobs) leaks the flat "
        "workflow-wide job feed back into node runtime."
    )


def test_app_ws_update_upserts_into_latest_snapshot_jobs() -> None:
    """WS ``job_update`` must upsert into ``latestSnapshotJobs``: find by
    ``job_id`` → replace on hit, append on miss.

    Why upsert and not "patch only if found": the fan-out dispatch path
    (``execution.py:_execute_fanout_body``) creates shard job rows in a
    background task, serially, AFTER ``dispatch_single_node`` has
    returned. The frontend's ``[latestSnapshotId]`` effect fires
    ``getSnapshot`` at endpoint-return time, so the initial fetch sees
    only the parent job; every shard's first WS frame arrives with no
    row to find. Under a find-only rule those frames dropped silently
    and the node's dot stayed on the previous aggregate until reload —
    the "节点运行完了不会立刻在画布上更新" bug this test defends against.

    Snapshot scoping (the invariant the previous find-only rule was
    accidentally enforcing via array membership) is now explicit: the
    upsert is gated by ``workflow_id`` and ``snapshot_id`` matching the
    currently-tracked latest snapshot, so a stale frame from another
    snapshot of the same workflow (or a different workflow entirely)
    is dropped instead of appended.
    """

    body = APP_TSX.read_text(encoding="utf-8")
    assert "setLatestSnapshotJobs" in body, (
        "App.tsx must have a setLatestSnapshotJobs updater in the "
        "WS handler for job_update to reflect on the canvas."
    )
    # findIndex-then-replace-or-append is the upsert pattern.
    assert re.search(
        r"findIndex\(\s*\(\s*j\s*\)\s*=>\s*j\.job_id\s*===\s*p\.job_id\s*\)",
        body,
    ), (
        "WS job_update must locate the job in latestSnapshotJobs by "
        "job_id — the found index gates the replace-vs-append branch."
    )
    # And the miss branch appends (not returns prev), so shard job
    # rows created after the initial getSnapshot fetch still populate.
    # ``prev.concat`` is the append operator we use for the SnapshotJob;
    # a bare ``return prev`` in the miss path would regress the bug.
    assert "prev.concat(" in body, (
        "WS job_update must append newly-observed jobs (via "
        "``prev.concat(...)``) when findIndex misses — otherwise "
        "fan-out shards created after the snapshot fetch stay invisible "
        "to the aggregator until the user reloads the page."
    )
    # Snapshot-scoping is now explicit rather than implicit via array
    # membership: workflow_id + snapshot_id both must match the current
    # tracked snapshot before we touch the array.
    assert re.search(r"workflowIdRef\.current", body), (
        "WS job_update must gate the upsert on workflowIdRef.current — "
        "otherwise a stale frame from a different workflow leaks into "
        "the current view."
    )
    assert re.search(r"latestSnapshotIdRef\.current", body), (
        "WS job_update must gate the upsert on latestSnapshotIdRef.current — "
        "otherwise a stale frame from a different snapshot of the same "
        "workflow leaks in and poisons the aggregate."
    )


def test_app_refetches_snapshot_when_latest_id_changes() -> None:
    """After single-node dispatch mints a fresh snapshot,
    ``setLatestSnapshotId(newId)`` fires and this useEffect must re-fetch
    the snapshot's jobs — otherwise WS updates for the new jobs drop
    (nothing to find-by-job_id in the array) and the node keeps the
    previous snapshot's aggregate state until the user reloads.
    """

    body = APP_TSX.read_text(encoding="utf-8")
    # The fetch must happen and must be gated on the [latestSnapshotId] dep,
    # so a fresh id (from single-node dispatch) triggers a re-fetch.
    assert "getSnapshot(latestSnapshotId)" in body, (
        "App.tsx must call getSnapshot(latestSnapshotId) — otherwise "
        "single-node dispatches (which bump latestSnapshotId) don't get "
        "the new snapshot's jobs into the array."
    )
    # A useEffect that fires on [latestSnapshotId] and contains the fetch.
    # Walk balanced-paren over the whole file to find useEffect(...) blocks.
    idx = 0
    matched = False
    while True:
        m = re.search(r"useEffect\(", body[idx:])
        if not m:
            break
        start = idx + m.end()
        depth = 1
        i = start
        while i < len(body) and depth > 0:
            ch = body[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        arg = body[start : i - 1]
        idx = i
        # Accept ``latestSnapshotId`` as any dep in the array — extra deps
        # only broaden when the refetch fires (e.g. ``reconnectTick`` after
        # a WS reconnect); the load-bearing invariant is that a fresh id
        # still triggers it.
        if "getSnapshot(latestSnapshotId)" in arg and re.search(
            r"\[[^\]]*\blatestSnapshotId\b[^\]]*\]\s*$", arg.strip()
        ):
            matched = True
            break
    assert matched, (
        "no useEffect(..., [..., latestSnapshotId, ...]) that calls "
        "getSnapshot(latestSnapshotId) — this is what refreshes the jobs "
        "array after single-node dispatch bumps the id."
    )


# ---------------------------------------------------------------------------
# SnapshotCanvas.tsx — snapshot view also uses the aggregation helper
# ---------------------------------------------------------------------------


def test_snapshot_canvas_uses_aggregate_helper() -> None:
    """Snapshot view has the same collision problem — a fan-out slot's
    22 jobs collapse into one Map slot via last-write-wins. Must use the
    same helper so open-a-historical-run also shows the aggregated node
    state. For terminated runs (no in-flight jobs) the aggregator's
    terminal branch still surfaces failed shards as red — the priority
    swap only affects the live case where something is still running.
    """

    body = SNAPSHOT_CANVAS_TSX.read_text(encoding="utf-8")
    assert "aggregateJobsToRuntime" in body, (
        "SnapshotCanvas.tsx must call aggregateJobsToRuntime from "
        "./nodeRuntime — otherwise a fan-out failure in a historical run "
        "is invisible whenever a later shard's done attribution overwrites "
        "the failed one via last-write-wins on graph_node_id."
    )
