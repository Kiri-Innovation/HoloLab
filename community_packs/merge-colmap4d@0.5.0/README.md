# col4d/merge-colmap4d 0.5.0

Fan-out zips equal-length outer arrays of `4d_cams`, `4d_points`, and
`4d_images` by independently byte-sorted position. Each shard assembles one
compatible triple and emits one `colmap4d`; the parent emits an outer array.
Names need not match; lengths must match. This does not combine models across
shards. All three ports map: explicitly repeat shared points if compatible
with every camera group. Track references, timestamps and pixel names must
still pass the unchanged assembler validation. With fan-out disabled, all
inputs and output remain scalar. Version 0.4.0 remains unchanged.

Root defaults to this directory. `.packignore` includes only the manifest and
two runtime Python modules (plus the protected ignore control itself). Tests
and documentation are excluded from identity and publication.

## Assembly and validation

Assemble scalar `4d_cams`, `4d_points`, `4d_images` components into one complete `colmap4d` output. Inputs, IDs, observations and int64 nanoseconds are unchanged. No XMP time inference. Version 0.2.0 remains untouched for history.

`strict_validate` defaults to **false**. The normal MegaSAM binary case uses `colmap4d.arrays.read_point_arrays` and `read_image_headers`; it never constructs Python Point3D objects, observation lists or the global reciprocal-observation set. The small camera table retains full validation. The library's header reader omits observations; its array/text reader uses `include_tracks=False`. In strict final validation the library itself calls its base reader with `include_observations=False, include_tracks=False`, but this still creates per-point objects, so it belongs to the explicit heavy path.

## What fast validation proves

- Exact record sizes, complete point/image counts and duplicate/invalid IDs; all point XYZ/error and observation XY values are checked for finiteness (error nonnegative).
- Complete, unique image/point timestamp coverage, with exact integer ns. ID order is not assumed to be chronological: legitimate multi-camera models need not have monotonic time by ID.
- All camera parameters/poses, safe image names, image/pixel bijection, full pixel decoding, RGB/RGBA mode and resolution. Metadata is merged with conflict rejection; device/camera attribution conflicts also fail.
- **Uniform single-observation points (MegaSAM): exhaustive reciprocal observation/track and point/source-image timestamp equality.** Chunked direct ID indexing or searchsorted replaces Python objects. Every valid observation must match its point's one fixed (image,index); matching reference count then proves no point lacks its unique observation. This is not sampling.
- Uniform multi-observation tracks: every referenced ID, track image/index bound and geometry value is checked; duplicate tracks, reciprocal relations and integer track-centroid time are deterministically sampled (up to 4096 evenly spaced points and 4096 observations per image). This does not certify unexamined multi-track relations. Use strict mode for untrusted/external acceptance.

Empty tracks cannot claim track_centroid. Wrong sizes/coverage/geometry fail without a success marker. Valid text, nonuniform-track, unsorted model or noncanonical sidecar layouts automatically fall back to the complete legacy normalizer/validator (with an explicit log). They are supported, but are not promised dense-binary fast-path performance.

## Strict mode and output integrity

`strict_validate=true` retains 0.2.0's full reciprocal/centroid validation, binary normalization and final `colmap4d.validate_full` with strict exit-code handling. It needs substantial RAM/time on dense data (the old large job was observed at 42.4 GB RSS). Default fast mode does **not** call or claim the library's full validation; its coverage is stated above and in the result log.

Fast canonical binary files and all image bytes are copied to independent destination files. No hardlinks, mutation of inputs, ID remapping, float conversion, observation dropping or time recomputation. Sidecar/meta output layout is `colmap4d/sparse/0`; pixels remain under `colmap4d/images/<NAME>`. `time_meta.json` uses the same merged metadata and library serializer as 0.2.0. All canonical model content must match old output hashes; gateway-owned `.hololab-metadata.json` is run provenance rather than model content.

Progress `[ITER 0/5]` through `[ITER 5/5]` covers read/validation/pixels/copy/completion. The JSON result reports validation coverage, fallback reason, wall seconds and Linux process `max_rss_kib`. No `strict_problems=0` claim is emitted for sampled fast validation.

## Validation and benchmark

See `reports/merge-030-performance/` in this repository for real gateway jobs on the same 41,990,400-point / 81-image inputs, hash equality, and negative fixtures. The earlier estimate of 55 million points is corrected by binary counts. Unchanged assembler regression tests: `python -m unittest discover -s community_packs/merge-colmap4d@0.4.0 -p test_fast.py -v` with colmap4d, NumPy and Pillow available. No library changes or registry publication are included.
