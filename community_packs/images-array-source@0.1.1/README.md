# HEIC-capable images-array-source

`community/images-array-source@0.1.1` converts HEIC primary images into real
PNG files. Other supported images keep the builtin source's exact filenames,
absolute symlinks, and filename byte ordering. The output remains an
`image`, `arrayed[frame]`, directory handle; downstream nodes need no changes.

## Why community

The builtin source uses `runtime.mode: host`. The manifest validator requires
`exec.python` for host execution and rejects an `exec.shell` entrypoint
(`hololab/manifest/schema.py`, `_check_host_exec`). Python could technically
spawn ffmpeg with `subprocess`, but that would still introduce an external
system dependency into a standard-library-only builtin. The host runner uses
the node's Python, not the `kiri` environment. Having ffmpeg or pillow-heif
installed in kiri does not make either part of the builtin contract.

This pack therefore declares its dependencies and uses `runtime.env: kiri`.
The existing builtin pack is untouched, including its published identity and
non-HEIC behavior. Existing builtin nodes do not automatically gain HEIC support:
replace the source node with this community pack, retain `frames_path`, and
reconnect its `frames` output to the same downstream input.

## Environment and execution

Map the node's logical `kiri` environment to a Python environment containing
`Pillow>=11.1.0` and `pillow-heif==1.3.0`. `requires.packages` declares these
requirements; it does not imply that dependency installation happens silently.
An environment maintainer can install `requirements.txt` with that environment's
`python -m pip install -r requirements.txt`. No GPU is required.

Validate the source using `hololab pack validate` on this directory, then add
its local source through the node's **Pack sources → Add path** UI and rescan.
No central registry publication is needed for local use. No running service or
installed pack is modified by adding these repository files.

Direct CPU verification from this directory:

```bash
/path/to/kiri/bin/python run.py --frames-path /absolute/input --output /tmp/new-output
```

## Output and limits

- `.heic` is matched case-insensitively. `frame_0000.HEIC` becomes
  `frame_0000.png`, a real PNG with EXIF orientation baked into its pixels.
- RGB and RGBA are supported, including alpha. All eight EXIF orientations are applied to every channel, including alpha.
  No resizing or color conversion is applied. PNG compression is lossless relative to the
  decoded HEIC pixels, not to the image before HEIC compression.
- PNG carries Pillow-supported ICC/EXIF information except the orientation tag.
  Original EXIF is read before `to_pillow()` normalizes its orientation tag.
  Missing orientation and orientation 1 leave pixels unchanged; invalid values
  also leave pixels unchanged and emit a warning. HEIC auxiliary/depth
  images and non-primary sequence frames are not exported.
- High-bit-depth/HDR HEIC is rejected explicitly to avoid silently reducing
  precision through Pillow's 8-bit RGB/RGBA PNG path.
- If replacing the extension would collide or reorder files (for example,
  `a.heic` alongside `a.png` or `a.jpg`), HEIC outputs retain the original
  extension as part of the stem: `a.heic.png`. If even this mapping is ambiguous,
  the run fails before writing files. Non-HEIC names never change.
- Use a fresh output directory. Existing matching targets are replaced;
  unrelated old files are not removed. Failures can leave partial outputs.
- The final log reports `converted N HEIC image(s) to PNG` and linked count.

Regression tests: `tests/test_images_array_source_heic.py`. Run with Pillow,
pillow-heif and the project's test dependencies installed to include real
HEIC encoding/decoding and alpha checks.

## Orientation mapping

| EXIF | Pillow transpose operation |
| --- | --- |
| 1 | None |
| 2 | FLIP_LEFT_RIGHT |
| 3 | ROTATE_180 |
| 4 | FLIP_TOP_BOTTOM |
| 5 | TRANSPOSE |
| 6 | ROTATE_270 (90 degrees clockwise) |
| 7 | TRANSVERSE |
| 8 | ROTATE_90 (90 degrees counterclockwise) |

Version 0.1.1 is pending publication. The immutable 0.1.0 pack is retained.
Existing nodes pinned to 0.1.0 must explicitly select 0.1.1 to receive this fix.

## Verification on 2026-11-03

All 252 HEIC inputs from `beach_sunglasses_hat_sitting` became 1440 x 1920
PNGs. Every output matched the manually rotated decoded pixels exactly and
had no EXIF orientation. The comparison was visually checked: the person
is seated upright. All 79 targeted regression tests passed in kiri
(Pillow 11.3.0, pillow-heif 1.3.0), along with Ruff and manifest validation.
Evidence and the comparison image: `reports/heic-rotation-20261103/` at the
repository root. Full PNG outputs: `/tmp/hololab-heic-rotation/frames/`.
No GPU, service restart, workflow change, or registry publication was used.
Other scenes, live workflow dispatch, and combined container irot/imir plus
EXIF transforms were not exercised.
