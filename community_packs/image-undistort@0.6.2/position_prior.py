"""CPU-only positional adapters, separated from GPU imports for verification."""

import shutil
from pathlib import Path

from position_model import (
    copy_auxiliary,
    paired_poses,
    stage_images,
    validate_tracks,
    write_images,
)

__all__ = ["publish_poses", "stage_images", "stage_sparse_prior"]


def stage_sparse_prior(model: Path, output: Path, files: list[Path]):
    poses = paired_poses(model, files)
    rewritten, rewrites = [], []
    for (row, obs), file in zip(poses, files, strict=True):
        row = list(row)
        rewrites.append((row[9], file.name))
        row[9] = file.name
        rewritten.append((row, obs))
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    shutil.copyfile(model / "cameras.txt", output / "cameras.txt")
    write_images(output / "images.txt", rewritten)
    if (model / "points3D.txt").is_file():
        shutil.copyfile(model / "points3D.txt", output / "points3D.txt")
        validate_tracks(output / "images.txt", output / "points3D.txt")
    copy_auxiliary(model, output, {int(row[0]): int(row[0]) for row, _ in poses})
    return rewritten, rewrites


def publish_poses(output: Path, poses, original: Path) -> None:
    # Feature coordinates belonged to the distorted image. This output is a
    # pose bundle, not a point cloud: clear both sides rather than dangling IDs.
    write_images(output / "images.txt", [(row, "") for row, _obs in poses])
    (output / "points3D.txt").write_text("")
    copy_auxiliary(original, output, {int(row[0]): int(row[0]) for row, _ in poses})
