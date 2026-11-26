# Metric depth map

The bundled `depth_map@0.1.0` Type Pack stores a raster of optical-axis Z
values in metres. Array rows increase downwards and columns increase to the
right. Positive finite values represent measured or estimated depth. Positive
infinity represents no finite hit. Consumers should skip NaN, negative infinity
and nonpositive samples as invalid. The codec preserves the float32 samples
losslessly, including infinity. Intrinsics and color can travel on separate ports.

A scalar is a single-part scanline OpenEXR file using ZIP compression, with
one full-resolution FLOAT channel named `Z`. The data and display windows
are identical and start at (0, 0). The array form uses HoloLab's positional
directory layout with `p_0000000000000000.exr` and successive members along
the `frame` axis. Positions express collection order.

An optional EXR string attribute `hololabFrame` contains a JSON object with
nonempty string fields `source_id`, `frame_id`, and `clock_domain`, and a
signed int64 JSON integer `timestamp_ns`. The attribute is the sole frame
metadata for the EXR. Producers define the source and clock identifiers and
timestamp meaning. A still or a depth raster without established provenance
can omit the attribute. Consumers that associate frames must establish shared
source identity and a compatible clock. Full raster dimensions come from EXR.

Worker environments using the codec need NumPy and OpenEXR Python bindings
with the `OpenEXR.File` API (3.3 or newer). HoloLab discovers the manifest
without importing these dependencies. Exact worker bindings use the existing
Type Pack resolution mechanism.

Bundled discovery exposes the type declaration. Typed algorithms bind
`depth_map@0.1.0` through `exec.typed.type_packs` and need the corresponding
codec in their Type Pack lock. The current installer resolves that lock
through the algorithm's registry authority.

For standalone codec and worker tests, install the OpenEXR bindings in the
test environment and run `python -m pytest -q tests/test_depth_map_type.py`.
The worker test uses a temporary filesystem registry.
