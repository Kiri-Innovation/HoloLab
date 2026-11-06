# col4d/repeat-4d 0.1.0

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
result remains an array, select a scalar element before connecting this port.
This pack does not implement the array-view adapter.

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

From the outer checkout:

```sh
PYTHONPATH=hololab:packages/colmap4d/src hololab/.venv-runtime/bin/python -m pytest -q hololab/tests/test_repeat_4d.py
```

Tests validate the manifest schema, actual prepare resolver, decimal frame
count, nanosecond limits, malformed cameras, CLI output and real colmap4d
camera/image/time readers plus strict sidecar validation and full time coverage.
No renderer pack is required.
