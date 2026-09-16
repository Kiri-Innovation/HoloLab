# stg-train

Wraps [SpacetimeGaussians](https://github.com/oppo-us-research/SpacetimeGaussians)' `train.py` as a HoloLab pack.

## Inputs
- `colmap_dir` — a directory produced by `gsseq_to_multiview.py` (e.g. `.../point/colmap_0/`).
- `flow_dir` *(optional)* — Track4World `3d_ff_output` for flow-based initialization. Not used yet by this pack; reserved.

## Outputs
- `model_dir` — the trained STG model directory. Contains `iteration_N.splatv`, `save_cam/`, `point_cloud/`, `exp_log.txt`, etc.

## Env
`runtime.env: kiri` — the logical name for the SpacetimeGaussians training environment. Map it on your node with:

```yaml
envs:
  kiri: /path/to/your/kiri/conda/env
```

## Smoke config
For a fast sanity check, set `iterations` to a small number (e.g. 200–500) and `duration` to the number of frames actually present in your COLMAP dir.

## Required params
`stg_repo` **must** be provided — there is no default because the SpacetimeGaussians checkout is machine-specific. Example REST body:

```json
{
  "algorithm_name": "stg-train",
  "algorithm_version": "0.1.0",
  "params": {
    "iterations": 500,
    "duration": 49,
    "stg_repo": "/absolute/path/to/SpacetimeGaussians"
  },
  "input_handles": { "colmap_dir": "/absolute/path/to/point/colmap_0" }
}
```
