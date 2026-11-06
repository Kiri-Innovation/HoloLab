# col4d/repeat-4d 0.2.0

> Legacy archive: retained for historical references and reproducibility.
> For new workflows use `col4d/repeat-into-4d@0.1.0`, which consumes
> `colmap-single-cam` and takes its time origin from `start_time_ns` (default 0).
> Convert timed leaves explicitly with `col4d/single-cam-drop-time@0.1.0`.
> The contract below describes this retained legacy version, not the new node.

Repeat one timeless static camera into a synthetic `colmap4d-cams` component.
This is a format-domain utility in `community_packs`, independent of any
reconstruction algorithm. The requested `repeat-4d` name follows the existing
`col4d` scope; no redundant camera prefix is needed.

## Single-camera contract (for array-view / interval-pick authors)

Input port `camera`: **`colmap-camera`, storage file, arrayed false**.
UTF-8 JSON with exactly these fields:

```json
{
  "model": "PINHOLE",
  "width": 640,
  "height": 480,
  "params": [500, 510, 320, 240],
  "qvec": [1, 0, 0, 0],
  "tvec": [0, 0, 0]
}
```

`model`, `width`, `height`, `params` describe a standard COLMAP Camera.
`params` follows the model's standard parameter ordering and count; dimensions
and focal lengths are positive. `qvec` is a unit **(qw,qx,qy,qz)** quaternion;
`tvec` is translation. Both define **world-to-camera** coordinates:
`x_camera = R(qvec) x_world + tvec`. All numbers must be finite.

This new HoloLab leaf type is needed because `colmap4d-cams` is a collection
component and SHARP's `camera_track` is a reconstruction bundle with frames,
COLMAP and algorithm artifacts, not a scalar pose. It does not change the
colmap4d storage specification or add a framework type registration.

A future `colmap4d-cams` array view should iterate **image records**, join each
record's CAMERA_ID to the cameras table, and emit these JSON leaves. Do not
iterate just CAMERA_ID: several poses may share intrinsics. Remove original
image IDs, names, times and point observations from this timeless projection.
`interval-pick` must retain the `colmap-camera` leaf tag and file bytes; if its
result remains an array, enable `arrayed_toggle: true` on the workflow node.
This pack does not implement the array-view adapter.

## Parallel camera arrays

The manifest declares `arrayable: true`; each invocation still consumes one
file (`camera.arrayed: false`) and produces one directory
(`4d_cams.arrayed: false`). The workflow node must set `arrayed_toggle: true`.
Do not set the input declaration to `arrayed: true`: that would make each
invocation expect an array instead of a single camera JSON.

With `arrayed<colmap-camera>[camera]` input, the framework dispatches one
invocation per camera and collects `arrayed<colmap4d-cams>[camera]` output.
The outer label is inherited from the input (for example `cam` stays `cam`).
Output directories use framework positional IDs and retain input order,
regardless of completion order. Camera/image IDs are local to each component;
this is not a merged COLMAP model.

Recommended new-node defaults are `parallelism: 4`, `batch_size: 1`.
Existing nodes must explicitly enable fan-out and choose parallelism above 1
for concurrent execution. Parameters are shared unchanged across all shards.
In particular, `start_time_ns: 0` starts every camera at zero: simultaneous
static viewpoints have overlapping timestamps, never sequential offsets.
With fan-out disabled, the original single-camera invocation still works.
Version 0.1.0 is retained unchanged for historical references.

## Output and timing

Output `4d_cams`: **`colmap4d-cams`, storage dir, arrayed false**. Root files:

- `cameras.bin`: one shared camera, CAMERA_ID=1.
- `images.bin`: IMAGE_ID=1..N, constant pose, unique `f000000__cam0.png` names,
  empty POINTS2D arrays.
- `times.bin`: uint64 count followed by little-endian (uint32 IMAGE_ID,
  **signed int64 nanoseconds**).
- `time_meta.json`: colmap4d 1.0, ns, mid_exposure convention, explicit
  `synthetic_uniform` clock domain and synthetic conversion provenance.

No pixels or point cloud are fabricated. Image names are future rendering
output names, not claims that pixel files already exist. This component alone
is not a complete model for image-consuming training/assembly.

Parameters: `duration` float seconds (default 1), `fps` float (default 30),
`start_time_ns` integer (default 0). Frame count is **floor(duration × fps)**,
calculated exactly from decimal representations. For example 1.05 × 30 yields
31 frames. A product below one is rejected, not promoted to one. The endpoint
is excluded; no extra frame is appended at `start + duration`.

Time i is `start_time_ns + round(i * 1e9 / fps)` with nearest, ties-to-even
rounding of the rational offset. Start time remains integer throughout, even
near int64 boundaries. Maximum FPS is 1e9 so distinct samples remain distinct
at nanosecond resolution. Output is limited to 1,000,000 frames to bound memory
and disk use. Timestamp overflow is rejected before any output writes.

Prepare validates numeric types, finite values, positive duration/FPS and
individual parameter bounds using the existing HoloLab parameter resolver.
The positive lower bound is the smallest positive float, `5e-324`, because
the manifest currently supports inclusive `min` only. Cross-parameter frame
count, last-time overflow and camera payload checks run at execution entry,
before creating files; the framework has no pack-specific prepare hook.

## Local verification

From the HoloLab checkout:

```sh
PYTHONPATH=.:../packages/colmap4d/src .venv-runtime/bin/python -m pytest -q tests/test_repeat_4d.py tests/test_repeat_4d_fanout.py tests/test_fanout_parallelism.py tests/test_image_undistort_manifest_dim_labels.py
```

Tests validate the manifest schema, actual prepare resolver, decimal frame
count, nanosecond limits, malformed cameras, CLI output and real colmap4d
camera/image/time readers plus strict sidecar validation and full time coverage.
No renderer pack is required.

The fan-out regression uses a temporary database and local scheduler with only
remote dispatch replaced by real local CLI processes. Three distinct cameras
produce three ordered component directories; two shard invocations overlap,
and all three have identical zero-based timestamps. Type resolution verifies
that a named input camera axis is inherited by the collected output.

## Draft migration and deferred live checks (2026-11-06)

Workflow `9967b1f8-3646-41f5-a269-394b6d392169`, node `nmw4d2y6j2`,
was migrated via `POST /api/workflows` with a freshly read `base_updated_ts`:
version `0.2.0`, `arrayed_toggle: true`, `parallelism: 4`, `batch_size: 1`.
The edge `nmw4d1z2s1.out -> nmw4d2y6j2.camera` remains connected;
parameters remain duration 1, fps 30, start_time_ns 0. Historical snapshots
and the retained 0.1.0 pack are unchanged. A read-back reports an array output;
the current draft's upstream axis label is unresolved (empty), so no literal
`camera` label is claimed for that draft. Named axes propagate when resolved.

No service was restarted and no live job was launched. After an operator's
permitted deployment/reload window, verify the compute node advertises 0.2.0
and its required colmap4d runtime, prepare the interval-pick edge, then run a
small array to confirm remote shard dispatch, aggregate output and downstream
preview. Check the upstream camera axis label once its type is resolved.
Local checks do not establish that remote runtime/transport path works.
