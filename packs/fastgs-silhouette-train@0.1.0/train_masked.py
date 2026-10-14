"""FastGS adapter: masked RGB plus explicit differentiable silhouette loss.

Imports the installed trainer without editing the FastGS repository.
Static and dynamic training share native differentiable alpha; the native trainer
adds deformation regularization and saves paired deformation checkpoints.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/cloud/cloud-ssd1/FastGS-Pano")
from argparse import ArgumentParser

import torch
import train
from arguments import ModelParams, OptimizationParams, PipelineParams
from silhouette_loss import silhouette_loss
from utils.general_utils import safe_state

p = ArgumentParser()
lp = ModelParams(p)
op = OptimizationParams(p)
pp = PipelineParams(p)
p.add_argument("--silhouette_weight", type=float, default=0.1)
p.add_argument("--silhouette_loss", choices=["l1", "bce"], default="bce")
p.add_argument("--max_scale", type=float, default=0.0, help="Reserved; must be zero")
p.add_argument("--test_iterations", nargs="+", type=int, default=[])
p.add_argument("--save_iterations", nargs="+", type=int, default=[])
p.add_argument("--checkpoint_iterations", nargs="+", type=int, default=[])
p.add_argument("--quiet", action="store_true")
a = p.parse_args()
if a.max_scale != 0:
    raise ValueError("Scale-capped refinement was experimental and is not supported")
if not a.masks:
    raise ValueError("Independent masks input is mandatory")
render_original = train.render_fastgs
photo_original = train.photometric_loss
state = {}
records = []


def render_with_alpha(*args, **kwargs):
    kwargs["render_invdepth"] = True
    result = render_original(*args, **kwargs)
    state["alpha"] = result["accum_alpha"]
    return result


def loss_with_silhouette(image, gt, weight, mask=None):
    if mask is None:
        raise ValueError("Missing rgb_loss_mask")
    rgb, l1, ssim = photo_original(image, gt, weight, mask)
    alpha = state.pop("alpha")
    silhouette = silhouette_loss(alpha, mask, a.silhouette_loss)
    total = rgb + a.silhouette_weight * silhouette
    step = state.get("step", 0) + 1
    state["step"] = step
    if step % 1000 == 0 or step == a.iterations:
        records.append(
            dict(
                iteration=step,
                rgb_loss=float(rgb.detach()),
                silhouette_loss=float(silhouette.detach()),
                silhouette_kind=a.silhouette_loss,
                total_loss=float(total.detach()),
            )
        )
        (Path(a.model_path) / "silhouette_metrics.json").write_text(json.dumps(records, indent=2))
    return total, l1, ssim


train.render_fastgs = render_with_alpha
train.photometric_loss = loss_with_silhouette
safe_state(a.quiet)
torch.cuda.reset_peak_memory_stats()
start = time.monotonic()
train.training(
    lp.extract(a),
    op.extract(a),
    pp.extract(a),
    a.test_iterations,
    sorted(set([*a.save_iterations, a.iterations])),
    a.checkpoint_iterations,
    None,
    -1,
    False,
)
(Path(a.model_path) / "adapter_metrics.json").write_text(
    json.dumps(
        dict(
            seconds=time.monotonic() - start,
            peak_allocated_mb=torch.cuda.max_memory_allocated() / 2**20,
            peak_reserved_mb=torch.cuda.max_memory_reserved() / 2**20,
            silhouette_weight=a.silhouette_weight,
        ),
        indent=2,
    )
)
print("Training complete.", flush=True)
