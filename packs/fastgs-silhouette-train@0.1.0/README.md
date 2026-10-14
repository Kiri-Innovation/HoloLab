# Masked RGB and silhouette-supervised static FastGS

Inputs: the original `colmap-folder` and a complete independent `mask-dir`.
This pack imports the existing FastGS trainer and decorates only its training
render/loss functions. It does not modify the FastGS repository or source RGB.

The loss is `masked_photometric_loss + silhouette_weight * BCE(alpha, mask)`.
RGB outside the mask remains ignored. The additional differentiable alpha term
penalizes both opaque background and transparent foreground. Unlike blackening
GT RGB, it supervises actual transparency rather than background color.
FastGS's rasterizer already implements the alpha backward pass; the adapter
requests alpha with `render_invdepth=True`. The depth output is unused.

The standard FastGS masked scoring/densification routines remain unchanged.
Static mode only. Defaults match the original training pack, with an added
`silhouette_weight=0.1` (the workflow records the selected weight). The adapter writes per-1000-step RGB/silhouette/total
losses and measured training time/CUDA allocator peaks. Training artifacts
remain ordinary FastGS PLY models.

The optional `silhouette_loss=l1` reproduces the initial alpha-loss trials.
BCE more strongly penalizes confidently opaque background pixels.

The raw masks-only baseline should be retained separately: this additional
objective is an intervention, not the same experiment as supplying masks alone.
Evaluate raw rendered alpha on both black and white; do not clip final renders
with 2D masks to claim a clean 3D model.
