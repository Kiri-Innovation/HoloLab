# col4d/capture-point-cloud 0.1.0

Capture a static `point-cloud` around one `colmap4d-single-cam` timestamp.
Category: `reconstruction/colmap4d`.

| Port | Type | Invocation / fan-out |
| --- | --- | --- |
| `timestamp_cam` | `colmap4d-single-cam`, file | One camera; fan-out maps an input array |
| `4d_points` | `colmap4d-point-cloud`, directory | `scalar: true`; broadcasts the entire cloud to every shard |
| `points` | `point-cloud`, directory | Scalar output; fan-out aggregates into an array |

`time_range_s` defaults to **0.03 seconds**, is finite and nonnegative, and is
explicitly the **half-width**. Keep point center times satisfying:

```
abs(point_t_ns - timestamp_cam.timestamp_ns) <= time_range_s * 1e9
```

Both endpoints are included. Zero selects the exact timestamp. Integer ns are
never cast to float or subtracted in int64; bounds saturate safely at the int64
limits. A fractional ns threshold is floored, preserving this integer-time
inequality. Decimal conversion avoids float multiplication rounding artifacts.

## Contracts and storage

The timed camera contains exactly `model, width, height, params, qvec, tvec,
timestamp_ns`. Validation is copied from `single-cam-drop-time@0.1.0`.
`timestamp_ns` is a signed int64 nanosecond value. **The output of
single-cam-drop-time has no timestamp and cannot feed this node.** Connect the
same chain's upstream `colmap4d-cams-to-array` timed leaves instead.

All source point/time parsing uses **colmap4d** (`read_point_arrays`,
`read_points_t_bin/txt`, `read_time_meta`); the pack contains no binary parser.
Binary is preferred over text. The point component must have `points3D.bin/txt`,
`points_t.bin/txt`, and `time_meta.json` declaring ns and a clock domain. The
camera and point cloud must share the same time origin/domain; the seven-field
camera does not carry separate clock-domain metadata for automatic comparison.

Join timestamps to geometry by **point ID**, never file position. Points without
a center timestamp do not satisfy the predicate and are excluded (with a CLI
warning); time IDs absent from geometry are ignored. Duplicate time IDs fail
strict library validation. Point-array duplicate handling follows colmap4d.

The existing HoloLab `point-cloud` type and triangulation packs use a directory
containing **COLMAP-text `points3D.txt`**. This node writes that file only, with a
`# Number of points:` header that the existing summary/viewer recognizes.
Original IDs, float64 XYZ and uint8 RGB are retained; `.17g` text preserves XYZ
precision. Tracks are empty and the unused reprojection error is zero: there
are no camera observations in this geometry-only output. No camera or time
sidecars are copied. An empty window is a valid zero-point file, not a failure.
Input/output overlap and overwriting nonempty output directories are rejected.

## Running

```
python capture.py --timestamp-cam /path/to/timed-camera.json \
  --points /path/to/colmap4d-points --output /tmp/captured-cloud \
  --time-range-s 0.03
python -m unittest discover -s community_packs/capture-point-cloud@0.1.0 -v
pytest tests/test_capture_point_cloud_fanout.py
```

Use the existing logical `colmap4d` environment with colmap4d>=1.0.4 and numpy.
The library readers load dense source tables into CPU memory on each cold CLI
invocation. Default fan-out concurrency is **1**, with a 24 GB memory advisory;
this is deliberately conservative for the real 41,990,400-point source. No
shared disk cache, global environment changes or extra worker processes are
introduced by the pack. `capture_loaded` also permits a caller to reuse one
library-decoded cloud for several timestamps in a single process.

## Verification

Unit tests cover inclusive endpoints at epoch-scale ns, sub-ns/unit conversion,
int64 limits, empty windows/clouds, text/binary ID joins, missing point times,
input validation and geometry-only output. The isolated HoloLab scheduler test
executes real CLI commands for scalar and fan-out modes on a small fixture and
checks broadcast input paths and aggregate output shape.

`CAPTURE_REAL_PATHS=/tmp/capture-point-cloud-acceptance/real-paths.json` enables
the real 81-camera workflow acceptance. The isolated worker reuses one read-only
colmap4d-decoded cloud to execute all 81 real shards through the production
filter/writer; scalar mode additionally runs the actual CLI cold. This avoids
81 redundant decodes during acceptance while exercising real scheduler/shard
aggregation. No production workflow, node configuration or services are changed.
Acceptance artifacts and optional screenshot are under
`/tmp/capture-point-cloud-acceptance/`.
