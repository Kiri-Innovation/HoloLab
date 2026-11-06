# col4d/repeat-4d 0.3.0

> Legacy archive: retained for historical references and reproducibility.
> For new workflows use `col4d/repeat-into-4d@0.1.0`, which consumes
> `colmap-single-cam` and takes its time origin from `start_time_ns` (default 0).
> Convert timed leaves explicitly with `col4d/single-cam-drop-time@0.1.0`.
> The contract below describes this retained legacy version, not the new node.

Repeat one timed camera into a synthetic `colmap4d-cams` component.
This version changes the input contract and default time origin. Versions
0.1.0 and 0.2.0 retain their original contracts.

Input `camera` is a file tagged `colmap4d-single-cam`, containing exactly:

```json
{"model":"PINHOLE","width":640,"height":480,"params":[500,510,320,240],"qvec":[1,0,0,0],"tvec":[0,0,0],"timestamp_ns":123456789}
```

The first six fields are timeless geometry: COLMAP intrinsics, a unit wxyz
quaternion and world-to-camera translation. Dimensions are positive uint64;
model parameter counts and positive focal lengths are validated. Numeric
geometry must be finite. `timestamp_ns` is required, an integer in
[-9223372036854775808, 9223372036854775807]; booleans, floats, strings, null,
missing fields and additional fields are rejected. The leaf is self-contained;
no sidecar lookup or inference from filenames is performed.

`duration` (seconds, default 1) and `fps` (default 30) remain shared parameters.
`start_time_ns` is now optional, default null:

- Missing or null: each invocation starts at its input `timestamp_ns`.
- Explicit integer, including zero: overrides the origin for every invocation.

Frames = floor(duration * fps), limited to 1..1000000. Timestamp i is origin +
round(i * 1e9 / fps), with exact decimal arithmetic and ties-to-even rounding.
Overflow is rejected before output writes. Synthetic timing metadata remains
synthetic; the seven-field input does not carry a source clock-domain declaration.

The manifest uses `arrayable: true`; workflow `arrayed_toggle: true` maps over
one outer camera axis. Invocation input and output retain `arrayed: false`.
The framework collects `arrayed<colmap4d-cams>[camera]`, inheriting the input
outer label and order. Recommended parallelism is 4, batch size 1. Each output
component contains cameras.bin, images.bin, times.bin and time_meta.json;
no pixels or point observations are generated.

`col4d/single-cam-drop-time@0.1.0` explicitly converts the timed input to
`colmap-single-cam` geometry. Its output cannot feed repeat-4d 0.3.0 because it
has deliberately discarded the required time. New contracts do not use
`colmap-camera` as an alias.

## Coordinated workflow migration

Do not upgrade only repeat-4d in workflow
`9967b1f8-3646-41f5-a269-394b6d392169`. This change leaves the draft untouched.
Before migration, all required nodes must be available on the assigned node:

1. A coordinated `colmap4d-cams-to-array` version whose output is
   `arrayed<colmap4d-single-cam>[camera]`, with timestamp_ns inside every JSON.
2. `repeat-4d@0.3.0` and the existing generic `interval-pick`.
3. A matching `colmap4d-cams-from-array` if the selected array also feeds a
   restoration branch. The drop-time node is needed only for geometry consumers.

After the operator permits deployment, verify catalog versions and runtime
(colmap4d >= 1.0.4), then re-read the latest draft and migrate the connected
producer/consumer chain together using POST /api/workflows with base_updated_ts.
Keep fan-out enabled. Node `nmw4d2y6j2` currently has explicit start_time_ns=0:
retaining it preserves synchronized zero-based timing even in 0.3.0. To adopt
per-element source times, remove that override or set it to null in the same
migration. Do not reinterpret old six-field artifacts as timed leaves; regenerate
the upstream output. Preserve historical snapshots and old pack versions.

Deferred live checks: prepare the full edge chain, run a small timed array,
confirm shard count/concurrency, source-time versus zero-override behavior,
aggregate ordering/labels and output preview. Test any restoration/geometry
branches separately. No service restart or live execution is part of this change.

## Local verification

From the HoloLab checkout:

```sh
PYTHONPATH=.:../packages/colmap4d/src .venv-runtime/bin/python -m pytest -q tests/test_repeat_4d.py tests/test_repeat_4d_v03.py tests/test_repeat_4d_fanout.py tests/test_image_undistort_manifest_dim_labels.py
```

Tests cover strict input validation, signed int64 boundaries, prepare and manifest
command rendering, actual CLI execution, optional overrides, geometry conversion,
and local scheduler fan-out with distinct source times. Remote dispatch is
replaced by local CLI processes in the scheduler test; production services are
not involved.
