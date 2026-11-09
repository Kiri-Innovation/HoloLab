# Images add timestamp 0.2.0

Enable fan-out for `images` shaped camera/frame and `timestamps` shaped camera.
Both inputs map their outer arrays by independently sorted position and must
have equal camera counts. One invocation receives a full frame array and one
`colmap4d-images` or `colmap4d-timestamps` directory. Output `timed_images` is
collected as a camera array. With fan-out disabled, inputs retain the 0.1.0
single-invocation contract.

Timestamps are mapped because timed components also carry image IDs, names,
and optional camera/pose records that must belong to the corresponding camera.
The adapter copies those records and pairs frames with IDs sorted by
(timestamp_ns, image_id). Equal frame counts are required. Positional pairing
cannot detect semantically reordered frames. If all cameras share a timestamp-only
timeline, explicitly repeat that component in camera order. Sharing clock
values alone does not make camera identity interchangeable.

Version 0.1.0 is retained. The runtime implementation is unchanged.
