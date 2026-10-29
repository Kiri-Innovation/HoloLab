# col4d/merge-colmap4d 0.1.5

This version preserves the one-dimensional image identity established by
`flatten-array@0.1.0`. Both the COLMAP `Image.name` and the physical path under
`images/` are the root-level `<outer>__<inner>` name, for example
`g000094__cam0.jpg`.

All ID allocation, geometry, timestamps, point-time derivation, calibration,
and time metadata remain byte-compatible with 0.1.3. The group prefix remains
available in the flat filename for audit and reconstruction alignment, but it
is no longer encoded as a directory hierarchy in the colmap4d artifact.
