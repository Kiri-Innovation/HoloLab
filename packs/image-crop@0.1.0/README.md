# image-crop@0.1.0

One image in, one image out; both use the existing `image` tag and file storage.
CPU runtime: `kiri` (Pillow, NumPy, OpenCV already present). The executor owns
`{{ outputs.image }}.hololab-done`, matching single-image-source.

## Parameter semantics

`pad_left`, `pad_top`, `pad_right`, `pad_bottom` are independent floats, default 0,
**always pixels, never ratios**. Round half away from zero: 0.49 -> 0,
0.5 -> 1, -0.5 -> -1. Positive removes that side; negative adds a border.
`pad_color` defaults to `#000000`, accepts exactly `#RRGGBB`, and is opaque.
Grayscale uses luminance; 16-bit color components scale by 257.

For rounded L/T/R/B, the output is `(W-L-R) × (H-T-B)`. Crop bounds are clamped
to the original extent. If the positive cuts consume an entire axis, the job
fails with source dimensions and rounded paddings. Opposite-side padding does
not rescue an empty crop. A valid 1×1 crop succeeds. Nonfinite values and
excessive output allocations fail explicitly.

PNG/JPG/JPEG/WebP are detected from bytes, including extensionless handles.
No resize, interpolation, rotation, or EXIF auto-orientation is performed.
Zero padding copies the original bytes. JPEG transforms re-encode at quality
95, so JPEG pixel values may change through compression. WebP transforms use
lossless encoding. PNG retains 1/2/4/8/16-bit depth; low-bit grayscale may be
represented by an equivalent palette. Indexed PNG padding requires an existing
opaque palette color, otherwise it fails rather than silently changing depth.
Animated PNG/WebP is rejected. Metadata preservation is best effort; 16-bit PNG
uses OpenCV and does not retain ancillary metadata.

## Verification — 2026-10-23

Hot watcher registration confirmed through `/api/pack-catalog`; inferred preview
is `image`. No process restart. Only this new pack was added to the repository.
A separate validation workflow was created; no pre-existing graph was edited.

- Workflow: `1f9ec9d4-e851-4fbc-828e-5940d3f22cfa`
- Final snapshot: `543450fb-de5a-4175-8b93-942da04bc95d`
- Source: `single-image-source@0.1.0`, RGB PNG, **320×240**.
- Source fixture has a 20-pixel grid, labeled opposite corners and a diagonal.

| Case | L / T / R / B (pixels) | Actual output | Result |
| --- | --- | --- | --- |
| Unequal crop | 31 / 17 / 53 / 29 | 236×194 PNG | Exact source rectangle `[17:211,31:267]` |
| All zero | 0 / 0 / 0 / 0 | 320×240 PNG | Byte-identical to source |
| Overflow | 321 / 0 / 0 / 0 | No output | Clear error; source is only 320 pixels wide |
| Border | -23 / -11 / -37 / -19 | 380×270 PNG | Source at (23,11), border `#204060` |
| Minimum crop | 319 / 239 / 0 / 0 | 1×1 PNG | Exact original bottom-right pixel |
| Mixed | 31 / -11 / 53 / -19 | 236×270 PNG | Exact retained columns with top/bottom border |

The expected overflow failure stops whole-graph execution; remaining unequal
and zero nodes were explicitly continued against the same snapshot. Successful
jobs have executor idempotency markers. The failed job has no image output.

Every successful output was opened for visual inspection. The retained grid,
corner labels and diagonal show correct crop position without stretching or
rotation. NumPy comparisons additionally verified every retained PNG pixel,
border color and 1×1 pixel. The zero result passed a byte-level comparison.
The final run's PNGs were also compared to the visually inspected first run.

Evidence in [validation/](validation/) consists of actual file copies from job
outputs, not mocked screenshots. [results.json](validation/results.json) records
job IDs, absolute original output paths, measured dimensions and SHA-256 hashes.
[overflow-log.json](validation/overflow-log.json) is the real failure log;
[catalog.json](validation/catalog.json) records registration and viewer inference.

