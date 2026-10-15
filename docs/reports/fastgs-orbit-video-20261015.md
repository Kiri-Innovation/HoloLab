# FastGS orbit video pack validation — 2026-10-15

Implemented `fastgs-orbit-video@0.1.0` in the independent FastGS-Pano repository:
`/cloud/cloud-ssd1/FastGS-Pano/packs/fastgs-orbit-video@0.1.0/`.
The pack has a manifest, executable, README and CPU tests. HoloLab itself only
needs this integration report; no gateway, viewer or existing pack was changed.

## Contract and defaults

| Port | Contract |
| --- | --- |
| `ply` input | Optional `ply-3dgs`, file storage, standard static 3DGS SH 0–3 |
| `gs_ckpt` input | Optional `fastgs-model`, directory storage; latest numeric iteration or explicit `iteration` |
| `video` output | `video-source`, directory containing `preview.mp4` |
| `diagnostics` output | Existing `text` tag, directory containing provenance and five PNG samples |

Exactly one input is required by the executable. Dynamic checkpoints with a
matching deformation file fail with instructions to bake a PLY first.

Defaults are 93 frames, 15 fps, 650×650 resolution, black background, fixed-radius
orbit, automatic radius (`radius=0`), 10% framing headroom (`margin=0.1`),
40-degree square FOV, camera-down +Y and start angle 0. White background, signed
X/Y/Z axes, resolution, explicit radius, angle, FOV and iteration are adjustable.
Resolution is even for yuv420p. The implementation uses FastGS `render_fastgs`
with mult=0.5 and Gaussian scale modifier=1, then ffmpeg libx264 / CRF 18 /
yuv420p / faststart, following the existing person4d encoding recipe.

The tag decision was checked against
`hololab/gateway/tag_viewers.py::TAG_VIEWER_REGISTRY`:
`video-source` resolves to `video-grid`. This viewer lists video files inside a
directory, so the output preserves that existing storage contract instead of
inventing a tag. The live catalog and resulting handle both return this preview.

The equally spaced fixed-radius camera construction follows the previous fitted
orbit script. That script actually used a manually enlarged radius (2.30 times
an old camera radius), not a general 10% auto-fit. This pack fits all Gaussian
centres in perspective for every requested heading, then checks rendered alpha
>0.5 bounds in every output frame. Any needed expansion changes the shared radius
for the entire sequence. PLY inputs need no COLMAP dataset. Target, radius and FOV
never change within a video. No source PLY or Gaussian attributes are modified.

## Live integration

- Gateway: `http://127.0.0.1:8828`, never restarted.
- Compute node: `c6ad6211-18ff-4a0a-9108-67f370d25072` (`kiri4090`).
- Catalog registration: hot rescan by PATCHing the existing, unchanged
  `pack_dirs` list to the live node config. No source path was added or removed.
- Workflow: `ca5d26f1-b30a-4c03-9511-31e4cb151d99` (`orbit-pack-e2e`).
- Appended node: `steven-orbit-video`, preview opened on `video`.
- Appended edge: `steven-person-export.ply → steven-orbit-video.ply`.
- Dispatch: single-node **continue** on snapshot
  `c9de8589-4ec5-42df-a6a6-6aac9c6ecc60`; no upstream reruns.
- Job: **`758a35a1-0753-491a-976b-80e7e743d6b4` — done**, 93/93.
- Video handle: `305f97b0-912d-4d2c-832f-7033f31820a4`.
- Diagnostics handle: `8769d5b7-9c95-4c86-b3bb-6415a1f86d84`.

The workflow update used its observed `base_updated_ts` optimistic lock. Before
and after comparisons confirmed all original 15 nodes and all existing edges
were unchanged, with exactly one node and one edge appended. The comparison was
repeated after rendering and the read-only browser audit. No Difix was run.

Initially a CUDA image-undistort daemon was resident, but no training or active
HoloLab job was running. It exited on its own before dispatch. The GPU process
list was empty before rendering; the pack also checks for other GPU PIDs before
initialization and every frame and declares exclusive scheduling. No other
process was terminated.

## Artifacts and verification

Main MP4:

