"""Opacity supervision, independent of RGB target colors."""

import torch


def silhouette_loss(alpha, mask, kind="bce"):
    alpha, target = alpha.squeeze(), mask.squeeze().float()
    if alpha.shape != target.shape:
        raise ValueError("Rendered alpha and foreground mask must have matching shapes")
    if kind == "bce":
        return torch.nn.functional.binary_cross_entropy(alpha.clamp(1e-5, 1 - 1e-5), target)
    if kind == "l1":
        return (alpha - target).abs().mean()
    raise ValueError(f"Unknown silhouette loss: {kind}")
