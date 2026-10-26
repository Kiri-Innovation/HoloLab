# merge-colmap4d 0.1.2

This version makes the colmap4d image sample set structurally flat. Its image
port is a one-dimensional `arrayed<image>` whose physical elements are
root-level files named `gXXXXXX__camN.jpg` by
`flatten-image-groups@0.1.0`.

The filename carries assembly provenance, not an array axis. The decoder is
deliberately strict: malformed names, duplicate group/camera pairs, groups not
present in points/cams, camera-set differences, incomplete XMP, or alias
mismatches are errors. After validation, the merge behavior and scalar
colmap4d layout are identical to 0.1.1.
