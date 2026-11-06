# col4d/single-cam-drop-time 0.1.0

Explicit conversion from `colmap4d-single-cam` to `colmap-single-cam`.
Both ports are named `camera`, with storage file and arrayed false.
Input JSON must contain exactly model, width, height, params, qvec, tvec and
signed int64 integer timestamp_ns. Geometry validation matches repeat-4d 0.3.0.
Output preserves the six geometry values and removes timestamp_ns. It does not
preserve original JSON whitespace or infer time from external metadata.

This is deliberately lossy. A timed camera can only be reconstructed by
explicitly supplying a timestamp again; there is no automatic inverse or
`colmap-camera` alias. Input remains unchanged; existing output files are not
overwritten. Invalid input fails before output creation.

The pack is arrayable; enabling arrayed_toggle maps over camera arrays and
collects an array of geometry-only leaves. Recommended parallelism 4, batch
size 1. It is a separate geometry branch, not a prerequisite for repeat-4d,
which requires the timed type.

Runtime: colmap4d >= 1.0.4, no GPU required. Local contract, real CLI and explicit
geometry roundtrip checks live in tests/test_repeat_4d_v03.py. No running service
or registry publication is needed for those tests.
