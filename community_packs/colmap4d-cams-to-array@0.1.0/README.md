# col4d camera component array converters 0.1.0

`col4d/colmap4d-cams-to-array`: scalar `4d_cams` (`colmap4d-cams`, directory)
→ `cameras` (`arrayed<colmap4d-single-cam>[camera]`, array directory of JSON files).
`col4d/colmap4d-cams-from-array` provides the inverse ports.
The col4d scope follows colmap4d-split and colmap4d-to-array.

Each IMAGE_ID produces one leaf, joining CAMERA_ID for intrinsics. Shared
intrinsics do not collapse poses. Leaves are sorted by signed integer timestamp
then IMAGE_ID; zero-padded filenames preserve this order under interval-pick.

## Unified single-camera contract

A `colmap4d-single-cam` JSON has exactly seven fields: model, width, height,
params, qvec, tvec, timestamp_ns. timestamp_ns is a signed int64 integer in
nanoseconds, never a float, string or boolean. Poses remain world-to-camera,
quaternion wxyz. Time is inside the leaf and available to consumers without
reading sidecars. The deprecated colmap-camera tag is not used by these packs.

`colmap-single-cam` contains exactly the six geometry fields, without time.
Use the separately maintained `col4d/single-cam-drop-time@0.1.0` for explicit
lossy conversion to that type. Timed leaves do not connect directly to legacy
six-field consumers. repeat-4d 0.3.0's timed input and fan-out are maintained
in another session; these converters do not modify or define its scheduling.

## Lossless identity and time restoration

`.camera-view.json` records original IDs, table order, leaf hashes, intrinsics,
time_meta bytes, and a redundant original timestamp snapshot.
`.camera-view/images.bin` retains image records, including names and observations.
The archive is streamed in 1 MiB chunks; observations are never expanded into
Python objects. Disk overhead is approximately the original images.bin size.

from-array validates each leaf's seven-field contract and signed int64 timestamp,
then writes times.bin using **the leaf's timestamp_ns** and original ID/order.
Sidecar time values are not the restoration source. Full-leaf hashes still
require unmodified source leaves: this inverse is not a timestamp-editing node.
Duplicate selections deduplicate by original IMAGE_ID; mixed sources fail.
The original intrinsics table, including unused cameras, is retained.
All three binary files and time_meta.json are byte-identical on a full roundtrip.
Subset observations may refer to points outside this component; no points or
pixels are generated or rewritten.

interval-pick selects indexes, not time intervals. It preserves seven-field leaf
bytes through symlinks, including timestamp_ns, and the links also retain access
to identity provenance for inverse conversion.

## Supported inputs and portability

Input: root cameras.bin, images.bin, times.bin, time_meta.json, nonempty images,
unique IDs, complete time coverage and explicit ns clock_domain. Text COLMAP
tables and empty selections are unsupported. Other files are outside the contract.
The inverse requires this converter's provenance: arbitrary camera JSON lacks
original image/camera IDs. Missing provenance, edited leaves, mixed sources and
changed/truncated selected image records fail.

To move an array, copy hidden JSON and the hidden archive directory with leaves.
Only copying leaves preserves their geometry/time type but loses exact identity
restoration. interval-pick links require the original array to remain available.
The to-array output and restored component themselves do not depend on job source
paths. Outputs must be empty and disjoint. The internal provenance format is v2;
old six-field v1 artifacts must be regenerated from their original component.

## Version and automatic views

These two packs are unpublished 0.1.0 packs corrected in place by explicit user
instruction. No type_capabilities are advertised: the platform Partition schema
accepts only source_exposure_groups, not per-image partitioning. colmap4d-cams is
a distinct type from colmap4d, so there is no exposure_groups view-key collision.
Use explicit conversion nodes until a per-image policy is added separately.

## Verification

From repository root:

```sh
PYTHONPATH=.:../packages/colmap4d/src .venv-runtime/bin/python -m pytest -q tests/test_colmap4d_cams_array.py
PYTHONPATH=.:../packages/colmap4d/src .venv-runtime/bin/python reports/colmap4d-cams-timed-contract-20261106/verify.py
```

The CLI check reads a completed real job, writes fresh artifacts outside the
repository and verifies the existing drop-time converter without modifying it.
No service is restarted. Both packs carry the same independently packaged module;
tests check synchronization.
