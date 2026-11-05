# megasam-track 0.4.0

Public input: `frames: video-source | array[frame]<image>`. Public outputs: `4d_cams`, `4d_points`, `4d_images` (unchanged colmap4d component tags).

Standardizes MegaSAM camera trajectories and per-frame point clouds into three scalar colmap4d application components. Points are depth-backprojected XYZ/RGB with source-frame exposure-center nanoseconds and reciprocal COLMAP observations. Binary encoding only. Components must be assembled before validate_full.

Runs the existing Depth-Anything, UniDepth, MegaSAM, RAFT and CVD chain through sharp_repo/per-frame/video_to_colmap.py --convert-only --track_megasam. This use of the tracking wrapper does not run SHARP: Gaussian inference, optimization and completion are outside this pack. Depth NPZ and caches are private scratch, not ports. A private copy of megasam_repo protects native sources from outputs_cvd writes.

Default exposure_metadata requires a JSON file bound to video_sha256, clock_domain and decoded-index exposure centers. pts_estimate explicitly treats actual video PTS as approximate exposure centers; synthetic_uniform requires positive fps_num/fps_den. Skip/cap preserve original decoded indexes. See README.md and docs/colmap4d-components.md in the source repository. Video input is one file or a handle directory containing exactly one video. SDR inputs only; unsupported HDR/color primaries fail before inference. Rotation metadata is not applied. Scratch retains native products for diagnosis and can exceed 40 GB for long clips.

Exposure metadata example (replace hash with the video SHA256):
```json
{"video_sha256":"<sha256>","clock_domain":"capture-clock", "frames":[
  {"decoded_index":0,"mid_exposure_ns":1000000000},
  {"decoded_index":1,"exposure_start_ns":1032000000,"exposure_ns":2000000}
]}
```
Records refer to decoded display order before skip/cap; duplicates, missing selected records and int64 overflow fail. PTS conversion and synthetic timing use rational arithmetic, nearest ties-even. No timestamp is inferred from average FPS. The video hash is checked before inference.

Output layouts: cams/{cameras.bin,images.bin,times.bin,time_meta.json}; points/{points3D.bin,points_t.bin,time_meta.json}; images/images/fNNNNNN__cam0.png. Point IDs are unique over the whole video. Images and observations are retained separately from point geometry, and must travel together to the component assembler. Image/point times remain absolute signed int64 ns, never normalized training time. Invalid/too-small depths are omitted. ERROR=0 is a geometric reprojection residual, not confidence. Empty depth frames remain camera observations with zero points.

Compared with ml-sharp megasam-track@0.3.0 and the withdrawn 0.3.1 draft, this is a new video-input contract, not a compatible replacement for camera_track or SHARP. Existing versions are not edited. It emits neither Gaussian PLY nor an opaque camera_track output. Native NPZ is used only internally. Memory includes the decompressed depth tensor plus one frame of point records; assembly currently loads the sparse model in memory. Large dense exports require adequate RAM and disk.

SDR decode converts declared transfer/primaries to sRGB before tracking. Missing color tags use a recorded BT.709 limited-range assumption in conversion.pixels; HDR, unsupported matrices/primaries/ranges are rejected. Pixel conversion requires the ffmpeg colorspace filter. Input rotation metadata is ignored consistently for both tracking and output pixels.

## Image-array input and exposure time

A flat directory of image leaves is ordered lexicographically by full element filename. `skip` and `max_frames` select this original index sequence, just as they select decoded display-order indexes for video. Mixed video/image directories and nested arrays are rejected. At least two selected images are required. Image arrays are not converted to a temporary video.

Timing precedence is fixed for arrays, independent of the video-only `timestamp_mode`, `fps_num`, `fps_den`:

1. If `exposure_metadata_path` is set, require valid exposure-center JSON using the same `clock_domain` and `frames[{decoded_index, mid_exposure_ns}]` (or exposure_start_ns + exposure_ns) semantics as video. Its identity field is **frames_sha256**, not video_sha256. Invalid/missing selected records or hash mismatch fail; no fallback.
2. Otherwise read selected images' HoloLab standard XMP namespace `https://hololab.dev/ns/rig/1.0/`: `wallClockEpochNs`, `exposureNs`, `timebase`. Center is start + exposure//2; `server_epoch` maps to `hololab_clocksync_server_epoch`. Partial coverage, incomplete fields, mixed clocks, malformed metadata fail. JPEG APP1 and PNG XMP are supported. All selected images must share a clock domain.
3. Only if exposure metadata is absent, require **frames_fps** (optional float, no default, positive; array path only). Use exact rational arithmetic from its decimal representation: `round(original_index * 1e9 / frames_fps)`, nearest ties-even. Record `synthetic_uniform` and `estimated: true`. It does not change the video's three timestamp modes.

Array identity is SHA256 of UTF-8 JSON with `ensure_ascii=False`, `sort_keys=True`, `separators=(',', ':')`, containing the ordered list `[{"name": filename, "sha256": SHA256(file_bytes)}, ...]` for **all** input images before selection. `run.py:frame_identity` computes it. Keep exposure JSON outside the image-array directory. This binding prevents accidentally applying exposure metadata from another frame collection. `selected_decoded_indexes` records original array indexes for continuity with video provenance, not a second timestamp map.

Frames must be single 8-bit RGB/RGBA SDR images with identical dimensions. Embedded ICC profiles are converted to sRGB; untagged images are explicitly assumed sRGB. Alpha is discarded for MegaSAM's RGB input. Preserve the original pixel orientation (no EXIF rotation). XMP is read before canonical PNG conversion. Native tracking, depth backprojection, output bin encoding and SHARP boundaries are unchanged.

Port identifiers follow existing snake_case (`frame_sequence`, `videos_dir`, `colmap_4d`), hence `4d_cams` / `4d_points` / `4d_images`. These are dictionary keys, not Python variable identifiers; shell templates use bracket lookup where needed. This user-authorized first-release contract edit retains version 0.4.0; it does not silently alias old port names.
