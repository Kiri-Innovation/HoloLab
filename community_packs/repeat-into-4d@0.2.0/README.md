# col4d/repeat-into-4d 0.2.0

Repeat a timeless `colmap-single-cam` geometry over the unique timestamps of a
`colmap4d-cams` reference. There are no duration, fps or start_time_ns parameters.
The historical 0.1.0 package remains unchanged.

## Contract and implementation facts

- `manifest.yaml:7` enables fan-out; `manifest.yaml:25` declares
  `timestamp_cameras` as `scalar: true`. Only `camera` maps over the outer axis.
- `repeat_into_4d.py:46` reads only the reference time layer, never its intrinsics
  or poses. `times.bin` is preferred, with `times.txt` fallback. Duplicate image
  IDs are rejected; repeated timestamp values are deduplicated and sorted using
  exact integers. Signed int64 extremes remain exact; empty timelines fail.
- `repeat_into_4d.py:58` reads and validates metadata before writing outputs.
  The entire `time_meta.json` is copied byte-for-byte, including clock_domain,
  conversion provenance and unknown fields. It requires a nonempty clock_domain
  and nanosecond units. No new synthetic clock or provenance is substituted.
- `repeat_into_4d.py:64` loads geometry exclusively from `camera`. Each output
  image uses this geometry and one unique reference timestamp. Output contains
  cameras.bin, images.bin, times.bin and time_meta.json; no pixel observations.
- The reference is trusted as the timeline provider: this pack does not join
  timestamps against reference image IDs or inspect reference geometry. It has
  no point-cloud input, so downstream render checks clock compatibility.

## Canvas migration

Select `col4d/repeat-into-4d@0.2.0` and keep fan-out enabled. Preserve the geometry
wire `single-cam-drop-time.camera -> repeat-into-4d.camera`; add exactly:

```
colmap4d-split.4d_cams -> repeat-into-4d.timestamp_cameras
```

Use the same split's `4d_points` for render-video. Keep render-video fan-out
on: `max_frames=1` applies per shard, so nine geometries render nine preview
frames/videos, not one frame total. Remove old duration/fps/start_time_ns values
when migrating the node. This change does not edit saved workflows or drop-time.

## A800 verification, 2026-11-09 (observed facts)

Real source workflow: `9967b1f8-3646-41f5-a269-394b6d392169`.
Split job: `1029d37f-1853-4ca7-8f44-2ceda55c866e` (`4d_cams`, `4d_points`).
Geometry job: `7b8c4c40-e63f-4f88-9a8a-db9c20519e95` (`camera`, nine leaves).
The suggested `099579ee-...` job is actually an old repeat output, so it was
not used as the timeline source.

Read from real inputs and all nine generated images.bin/times.bin files:
81 source time records, 81 unique timestamps, 9 geometries, **81 frames per
geometry, 729 frames total**. The real reference has no duplicate times;
regression fixtures separately exercise duplicates and out-of-order values.
All nine outputs preserve reference metadata bytes. Reference and point cloud
share this exact clock_domain:

```
synthetic_uniform:d0e38bd0d3faa130f84383ccd82d9eac0c32066c74263f38223aac1a7d691134
```

The upstream clock itself is synthetic; this pack preserves that specific
source clock rather than creating an unrelated synthetic clock.

An isolated gateway database ran the actual fan-out scheduler: nine repeat
shards followed by nine unchanged `vista4d/colmap4d-render-video@0.2.0` shards.
Only node transport was replaced by local manifest-rendered subprocesses.
Renderer used CUDA on A800, native dimensions, exact time matching, stride 1,
max_frames=1 and serial shard dispatch. All 18 shards completed successfully.
This verifies scheduling, broadcast, real CLI execution and render compatibility;
it does not verify production WebSocket transport. Existing workflows were untouched.

From the nine render_report.json files: each preview rendered one frame at
0 ns using 518400 points. First shard: raster 0.3291 s, frame 0.7393 s,
load 1.9362 s, renderer elapsed 2.9772 s. Across nine shards: raster
0.2813–0.3291 s, frame 0.5965–0.7393 s, renderer elapsed 2.4894–2.9772 s.
Renderer elapsed includes loading and encoding, but excludes interpreter/import
startup and scheduler overhead. **Inference:** full 729-frame runtime cannot
be established from these previews; no seven-minute claim is measured here.

Local evidence (temporary, not registry content):
`/tmp/repeat-v02-a800-verification/test_broadcast_and_render0/evidence.json`
indexes all generated outputs and their render reports;
`/tmp/repeat-real-test.log` records the scheduler run.

Regression commands:

```sh
.venv-runtime/bin/python -m pytest -q tests/test_repeat_into_4d_v02.py tests/test_repeat_into_4d_v02_fanout.py tests/test_repeat_into_4d.py tests/test_repeat_4d_fanout.py
REPEAT_REAL_PATHS=/tmp/repeat-real-paths.json REPEAT_RENDER_PYTHON=/cloud/cloud-ssd1/envs/kiri/bin/python .venv-runtime/bin/python -m pytest -q -s tests/test_repeat_into_4d_v02_fanout.py
```

REPEAT_REAL_PATHS contains absolute paths keyed by camera, timestamp_cameras,
and points. The optional real-data check expects nine geometry leaves.
No services were restarted. `/api/jobs?limit=1000` had no active jobs before
work. Pack discovery was refreshed without restart. Nothing was published to
any registry, and no registry cleanup was necessary.
