# col4d camera component array converters 0.1.0

`col4d/colmap4d-cams-to-array`: scalar `4d_cams` (`colmap4d-cams`, directory)
→ `cameras` (`arrayed<colmap-camera>[camera]`, array directory of JSON files).
`col4d/colmap4d-cams-from-array` provides the inverse ports.
The col4d scope follows colmap4d-split and colmap4d-to-array.

Each IMAGE_ID produces one leaf, joining CAMERA_ID for intrinsics. Shared
intrinsics do not collapse poses. Leaves are sorted by signed integer timestamp
then IMAGE_ID; zero-padded filenames preserve this order under interval-pick.
A leaf has exactly model, width, height, params, qvec and tvec. Poses remain
world-to-camera, quaternion wxyz. It is directly accepted by repeat-4d 0.1.0.

## Time and lossless identity

Choose separate provenance, not extra leaf fields or timestamp filenames:
repeat-4d 0.1.0 rejects additional JSON fields. `.camera-view.json` records
original image/camera IDs, signed int64 nanoseconds, record order, leaf hashes,
intrinsics and exact time_meta bytes. `.camera-view/images.bin` retains original
image records, including names and observations. The archive is streamed with
1 MiB chunks; observations are never expanded into Python objects. This costs
approximately the original images.bin size on disk, not an archive per leaf.

interval-pick is an index stride, not a time resampler. Its symlinks resolve to
original leaves and their hidden provenance; selected IDs/times remain unchanged.
repeat-4d consumes the timeless six-field projection and generates its own
synthetic timeline from its parameters. It does not inherit source timestamps.
The separate fan-out change is outside these packs.

from-array restores selected image identities, deduplicates repeated links to
an identical source identity, and retains the original intrinsics table (including
unused cameras). It restores original record ordering rather than array order.
All three binary files and time_meta.json are byte-identical on a full roundtrip.
Subsets retain exact image observation records, which may still refer to points
outside this component; no points or pixels are generated or rewritten.

## Supported inputs and failure behavior

Input component: root cameras.bin, images.bin, times.bin, time_meta.json, nonempty
images, unique IDs and complete time coverage; explicit ns clock_domain required.
Text COLMAP tables are not supported in this version. Other component files are
not part of the roundtrip contract. Empty selections are rejected.

The inverse is provenance-backed, not an importer for arbitrary timeless camera
JSON: missing times/IDs are never invented. Missing provenance, edited leaves,
mixed source identities, and changed/truncated selected image records fail.
To move an array, copy its hidden JSON **and** hidden archive directory along
with the leaves. Copying just leaf files loses reversibility. interval-pick links
require the original array to remain available, consistent with its own contract.
The to-array output and restored component themselves do not depend on source
job artifacts or use source symlinks. Output directories must be empty and disjoint.

## Automatic array view

No type_capabilities declaration is advertised: the current platform Partition
schema accepts only source_exposure_groups, which would falsely describe this
per-image partition. Supporting a source_images policy needs a separate platform
change before registering a view. colmap4d-cams is a different type from colmap4d,
so there is no type/view-key collision with exposure_groups. Use explicit nodes.

## Local verification

From the repository root:

```sh
PYTHONPATH=.:../packages/colmap4d/src .venv-runtime/bin/python -m pytest -q tests/test_colmap4d_cams_array.py
PYTHONPATH=.:../packages/colmap4d/src .venv-runtime/bin/python reports/colmap4d-cams-array-20261106/verify.py
```

The second command reads the recorded real job component and creates a fresh
artifact directory outside the repository. It does not launch or restart services.
Both packs carry the same conversion module so packaged installs are independent;
the test checks that the copies stay synchronized.
