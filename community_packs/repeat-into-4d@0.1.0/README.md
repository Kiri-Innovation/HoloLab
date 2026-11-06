# col4d/repeat-into-4d 0.1.0

Repeat a timeless camera into a synthetic `colmap4d-cams` component. This new
package name starts at 0.1.0. It supersedes repeat-4d for new workflows;
repeat-4d 0.1.0/0.2.0/0.3.0 remain in place as legacy archives for historical
references, with their original manifests and implementation unchanged.

Input `camera` is a file tagged `colmap-single-cam`, containing exactly:

```json
{"model":"PINHOLE","width":640,"height":480,"params":[500,510,320,240],"qvec":[1,0,0,0],"tvec":[0,0,0]}
```

These are COLMAP intrinsics and a world-to-camera pose (unit wxyz quaternion).
Validation checks exact fields, model-specific parameter counts, positive
uint64 dimensions and focal lengths, and finite geometry. A timestamp_ns field
is rejected: timed leaves must explicitly pass through single-cam-drop-time.
The separate colmap4d-single-cam type still contains timestamp_ns; this pack
does not redefine it or introduce a colmap-camera alias.

Parameters duration (seconds, default 1), fps (default 30), and start_time_ns
(signed int64 nanoseconds, default 0) are non-optional effective values.
Omitting a parameter uses its default; explicit null is invalid. There is no
fallback to an element timestamp. Every camera shares the same parameter origin.
This deliberately replaces repeat-4d 0.3.0's default-input-time behavior.

Frames = floor(duration * fps), limited to 1..1000000. Timestamp i is
start_time_ns + round(i * 1e9 / fps), using exact decimal arithmetic and
ties-to-even rounding. Overflow fails before writes. Output 4d_cams is a
colmap4d-cams directory with cameras.bin, images.bin, times.bin and
time_meta.json; synthetic times and constant poses, no generated pixels or
point observations. Producer provenance names col4d/repeat-into-4d@0.1.0.

The pack remains arrayable. Enable workflow arrayed_toggle to map over camera
leaves; parallelism defaults to 4 for new nodes and batch_size to 1. Invocation
ports remain arrayed false; the framework collects an outer array preserving
input order and its axis label.

## Migration record and deferred live checks

On 2026-11-06 the five listed workflow drafts were checked; only
9967b1f8-3646-41f5-a269-394b6d392169 referenced repeat-4d. Its latest repeat node
was nmw4dnj3f5 (0.3.0, null origin), not the earlier nmw4d2y6j2.
A fresh base_updated_ts was supplied to POST /api/workflows to migrate it to
repeat-into-4d 0.1.0 with start_time_ns=0, preserving fan-out and other parameters.
The existing drop-time node nmw4dn8kq3.camera was connected to its camera input.
Initial read-back confirmed the new identity, origin and edge. A subsequent
concurrent canvas edit inserted interval-pick nmw4dt5dg6 between drop-time and
repeat-into-4d; that current typed chain was read back and preserved. No snapshots
or jobs were changed by this migration.

The intended type chain is:
colmap4d-cams -> to-array -> arrayed<colmap4d-single-cam> -> drop-time ->
arrayed<colmap-single-cam> -> repeat-into-4d -> arrayed<colmap4d-cams>.
Generic interval-pick can select elements along the array chain without changing
their type. Regenerate upstream outputs if their leaves still use an older type.

Legacy packs remain discoverable: archival is documented, not registry yanking
or physical removal. Historical snapshots and external references have not been
exhaustively migrated, so deleting those versions is unsafe. Nothing was
published, yanked or removed from a registry in this change.

No service restart or live job execution was performed. During an operator-
approved deployment window, verify catalog/runtime availability (colmap4d >=
1.0.4), prepare the entire chain, then check remote shard dispatch, zero/default
and nonzero origins, output ordering/labels and preview. Local success does not
verify the remote transport or production runtime.

## Local verification

```sh
PYTHONPATH=.:../packages/colmap4d/src .venv-runtime/bin/python -m pytest -q tests/test_repeat_into_4d.py tests/test_repeat_4d_fanout.py tests/test_image_undistort_manifest_dim_labels.py
```

Tests cover manifest loading, prepare/render/CLI origin selection, rejection of
null or invalid origins and timed leaves, drop-time-to-repeat CLI interoperability,
COLMAP reader validation, and local scheduler fan-out with actual CLI processes.
