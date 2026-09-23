# Follow-up items

Items intentionally deferred out of the fix that landed them here.
Each entry names the defect, the shape of the fix, and the reason it
was scoped out — so a future author doesn't have to re-derive the
context.

## Fanout `pending` shards stranded when dispatch raises before ASSIGNED

**Context**: `e8fcc72` (`fix(gateway): recover mid-flight WS drops instead
of stranding shards`) covered the WS-drop path end to end for `assigned`
and `running` rows. But a shard whose `_dispatch_prepared_shard` raised
**before** the PENDING→ASSIGNED transition (e.g. `get_session` returned
None during a disconnect window) stays in `pending` forever — the parent
fanout ends up FAILED via the aggregator, but the `pending` row is
never flipped.

**Symptom**: on the 88/100 stall, 8 of the 12 non-done shards
(`frame_0020..0027`) were left in `pending` and never re-dispatched
even after the parent was cancelled.

**Options**:

1. In `_run_one` (`hololab/gateway/execution.py`), catch dispatch-time
   exceptions before re-raising and flip the shard row to FAILED
   (SYSTEM_ERROR) with a `fail_message` naming the dispatch error.
   Small, local, keeps the `_run_one` contract clean.
2. After the fanout aggregator marks the parent FAILED, sweep any
   remaining `pending` children of that parent and flip them to
   CANCELLED. More systemic (also covers non-dispatch stranding
   modes), needs a helper on `JobsStore`.

Option 1 is probably what future-you wants — matches the
`_dispatch_prepared_shard` / `_dispatch_job` send-fail cleanup that
already exists in the same file.

## Shard-level stall detection

**Context**: `_await_job_terminal` (`hololab/gateway/execution.py`)
still polls with the 24 h `job_timeout_s`. A subprocess deadlock (e.g.
NFS wedge, colmap hang) leaves a shard `running` without heartbeat
until the hard timeout fires. Not the WS-drop path — that's fixed —
but the residual "process alive, socket alive, work stopped" case.

**Reason not to bolt on a static `M` seconds**: shard duration varies
by three orders of magnitude across packs — `image-undistort` per
shard is ~20 s, `stg-train` can run for hours. A single `M` misfires
either way.

**Recommended shape**:

* Add `expected_shard_duration_s: float` (optional) to the pack
  manifest schema (`hololab/pack-spec.md`).
* Extend `_await_job_terminal` to record `last_updated_ts` on each
  poll iteration and, if a row is `running` for more than
  `3 * expected_shard_duration_s` **without any `updated_ts` change**,
  transition it to FAILED with a `stalled` reason (new enum
  variant on `JobFailReason`).
* Manifests without the field opt out (falls back to the current
  24 h hard timeout).

This wants a small pack-schema RFC first because it touches
`writing-a-pack.md`, the manifest validator, and every existing pack
that would want the tag.