`/cloud/cloud-ssd1/Kiri4DGS/output/hololab/w/ca5d26f1-b30a-4c03-9511-31e4cb151d99/j/758a35a1-0753-491a-976b-80e7e743d6b4/video/preview.mp4`

Its sibling `diagnostics/provenance.json` records the source and all 93 cameras.
ffprobe confirmed H.264, yuv420p, 650×650, 15/1 fps, **93 frames, 6.2 seconds**;
the file is 534,058 bytes. Camera radius was 5.180192363360382 scene units;
numerical variation across all camera positions was below 3e-15.
Minimum opaque foreground border clearance over all frames was **35 px**,
exceeding the 29.55 px required by the default headroom at 650 pixels.

Verification evidence is stored at:

`/cloud/cloud-ssd1/Kiri4DGS/output/hololab/deliveries/fastgs-orbit-video-validation-20261015/`

It includes workflow snapshots, dispatch/job/handle JSON, ffprobe output,
`export_contact.png`, five frames decoded from the actual MP4 (0/23/46/69/92),
`canvas-node.png`, `browser-audit.json`, and the additional runs below.

A Chromium audit opened the real workflow, with API mutations blocked. The
appended node rendered its video-grid preview; the actual proxy video reached
readyState 4, decoded at 320×180 (the platform thumbnail rendition), duration
6.2 seconds, with no page errors. The original artifact remains 650×650.

CPU checks passed for numeric checkpoint iteration selection, ambiguous/missing
input rejection, dynamic checkpoint rejection, and perspective containment with
constant radius at all 93 angles for every signed axis. Manifest parsing and
both optional-input shell branches were checked. A real static-checkpoint run
also passed with 12 frames, 256×256 and white background, selecting iteration
15000 and reproducing the foreground PLY SHA-256.

## Model identity and visual findings

The requested upstream port is **not** the final frozen reference model:

| Source | Gaussian count | SHA-256 |
| --- | ---: | --- |
| Latest `steven-person-export.ply` (job `307a1c3a-62d5-43e4-9057-e80608cd8329`) | 259,134 | `e2e10853b6ad528fd179d369ae610add874c869955e21a3de1695448c637ffc9` |
| `person_frozen_reference.ply` | 175,412 | `aaefe6af34ad7ac2d9558e9ba1153936a97674b69d01b12b094e9e207f6960fe` |

The appended node faithfully consumes the latest export handle
`2f504f3f-d323-44fb-871c-ecf60013136e`. Existing workflow inputs were not changed
to disguise this difference. The final reference was additionally rendered with
the same executable and defaults, outside the workflow:

`.../fastgs-orbit-video-validation-20261015/reference-video/preview.mp4`

That reference run also produced 93 frames at 650×650 / 15 fps / 6.2 seconds,
with source hash verified before and after rendering. Radius was
4.546494588570996, and minimum opaque foreground clearance was **43 px**.
`reference-diagnostics/provenance.json` and `reference_contact.png` record it.
This was a direct executable validation, not a fabricated HoloLab job.

Both sets of five decoded MP4 frames were opened and visually inspected:
black background, upright subject, complete reconstructed upper-body subject
inside the frame, and no per-frame zoom. Silhouette dimensions naturally change
with viewpoint and perspective: foreground export height is 54.0–77.1% of the
frame; reference height is 62.3–79.4%. Fixed camera distance does not imply an
identical pixel bounding box at every angle.

**Remaining limitations:** visible arm/torso wisps and stretched fragments are
present in the source Gaussians, in both models (more pronounced in the foreground
export). Background is black, but the result must not be described as entirely
artifact-free. The pack deliberately renders all source Gaussians; alpha >0.5
is only a framing diagnostic, not a cleanup filter. Source models represent an
upper body, not a full-body person. PLY has no gravity metadata, so differently
oriented scenes require `down_axis` selection. Dynamic checkpoints require an
explicit bake step. Wiring the frozen reference into a persistent workflow
would need a separate appended source/export path; it was not substituted for
the explicitly requested `steven-person-export` connection.

## Commits

FastGS-Pano implementation: `ac1d9b1` — `Add fixed-radius Gaussian orbit video export pack`.
HoloLab integration evidence is committed separately with this report. Only the
four new pack files and this report were staged; pre-existing untracked work in
HoloLab was left untouched. No parent repository submodule pointers were changed.
