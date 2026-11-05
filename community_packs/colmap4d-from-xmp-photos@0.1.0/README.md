# k4d/colmap4d-from-xmp-photos 0.1.0

The image input is strictly one-dimensional. Convert the upstream
`arrayed[group,cam]<image>` with the type-agnostic `flatten-array@0.1.0`.
Root-level elements use the generic `<outer>__<inner>` convention
(`gXXXXXX__camN.jpg` here).

The input element name is preserved as both COLMAP `Image.name` and the
root-level path below `images/`. Grouping remains recoverable from the name
for assembly alignment, without becoming an output directory hierarchy.

Independent legacy XMP producer, migrated from frozen col4d/merge-colmap4d@0.1.5. Input points/cams retain grouped array contracts; images is a flat image array with gXXXXXX__camN.jpg names. Image time is wallClockEpochNs + exposureNs // 2, with one common XMP timebase. Point times are integer track centroids. Output is a complete scalar colmap4d model, not three components. Source provenance identifies this new pack; all other native behavior is retained.

Legacy compatibility includes blank image observation rows and retained point tracks addressing triangulation keypoints. This passes the existing colmap4d validator but does not claim the stronger reciprocal-track component profile. Use merge-colmap4d@0.2.0 only with actual component-profile inputs. No STG changes, GPU or training. See README.md.

The algorithm is copied from 0.1.5; only conversion.source changes. The old pack is retained byte-for-byte. This is not an automatic workflow migration.
