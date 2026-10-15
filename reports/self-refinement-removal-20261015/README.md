# Self-refinement removal ledger — 2026-10-15

Removed only the additions identified by the delivery commits.json and workflow-before/after.json. Existing WIP, protected deliveries, and zombie shards were not modified. Gateway 8828 was not restarted.

| Category | Action and result |
|---|---|
| Workflow | CAS-protected POST against the freshly read draft; subtract exactly 6 node IDs and 15 edge IDs. HTTP 200. 12 nodes / 18 edges, DAG, validation issues: none. |
| Delivery | Exact-path checked shutil.rmtree removed steven-self-refinement-20261014 (30 files); path absent. |
| Source | Three traceable git reverts removed 12 tracked files; five dedicated compiled Python cache files also deleted. |
| Artifacts | 292 individual DELETE /api/artifacts/{handle_id}; all HTTP 200, state=deleted; 867,529,278 bytes reported freed. Physical paths verified absent. |
| Shared snapshot | Preserved 8db73969-d01a-4dec-9654-6b508e55ab67. Preview: 114 jobs, 113 exclusive / 13 shared artifacts, 103 exclusive / 11 shared jobs. It contains 15 jobs outside this line, so no snapshot DELETE was issued. |
| Protected delivery | All 871 protected files retain their SHA256, including person_frozen_reference.ply, final_orbit_fitted.mp4, renders_fitted/, all three person delivery directories, and tools/person4d/make_video.py. |
| Existing packs | person-gray-mask, fastgs-silhouette-train and all other pipeline packs retained. Five removed packs absent from online catalog. |

## Jobs and deletion attribution

Each parent job ID, workflow, graph-node ID, creation timestamp and output map matched the archived job-details.json. The 93 completed Difix children were checked by parent_job_id and creation window. 186 producer-less input-slice handles were proven to be those children’s exact inputs, under this line’s rendering directory, within the Difix creation window. All 10,511 known jobs were checked for outside consumers; none found. All 38,667 artifact paths were checked for overlaps before deletion.

| Node | Job ID | Created UTC |
|---|---|---|
| refine-render-before | `7dd35ba9-5bcb-4e9d-a2ca-4590908bf81e` | 2026-10-14T14:50:42.702008+00:00 |
| refine-difix | `fddd3aa3-52f2-40d3-a989-8db57f1241aa` | 2026-10-14T14:52:24.060358+00:00 |
| refine-data | `05448762-01c9-4c23-a395-9a95efb53338` | 2026-10-14T15:07:08.193723+00:00 |
| refine-train | `391822cb-aa35-4b8e-8e73-d4b589101e79` | 2026-10-14T15:08:08.244928+00:00 |
| refine-render-after | `ebfe8a11-7263-4ed9-8f1d-906f420b244f` | 2026-10-14T15:08:43.284909+00:00 |
| refine-compare | `f76ded4e-720f-4795-8f59-2181c4047e3b` | 2026-10-14T15:10:28.366323+00:00 |

## Exact source removal

- hololab: `5634979c5806f605d16e0129b78007cb150ea485` → revert `e641848a7a2a9c89ed3c00a80ed8967f40bab813`.
  - `packs/refinement-compare@0.1.0/manifest.yaml`
  - `packs/refinement-compare@0.1.0/run.py`
  - `packs/replace-colmap-images@0.1.0/manifest.yaml`
  - `packs/replace-colmap-images@0.1.0/run.py`
  - `reports/steven-self-refinement-20261014.md`
  - `tests/test_replace_colmap_images.py`
- /cloud/cloud-ssd1/FastGS-Pano: `7e408937303d58bfd5c8150859fb172aae445377` → revert `b181d18b52aa01aabf4dbae7434a9b57336a1783`.
  - `packs/fastgs-camera-array@0.1.0/manifest.yaml`
  - `packs/fastgs-camera-array@0.1.0/run.py`
  - `packs/fastgs-refine-ply@0.1.0/manifest.yaml`
  - `packs/fastgs-refine-ply@0.1.0/run.py`
- tryouts/Difix3D: `9f120f376b1d1089aad359e887de7fbb8232c988` → revert `81a638f4ff8faa2f322902532b6faefe189c4fe0`.
  - `packs/difix-image@0.1.0/manifest.yaml`
  - `packs/difix-image@0.1.0/run.py`

## Preserved and exceptions

- The shared snapshot and job database rows remain for history; only this line’s artifact files were deleted and handles tombstoned. No bulk deletion or SQL mutation was used.
- Original Difix source, weights, existing fix packs, and general FastGS render.py predate the recorded commits and remain. Orbit-rendering commits 2ea07a9 and b1e8814 remain.
- Concurrent cosmetic edits after the successful workflow save were retained (three existing nodes; positions/titles). No old graph snapshot was restored.
- FastGS-Pano main never contained the two removed packs. Its revert b181d18 is on the original hololab-integrate branch, locally retained pending the user’s remote-branch preference. No unrelated branch history was merged into main.
- Root repository submodule pointers and unrelated untracked WIP were not staged.

HoloLab main: removal revert e641848 plus this audit commit. Difix3D main: revert 81a638f pushed successfully. Final verification confirmed all 38,375 other artifact rows unchanged and all 12 other workflows unchanged.

## Detailed evidence

- scope.json: every removed source file, node, edge, delivery file, parent/child job ID, and snapshot-preview response.
- artifact-delete-receipts.json: every handle, producing job/path/time, HTTP status and exact API deletion result.
- protected-sha256.json: every retained protected file and SHA256.
- verification.json: final topology, validation, catalog, health and other-artifact checks.
- concurrent-cosmetic-edits-preserved.json: subsequent changes preserved.

Main chain remains video-src → frame-extraction → colmap-sfm → undistort/triangulate → merge-colmap → FastGS training, with all 18 original edges and no dangling endpoints. Validation uses the same validate_snapshot and live catalog as the gateway run endpoint; training was not rerun.
