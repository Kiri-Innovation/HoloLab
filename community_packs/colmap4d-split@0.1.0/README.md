# col4d/colmap4d-split 0.1.0

Input `colmap4d: colmap4d`; outputs `4d_cams: colmap4d-cams`,
`4d_points: colmap4d-point-cloud`, `4d_images: colmap4d-images`.
These scalar directories connect directly to merge-colmap4d 0.4.0.

All three outputs are materialized once. There are no selection parameters.
HoloLab allocates and registers every declared output, even without a consumer;
unconnected ports avoid downstream consumption, not the local copy cost.
Independent files (no symlinks/hardlinks) keep outputs usable after input cleanup.

Accepts sparse/0, sparse, or root COLMAP layouts. Binary takes precedence over
text. Requires times, points_t, time_meta.json and referenced images: this is the
timed component profile accepted by merge, not every optional-sidecar COLMAP
model. Cameras/images/times go to cams, points3D/points_t go to points;
time_meta.json is copied unchanged to both. Pixel files go to images/images/NAME.
ID values, observations, tracks, ns timestamps, metadata and pixel bytes are
never rewritten. Runtime provenance, completion markers and unrelated auxiliary
files are not components. Re-merge creates its own completion marker.

Uses colmap4d read_point_arrays/read_image_headers without per-point/observation
objects, checks finite geometry and safe pixel paths, then copies selected files.
It is a splitter, not a complete input validator; use merge strict_validate for
full validation of untrusted models. Text/variable-track arrays may be slower.
The default binary roundtrip through merge preserves model content exactly;
merge can normalize noncanonical/text inputs per its existing contract.

Run `python -m unittest discover -s community_packs/colmap4d-split@0.1.0`
from the HoloLab repository. Real 41,990,400-point evidence is recorded in
reports/colmap4d-split-070.
