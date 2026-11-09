# merge-cams-array 0.1.0

Connect repeat-into-4d's collected output to `cameras`, then connect `4d_cams`
to vista4d/colmap4d-render-video's scalar `4d_cams`. Leave this merge node's
fan-out disabled. Input axis is `camera`, matching the supplied aggregate-view
snapshot and the camera converter's manifest axis. The filesystem itself does
not encode axis labels; the live workflow snapshot was not independently read.

Binary IO uses the installed colmap4d library (source: ../packages/colmap4d).
Stable order is timestamp, lexical shard name, original IMAGE_ID. IMAGE_IDs
become 1..N; times are keyed by those new IDs without changing timestamps.
CAMERA_IDs occupy a new global namespace, with exact model/dimensions/parameter
matches deduplicated. Different intrinsics remain separate and references are
remapped, rather than approximated. Image names receive a shard prefix.
Point references are rejected because this node cannot remap a point model.

Metadata requires matching spec, ns unit, exposure convention and clock domain.
These are preserved; conversion records source metadata and recomputed counts
and time bounds. Device mappings are retained only inside source provenance,
not copied with stale IDs. Incompatible clocks must be aligned upstream.
Renderer supports only PINHOLE/SIMPLE_PINHOLE; mixed sizes require explicit
render width/height. Its point input must independently match clock/world space.

## Verification (2026-11-09)

Real source: /cloud/cloud-ssd2/hololab/w/9967b1f8-3646-41f5-a269-394b6d392169/j/099579ee-f93c-4946-8160-7e2603911b85/4d_cams

Output: /tmp/hololab-merge-cams-validation

9 shards, 30 poses each, 270 output poses; one deduplicated intrinsic.
Timestamp range 0..966666667 ns. Library read-back verified IMAGE_IDs 1..270,
exact image/time key coverage and nondecreasing timestamps.
Actual render pack run.py `cameras()` and `camera_matrices(...,64,64)` accepted
all 270 output poses. This is camera-input acceptance, not a rendered video or
full two-input execution. No live workflow was edited.

`python community_packs/merge-cams-array@0.1.0/test_merge.py` passes: colliding
IDs, interleaved timestamps, stable ties, equal/different intrinsics and clock
mismatch rejection. Manifest passes hololab.manifest.load_manifest.

repeat-into-4d requires no declaration change: its scalar output describes one
invocation; fan-out collects outputs on the input outer axis. No existing pack,
service or remote registry was changed.
