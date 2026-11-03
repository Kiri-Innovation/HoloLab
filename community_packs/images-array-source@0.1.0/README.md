# HEIC-capable images-array-source

`community/images-array-source@0.1.0` converts HEIC primary images into real
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
  `frame_0000.png`, a real PNG with the decoder's original size and pixels.
- RGB and RGBA are supported, including alpha. No resizing, extra rotation,
  or color conversion is applied. PNG compression is lossless relative to the
  decoded HEIC pixels, not to the image before HEIC compression.
- PNG carries Pillow-supported ICC/EXIF information. HEIC auxiliary/depth
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

## Verification on 2026-11-03

CPU-only direct execution in the existing kiri environment (Pillow 11.3.0,
pillow-heif 1.3.0), using the read-only `persona_bench` scene
`beach_sunglasses_hat_sitting/input/image`:

- 252 HEIC inputs produced 252 regular PNG files, with no output symlinks.
- `file` identified every output as `PNG image data, 1920 x 1440, 8-bit/color RGB`.
- PIL opened all 252 outputs. Every size, mode and decoded pixel SHA-256
  matched its HEIC source, and byte-sorted source/output sequence order matched.
- Example `305186217781234.png`: `ls -la` reported
  `-rw-r--r-- 1 root root 4151152`; both decoded pixel SHA-256 values were
  `c3e2950bafc95afcb00950e400d77034447e369ba1e88e1a6e77071001ca4812`.
- Final conversion log: `converted 252 HEIC image(s) to PNG; linked 0 frame(s)`.
- Targeted regression suite: **54 passed** across
  `test_images_array_source_heic.py`, `test_manifest.py`,
  `test_utility_arrayed_packs.py`, and `test_resource_preflight.py`.
  This includes all-PNG symlinks, mixed HEIC/PNG, uppercase HEIC, RGBA alpha,
  collision/order handling, and explicit host-shell rejection.
- Manifest validation and Ruff checks passed.

Local verification artifacts are under `/tmp/hololab-heic-verification/`:
`conversion.log`, `verification.log`, `file-evidence.txt`,
`pixel-evidence.json`, `verify.py`, and the `frames/` PNG directory.
The environment lacked `file`; Ubuntu's file/libmagic packages were downloaded
and extracted under that temporary directory, without system installation.

This validation did not publish to the registry, change installed packs or
workflows, restart HoloLab, access a GPU, or alter source data. The other
21 scenes and dispatch through a live HoloLab workflow were not exercised.
