# megasam-track 0.4.0

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