Additional checks:

```sh
/cloud/cloud-ssd1/envs/kiri/bin/python -m unittest discover -s packs/image-crop@0.1.0 -v
/cloud/cloud-ssd1/envs/kiri/bin/python packs/image-crop@0.1.0/crop.py --help
```

Seven tests pass: asymmetric/mixed placement, zero-copy equality, rounding and
invalid bounds, all four extensions plus extensionless handles, 16-bit PNG
(gray/RGB/RGBA), indexed depth/transparency and bilevel PNG, 2/4-bit grayscale.
Manifest schema validation and shell argument quoting checks also pass.

## Full manifest

```yaml
apiVersion: hololab.dev/v1
kind: Algorithm
name: image-crop
version: 0.1.0
description: "Crop a rectangle from one image, or add a constant-color border."
source_entry: crop.py
category: "image/processing"
docs: |
  Four independent float paddings, always in pixels (never ratios).
  Positive values remove pixels from that side; negative values add a border.
  Round to nearest integer, with half pixels away from zero (0.5 -> 1).
  Coordinates refer to stored pixels; no resizing, rotation or EXIF auto-orientation.
  Output size: W - left - right by H - top - bottom after rounding.
  Crop bounds are clamped to the original image. If positive paddings remove
  all original pixels on either axis, fail clearly; outward padding cannot
  rescue an empty crop. The retained rectangle must be at least 1x1.
  PNG/JPEG/WebP encoding and sample bit depth are preserved; format is detected
  from bytes, so extensionless file handles work. All-zero is a byte-for-byte copy.
  JPEG crops are re-encoded at quality 95; WebP crops use lossless encoding.
  PNG supports 16-bit samples without downconversion and indexed bit depths.
  Indexed borders must use an existing opaque palette color or fail explicitly.
  Low-bit grayscale may use an equivalent palette at the same bit depth.
  pad_color is opaque #RRGGBB; grayscale uses luminance (16-bit scales by 257).
  Animated images are rejected. Output limit: 100 million pixels, 100000 per side.
inputs:
  image:
    tags: [image]
    storage: file
    description: "One PNG, JPG/JPEG or WebP still image."
params:
  pad_left:
    type: float
    default: 0
    description: "Left pixels: positive crops, negative pads; nearest integer, half away from zero. Not a ratio."
  pad_top:
    type: float
    default: 0
    description: "Top pixels: positive crops, negative pads; nearest integer, half away from zero. Not a ratio."
  pad_right:
    type: float
    default: 0
    description: "Right pixels: positive crops, negative pads; nearest integer, half away from zero. Not a ratio."
  pad_bottom:
    type: float
    default: 0
    description: "Bottom pixels: positive crops, negative pads; nearest integer, half away from zero. Not a ratio."
  pad_color:
    type: string
    default: "#000000"
    description: "Opaque border color as #RRGGBB; grayscale uses luminance. Default black."
outputs:
  image:
    tags: [image]
    storage: file
    description: "Cropped/padded image in the original encoding and sample bit depth."
runtime:
  env: kiri
  gpu:
    required: false
  expected_duration_min: [0.01, 0.05]
  resources:
    scratch_gb: 1
    mem_gb: 2
exec:
  shell: |
    set -euo pipefail
    python '{{ pack_dir | replace("'", "'\"'\"'") }}/crop.py' \
      --image '{{ inputs.image | replace("'", "'\"'\"'") }}' \
      --output '{{ outputs.image | replace("'", "'\"'\"'") }}' \
      --pad-left={{ params.pad_left }} --pad-top={{ params.pad_top }} \
      --pad-right={{ params.pad_right }} --pad-bottom={{ params.pad_bottom }} \
      --pad-color='{{ params.pad_color | replace("'", "'\"'\"'") }}'
idempotency:
  marker: "{{ outputs.image }}.hololab-done"
progress:
  stdout_regex: '\[ITER (\d+)/(\d+)\]'
```
