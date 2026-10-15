# Per-frame mask array migration (2026-10-15)

Workflow: `ca5d26f1-b30a-4c03-9511-31e4cb151d99`.

## Contract and scope

- `person-gray-mask@0.1.0`: `masks` changes from scalar `[mask-dir]` to `arrayed: true`, `tags: [mask, image]`, `dim_labels: [frame]`, `storage: dir` for the aggregate. Each visible child is one PNG file, with the corresponding image stem. The manifest documents uint8 grayscale, 0/255 polarity, source dimensions, unique/exact stem coverage, and foreground coverage validation.
- `fastgs-foreground-export@0.1.0`, `fastgs-silhouette-train@0.1.0`, and the separate FastGS-Pano repository's `fastgs-pano-train@0.1.0`: `masks` becomes `arrayed<mask>[frame]`, aggregate storage `dir`. Required/optional settings are preserved. No execution code changes: consumers receive the aggregate directory.
- The live `steven-clean-fastgs` node actually runs `fastgs-silhouette-train`, not `fastgs-pano-train`. Both declarations were adapted; the requested clean-node short run exercises the actual silhouette trainer.
- In-place version migration follows `frame-extraction@0.1.0` and `video-array-source@0.1.0`'s documented cardinality migrations. This updates the existing wired nodes without replacing their versions or changing edges. Historical `mask-dir` handles are not rewritten; downstream verification explicitly uses newly generated handles.

## Tag and viewer decision

The manifest vocabulary uses open tags; the existing mask ports used only `mask-dir`. There was no existing per-file mask semantic tag. Introduce `mask` and co-tag the producer with existing `image`: a binary mask is also a single decodable image. `handle_summary._dim_sizes_from_tree` recognizes file leaves through `image`; a mask-only declaration would not report the array dimensions. The existing image contract also validates every leaf's decodability.

Consumers require `mask`, preserving their semantic input distinction. The existing image viewer is inferred via the producer's `image` co-tag. No `tag_viewers.py` changes were made: that file was explicitly identified as another session's work area. No gateway/framework changes or restart were needed.

Arrays are represented by an aggregate handle and explicit file elements. The framework enumerates those file names and creates subhandles when fan-out requires them; this non-arrayable consumer path does not eagerly register 182 separate UUID handles. Verification records the aggregate handle and every actual file element, without inventing child handle IDs.

## Verification

The pack watcher hot-rescanned all four manifests; `catalog-after.json` is the live catalog evidence. All three consumer declarations pass the framework's tag/cardinality/axis compatibility check against the producer.

`python -m pytest -q tests/test_manifest.py tests/test_strict_image_contract.py tests/test_arrayed_marker_contract.py`: **106 passed, 1 skipped**. An earlier invocation referenced a nonexistent summary-test filename and collected no tests; the corrected command above passed.

Live job receipts, logs, handle summaries, full mask-file checks, and export count comparison accompany this report.

After the mask rerun, single-node canvas export was rejected because its new snapshot had no produced clean-training artifact. To compare the identical model, an explicit `/api/jobs/run` export binds the previous export's colmap/model handles and the new mask handle. This is an actual pack execution, not a canvas node run. After the 3000-iteration clean canvas run, export is also rerun through the canvas to verify the complete current snapshot. The initial mask run used the mask-only declaration; the final reported run repeats generation after adding the image co-tag. No Difix job was dispatched. Only mask, foreground export, and clean training were dispatched; other pipeline nodes were reused.

The 3000-iteration training verification temporarily changes only `steven-clean-fastgs.params.iterations`, using optimistic concurrency checks, then restores that field immediately after dispatch. The execution snapshot retains 3000; the canvas retains its original setting.

## Repository delivery

HoloLab changes are limited to the three manifests and this evidence directory. Existing untracked `tmp/`, `test-results/`, and `website/flops-docs-light.png` are excluded.

FastGS-Pano is a separate repository at `/cloud/cloud-ssd1/FastGS-Pano`, checked out on `hololab-integrate`. Commit `431f01818eb683285468fc05148f3f77ebe9f735` contains only its mask input declaration change (5 insertions, 2 deletions). Its `origin/main` lacks this pack and the implementation of several parameters it uses. Publishing this branch to main would also publish roughly 30 unrelated existing commits; copying the manifest alone would introduce a pack incompatible with main's trainer. No unrelated history is merged or pushed. This branch baseline is a remaining publication constraint pending user direction.


## Live results

| Execution | Job ID | Result |
| --- | --- | --- |
| `steven-person-mask` (final declaration) | `15bc742d-8957-4adb-b656-261a59e65f97` | done; 182 file elements, axis `[frame]`, all 1458×1458 grayscale 0/255 PNGs |
| Same-model export via explicit input handles | `474108f4-66e9-4dce-b6ff-21f5a883714b` | done; 259,260 → 259,134, removes 126, exactly equal to baseline across 182 views |
| `steven-clean-fastgs` | `9e0c2f9f-c1e4-47fc-9ae3-7a3550cd05eb` | done; 3000/3000, new mask handle confirmed in input handles and saved cfg_args |
| `steven-person-export` on the new short-run model | `96efeda7-afa3-4089-8cb8-310ec119ed62` | done; 355,585 → 355,465, removes 120 across 182 views |

Final mask aggregate handle: `567a1ded-d615-4634-bd41-a3633495864f`, tags `[mask, image]`, dimensions `[182]`, inferred viewer `image`. `mask-elements-verified.json` enumerates all 182 file elements with SHA-256 digests; the dot marker is excluded.

Training's last recorded masked RGB loss is 0.02757187, silhouette BCE 0.05273154, total loss 0.03284503. The short-run model has a different Gaussian count from the old 15000-iteration model, as expected; the fixed-model export comparison above isolates the type migration and is exactly unchanged. Latest canvas runs for all three requested nodes are done. The current exported model is the 3000-iteration verification result; prior full-run models and exports remain available in history. No downstream orbit or Difix node was rerun.

Final graph comparison, excluding derived runtime/type display fields, confirms every node's parameters and all edges equal the pre-task graph. Training's canvas iterations remain 15000. The optional standalone `mask` viewer mapping is deferred; preview works through the existing image co-tag without editing the registry.
