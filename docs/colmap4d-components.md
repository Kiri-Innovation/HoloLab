# colmap4d components — application profiles v1

These HoloLab application profiles compose the frozen colmap4d v1.0 format.
They do not amend its specification. A component is **not a complete model**:
validate components first, assemble all three, then call `validate_full` with
strict warnings. The base format still permits absent/partial time sidecars;
these producer profiles require complete time coverage.

## Layout and identity

```text
cams/                         points/                       pixels/
  cameras.bin                   points3D.bin                  images/<NAME>
  images.bin                    points_t.bin
  times.bin                     time_meta.json
  time_meta.json
```

Readers also accept standard `.txt` counterparts, preferring `.bin` when both
exist. MegaSAM writes binary only. Component roots contain the model tables;
there is no second authoritative copy under `sparse/0`. Dot-prefixed provenance
is allowed. Pixel payloads belong exclusively to `colmap4d-images`.

All IDs and COLMAP records retain their standard types and meanings. Camera
records store intrinsics. Image records store world-to-camera quaternion
`(qw,qx,qy,qz)`, translation, CAMERA_ID, NAME and optional POINTS2D observations.
Point records store globally unique POINT3D_ID, XYZ, RGB, ERROR and tracks.
Images reference existing cameras. Nonempty point tracks and image observations
must be reciprocal, including zero-based POINT2D_IDX. No fictitious observations
may be added. Empty tracks are allowed only without a track-derived time claim.

## colmap4d-cams

This extends the geometry of `colmap-cams`, without adding columns to COLMAP
tables. `times` maps every IMAGE_ID to exactly one signed int64 nanosecond
timestamp. Coverage must equal the image ID set; duplicates and dangling IDs
are producer errors. Time belongs to the observation, not shared CAMERA_ID.
An image NAME is a unique safe relative POSIX path, without `..`, absolute
paths or backslashes, resolved below the separate pixel component's `images/`.

## colmap4d-point-cloud

`points_t` maps every POINT3D_ID to exactly one signed int64 nanosecond timestamp.
IDs are unique across the whole sequence, never reset per frame. MegaSAM's
per-frame depth-backprojected XYZ/RGB samples are the source of this component;
each point receives its source image's exposure-center time. Repeated spatial
locations at different times remain separate samples. These are points, not
Gaussians: opacity, scale, rotation, SHARP optimization and completion are absent.

For a real single-source observation, record that pixel in the image's POINTS2D
and a reciprocal one-element point track. Its time is exactly the image time;
the existing `points_t_method: track_centroid` applies. ERROR=0 denotes exact
backprojection/reprojection geometry, not depth confidence. Multi-observation
track-centroid producers (such as a future timestamped RoMa adapter) use the
integer rounded mean `(sum(times)+len(times)//2)//len(times)`. They must not
replace unequal observation times by a group filename's time. Empty-track
points require independent time provenance and must not claim track_centroid.
Point time is not a lifespan, Gaussian sigma, or static/dynamic label.

## colmap4d-images

`images/<NAME>` and cams' image NAMEs form a strict bijection: no missing or
extra files, duplicate NAMEs or aliases. Non-image metadata belongs outside
`images/`. Every image must fully decode as a single 8-bit RGB or RGBA image;
its pixel dimensions must match the referenced camera's WIDTH/HEIGHT.
Pixels are SDR sRGB (RGB primaries/transfer, not BGR, linear floats or untreated
HDR). RGBA alpha is straight/unassociated coverage; it does not imply depth
validity or dynamic segmentation. MegaSAM emits RGB PNG. Producers are
responsible for color conversion; validators can check modes, decoding and
dimensions, not infer the historical transfer function from pixels alone.
Pixel payloads carry no second authoritative time map: join NAME→IMAGE_ID→times.
Materialized outputs must remain usable independently of upstream/scratch paths.

## Time storage and metadata

Use unmodified colmap4d sidecars: text `ID T_NS`, or little-endian binary uint64
count followed by `(uint32 IMAGE_ID,int64 T_NS)` / `(uint64 POINT3D_ID,int64 T_NS)`.
No float seconds, normalized training time, filename-derived time or stored t0.
`time_meta.json` declares `colmap4d_spec: "1.0"`, `time_unit: "ns"`,
`time_convention: "mid_exposure"`, and a nonempty `clock_domain`.
The two metadata objects are recursively merged only when overlapping fields
agree; incompatible clock domains, source identities or device assignments fail.
Unknown optional fields are retained, as required by the frozen base format.
An equal clock-domain string alone is not proof that independent sources share
an origin: producers must establish that before pairing components.

