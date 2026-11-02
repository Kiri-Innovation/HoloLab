# image-undistort 0.6.1

Adds 8-bit RGBA support to 0.6.0. All four channels are passed together through
the existing `grid_sample`, with its existing bilinear interpolation, zero
padding, align-corners setting, precision, and camera grid. Output buffer
channels follow the input. Alpha is straight/unassociated, not premultiplied.
RGBA results are PNGs; exported pose NAME fields follow the output suffix.

Non-RGBA inputs retain the original OpenCV `IMREAD_COLOR` decode and RGB/BGR
conversion, including EXIF handling, JPEG metadata, and encoding parameters.
An unchanged decode probes for alpha before that legacy path, so RGB files incur
an additional decode. Camera math and sampling settings are unchanged.

FP16 can differ by one 8-bit level between channels because of CUDA rounding;
this also occurs with identical channel inputs and does not change geometry.
Use the existing `dtype=fp32` parameter when that numerical difference matters.

0.6.0 remains available for historical snapshots. Daemon identity hashes code
and manifest content, so 0.6.1 does not reuse the old daemon.
