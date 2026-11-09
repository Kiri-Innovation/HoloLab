# Timed image adapters

Timed colmap4d-images extends the existing pixels component with root times.bin,
time_meta.json and either images.bin identity records or image_names.json mapping
string image IDs to relative pixel paths. cameras.bin is optional and preserved.
Older pixel-only split outputs lack time identity and cannot be converted here.

colmap4d-timestamps is a directory with times.bin (COLMAP uint32 image ID plus
signed int64 ns records) and time_meta.json (colmap4d_spec=1.0, time_unit=ns,
time_convention=mid_exposure and nonempty clock_domain). It has no camera poses.

Conversion sorts (timestamp_ns, image_id) into p_<index:016d>.png elements.
Timestamp attachment pairs lexicographically ordered array elements with that
same time ordering, requires equal counts and outputs colmap4d-images. This is
positional correspondence; it cannot detect reordered semantic frame contents.
Existing identity and metadata are preserved. Timestamp-only inputs receive
frame_<image_id>.png names and image_names.json, without inventing camera poses.
The timestamps input uses the existing schema any_of alternatives.

## Fan-out 0.2.0

arrayable=true; timed_images stays a scalar invocation input. Platform fan-out
maps an upstream camera array, while each invocation emits array<image>[frame].
Collected output is camera/frame, with one directory per camera and PNG elements
below it. Disable fan-out for the original one-component conversion. Version
0.1.0 remains unchanged; select 0.2.0 using normal workflow version/fork controls.

Observed: three copies of the real two-frame /tmp/render-images-v03 component
were dispatched by the actual isolated gateway scheduler at parallelism=2.
Three shards completed; each produced two PNGs. Only remote node transport
was replaced with local manifest-rendered subprocesses. This does not test
production WebSocket transport. Evidence:
/tmp/timed-images-fanout-v02/test_real_images_fanout0/evidence.json

Disk: w/wf/j/<parent-job>/images/p_<camera:016d>/p_<frame:016d>.png
Choose a camera leaf for vista4d-generate; flattening cameras concatenates views
and does not establish a valid temporal sequence. images-add-timestamp@0.1.0
is unchanged: no arrayable; images is an array[frame], timestamps is scalar
any_of(colmap4d-images, colmap4d-timestamps). A future version can map paired
camera-level inputs, retaining its frame array as each invocation's input.
