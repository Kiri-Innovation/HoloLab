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
