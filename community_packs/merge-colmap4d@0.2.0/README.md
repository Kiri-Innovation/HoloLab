# col4d/merge-colmap4d 0.2.0

Three scalar application components, not grouped arrays. Layout: cams/{cameras,images,times,time_meta}; points/{points3D,points_t,time_meta}; pixels/images/<NAME>. Accepts bin/txt tables (bin preferred) and emits binary under model/sparse/0. Retains all IDs and exact int64 nanoseconds. No JPEG XMP time derivation.

Checks complete time coverage, duplicate IDs, safe unique image names, pixel bijection and resolution, reciprocal observations/tracks, source-frame point time equality and track_centroid mean. Both metadata objects must declare the same spec/unit/convention/clock domain; recursively overlapping fields must agree. Unknown metadata is preserved. Single 8-bit RGB/RGBA SDR sRGB images are required; pixel decoding cannot prove historical color conversion. Finishes with colmap4d validate_full and strict warning handling. Entire model is loaded in RAM; provision memory for dense point clouds.

Components cannot masquerade as complete models. See README.md and the application profiles docs/colmap4d-components.md. Legacy grouped JPEG/XMP behavior remains frozen at 0.1.5 and is separately available as col4d/frames-to-colmap4d@0.1.0. No STG changes or training are performed.

CLI: `python merge_colmap4d.py --cams CAMS --points POINTS --images PIXELS --output MODEL`. Output must be empty and disjoint from inputs. Inputs are never modified. Failure before profile validation creates no output; a later I/O failure can leave a partial output without the completion marker. Text images support empty POINTS2D lines. Output uses binary normalization without ID or time remapping. No per-frame or per-group filename time inference.

Public input port identifiers are now `4d_points`, `4d_cams`, `4d_images`, matching k4d/megasam-track@0.4.0. Component tags remain colmap4d-point-cloud / colmap4d-cams / colmap4d-images. The CLI still uses --points/--cams/--images internally; no file layout or assembly behavior changes.