MegaSAM supports three explicit adapter policies (not new protocol conventions):

* `exposure_metadata` (default): a JSON side input with `video_sha256`,
  `clock_domain`, and `frames` records containing `decoded_index` plus either
  `mid_exposure_ns` or `exposure_start_ns` and nonnegative `exposure_ns`.
  Center = start + exposure_ns//2. IDs refer to zero-based decoded display-order
  frames before skip/max_frames. All selected frames require a record. A record
  must choose exactly one timing representation; duplicate indexes fail.
* `pts_estimate`: explicit assumption that presentation PTS estimates exposure
  center, recorded in optional `conversion.timestamp_mode` and `estimated`.
  Convert actual integer PTS by rational stream time_base to ns; do not replace
  variable-rate intervals with average FPS or silently use best-effort timestamps.
* `synthetic_uniform`: explicitly supplied positive integer `fps_num/fps_den`;
  center = round(decoded_index * 1e9 * fps_den / fps_num), recorded with
  `clock_domain: synthetic_uniform`. No inference that these are measured times.

Rational rounding uses nearest integer, ties to even. Selection retains original
frame indices and timestamps. In pts mode, clock_domain identifies the video's
PTS domain by its SHA256. All modes retain video identity and selected indexes
as optional provenance, without a second T_NS table. No `presentation` value is
added to the frozen time_convention. Legacy SHARP NPZ files lack sufficient
timing provenance by themselves.

Image-array adapter extension (2026-11-05): `frames` also accepts
`arrayed[frame]<image>`. Timing precedence is explicit exposure JSON, then full
HoloLab XMP coverage, then explicitly supplied `frames_fps` synthetic timing.
The exposure records retain the same indexed mid-exposure nanosecond semantics;
array JSON uses `frames_sha256` to bind the complete sorted input collection.
See the README for `k4d/megasam-track@0.4.0` for the canonical hash and rejection rules.
This changes adapter input provenance, not the stored colmap4d time format.

## Assembly and downstream boundaries

Copy selected tables/sidecars to `model/sparse/0/` and pixels to `model/images/`.
Keep IDs, timestamps and coordinates unchanged. Check both directions of tracks,
time coverage, single-source equality / declared track centroid, metadata
compatibility, camera calibration, finite geometry and image bijection before
writing. Source components remain read-only. Run strict full-model validation
after assembly. Full validation alone does not check all geometry or tracks.

Public producer outputs and assembler inputs are `4d_cams`, `4d_points`,
`4d_images` (snake_case port identifiers). Each component is `storage: dir, arrayed: false`, tagged respectively
`colmap4d-cams`, `colmap4d-point-cloud`, `colmap4d-images`; no dim_labels.
Do not add legacy tags to imply compatibility. Whole-model output is scalar
`colmap4d`. Frames/cameras/points are internal records, not framework array axes.

STG consumes the assembled model, requires complete point/image times and
undistorted PINHOLE/SIMPLE_PINHOLE pixels, and normalizes time internally using
the image-time range. That normalization never rewrites storage ns. The present
sampler infers camera identity from NAME; MegaSAM uses `f000000__cam0.png` to
preserve a single-device fallback, and declares cam0 in devices. Single-camera
training would need batch=1. This profile does not modify STG.

`col4d/merge-colmap4d@0.1.5` remains frozen for old XMP-group workflows.
`col4d/merge-colmap4d@0.2.0` is component assembly. The old XMP behavior has a
separate identity `col4d/frames-to-colmap4d@0.1.0`; it derives exposure
centers from JPEG XMP and is not a consumer of these timed components.

Scope convention: `col4d` is the colmap4d format-tool ecosystem (assembly, XMP conversion, temporal grouping); algorithm packs such as `k4d/megasam-track` retain their algorithm scope. `col4d/frames-to-colmap4d@0.1.0` requires existing grouped points/cams as well as XMP JPEG frames; its name does not imply image-only reconstruction. Unpublished identity corrections replace `k4d/merge-colmap4d@0.2.0` and `k4d/colmap4d-from-xmp-photos@0.1.0`; historical snapshots/reports keep their original identities.
