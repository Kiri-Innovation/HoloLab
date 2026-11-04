# BiRefNet foreground masks

One video → an ordered, flat `arrayed<image>` on the `frame` axis. Compatible
with the SAM video mask ports, while retaining foreground props and soft edges.

- `runtime.env: cam3` (this machine maps it to `kiri`); CUDA required.
- Local checkpoint directory only (`trust_remote_code=True`); no weight download.
- Batch size 4; FP16 inference at 1024×1024, sigmoid in float32, bilinear
  upsampling to the decoded raster, then rounded uint8 grayscale PNG.
- `p_<i:016d>.png`: 0 background, 255 foreground, intermediate alpha retained.
  There is no thresholding of the saved output. Reported foreground ratios use
  alpha ≥ 128/255; `alpha_mean` reports mean coverage separately.
- Uses HoloLab `frame-extraction@0.2.1` directly, with the same `max_frames`,
  `skip`, `start_frame`, `end_frame`, and `max_width` semantics as `fx`.
- `.validation.json` records each source index, decoded RGB SHA-256, alpha
  statistics, frame selection, extractor hash, runtime and peak GPU allocation.
- Every invocation owns a unique `TemporaryDirectory` under its job scratch;
  no external reference frame directory is needed. Cleanup also runs on errors.
- `scratch_frame_bytes` reports the decoded frames' disk footprint.
- A `.hololab-done` marker is written only after every saved mask has been
  decoded and checked. Temporary decoded frames are automatically removed.

The inference follows `4DAnyone/fdanyone/foreground.py`, but loads frames in
bounded batches and resizes floating-point probabilities before PNG quantization.
It does not require the 4DAnyone Python package or copy the model weights.

Run decoder equivalence and scratch isolation tests in the configured inference environment:

```sh
python community_packs/birefnet-mask@0.1.2/test_alignment.py
```

`mask-overlay@0.2.0` uses a threshold for its visualization modes; this does not
alter the original soft mask PNGs used by a later alpha-channel writer.

For the optional 50-frame real-video test, set `BIREFNET_TEST_VIDEO` to a video path.
