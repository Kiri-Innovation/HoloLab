#!/usr/bin/env python3
"""Merge complete COLMAP reference domains and position-zipped image arrays."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

_PACK_DIR = Path(__file__).resolve().parent
if str(_PACK_DIR) not in sys.path:
    sys.path.insert(0, str(_PACK_DIR))

from array_positions import encode_position  # noqa: E402
from position_model import (  # noqa: E402
    camera_rows,
    image_files,
    read_images,
    validate_tracks,
    write_images,
)


def _parse_pose_rows(path: Path) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """Return (header_lines, [(line_no, tokenised_pose_row), …]) for images.txt.

    ``tokenised_pose_row`` retains the exact whitespace-split tokens so we
    can rewrite the last column (NAME) in-place without disturbing the
    pose numbers. Observation rows are not returned; callers that rewrite
    poses must retain the original following row using its line number.
    """
    raw = path.read_text().splitlines()
    header: list[str] = []
    poses: list[tuple[int, list[str]]] = []
    it = enumerate(raw)
    for i, ln in it:
        s = ln.strip()
        if not s:
            header.append(ln)
            continue
        if s.startswith("#"):
            header.append(ln)
            continue
        parts = s.split()
        if len(parts) < 10:
            continue
        poses.append((i, parts))
        try:
            next(it)  # obs line
        except StopIteration:
            break
    return header, poses


def _parse_names_from_images_txt(path: Path) -> list[str]:
    """Return the list of NAME fields from a COLMAP ``images.txt``, in file order."""
    _hdr, poses = _parse_pose_rows(path)
    return [row[9] for _line, row in poses]


def _sanitize_points3d_tracks(
    source: Path, target: Path, valid_image_ids: set[int]
) -> tuple[int, int]:
    """Strict copy: reject bad references rather than deleting tracks/points."""
    for line in source.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 8 or (len(fields) - 8) % 2:
            raise ValueError("malformed COLMAP point track")
        if any(int(fields[i]) not in valid_image_ids for i in range(8, len(fields), 2)):
            raise ValueError("track references absent IMAGE_ID")
    shutil.copyfile(source, target)
    return 0, 0


def _camera_rows(path: Path):
    return {cid: (row[1], list(map(float, row[4:]))) for cid, row in camera_rows(path).items()}


def _remap_observations(row, source_camera, target_camera):
    if source_camera == target_camera or not row.strip():
        return row
    import cv2
    import numpy as np

    source_model, source_params = source_camera
    target_model, target_params = target_camera
    if target_model != "PINHOLE":
        raise ValueError("observation remap requires PINHOLE destination")
    if source_model == "SIMPLE_RADIAL":
        f, cx, cy, k1 = source_params
        fx = fy = f
        distortion = [k1, 0, 0, 0]
    elif source_model in ("OPENCV", "PINHOLE"):
        fx, fy, cx, cy = source_params[:4]
        distortion = source_params[4:] if source_model == "OPENCV" else [0, 0, 0, 0]
    else:
        raise ValueError(f"unsupported observation camera: {source_model}")
    tx, ty, tcx, tcy = target_params
    k = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=float)
    nk = np.array([[tx, 0, tcx], [0, ty, tcy], [0, 0, 1]], dtype=float)
    fields = row.split()
    xy = np.array([[float(fields[i]), float(fields[i + 1])] for i in range(0, len(fields), 3)])
    mapped = cv2.undistortPoints(
        xy.reshape(-1, 1, 2), k, np.asarray(distortion, dtype=float), P=nk
    ).reshape(-1, 2)
    return " ".join(f"{x:.17g} {y:.17g} {fields[3 * i + 2]}" for i, (x, y) in enumerate(mapped))


def _validate_tracks(images_path: Path, points_path: Path) -> None:
    """Reject invalid indices and inconsistent reciprocal point references."""
    raw = images_path.read_text().splitlines()
    _, poses = _parse_pose_rows(images_path)
    observations = {}
    for line, row in poses:
        if line + 1 >= len(raw):
            raise ValueError(f"missing observation row for IMAGE_ID={row[0]}")
        fields = raw[line + 1].split()
        if len(fields) % 3:
            raise ValueError(f"malformed observations for IMAGE_ID={row[0]}")
        observations[int(row[0])] = fields
    references = set()
    for line in points_path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split()
        point_id = int(fields[0])
        for offset in range(8, len(fields), 2):
            image_id, index = map(int, fields[offset : offset + 2])
            obs = observations.get(image_id, [])
            if index < 0 or index >= len(obs) // 3:
                raise ValueError(
                    f"POINT3D_ID={point_id}: IMAGE_ID={image_id} "
                    f"POINT2D_IDX={index} outside {len(obs) // 3} observations"
                )
            if int(obs[index * 3 + 2]) != point_id:
                raise ValueError(
                    f"nonreciprocal track: point={point_id}, image={image_id}, index={index}"
                )
            references.add((image_id, index, point_id))
    for image_id, obs in observations.items():
        for index in range(len(obs) // 3):
            point_id = int(obs[index * 3 + 2])
            if point_id != -1 and (image_id, index, point_id) not in references:
                raise ValueError(
                    f"observation lacks reciprocal track: image={image_id}, index={index}, point={point_id}"
                )


def _remap_auxiliary_cameras(source: Path, target: Path, camera_ids: dict[int, int]) -> None:
    """Rewrite rig/frame sensor references along with the output camera table."""

    def mapped(value):
        if int(value) not in camera_ids:
            raise ValueError("rig/frame references absent CAMERA_ID")
        return str(camera_ids[int(value)])

    if (source / "rigs.txt").exists():
        lines = []
        for line in (source / "rigs.txt").read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                lines.append(line)
                continue
            fields = line.split()
            if len(fields) < 4:
                raise ValueError("malformed rig row")
            if fields[2] == "CAMERA":
                fields[3] = mapped(fields[3])
            i = 4
            for _ in range(int(fields[1]) - 1):
                if i + 2 >= len(fields):
                    raise ValueError("malformed rig sensor")
                if fields[i] == "CAMERA":
                    fields[i + 1] = mapped(fields[i + 1])
                if fields[i + 2] not in {"0", "1"}:
                    raise ValueError("invalid rig sensor pose flag")
                i += 3 + 7 * int(fields[i + 2])
            if i != len(fields):
                raise ValueError("malformed rig sensor pose")
            lines.append(" ".join(fields))
        (target / "rigs.txt").write_text("\n".join(lines) + "\n")
    if (source / "frames.txt").exists():
        lines = []
        for line in (source / "frames.txt").read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                lines.append(line)
                continue
            fields = line.split()
            if len(fields) < 10 or len(fields) != 10 + 3 * int(fields[9]):
                raise ValueError("malformed frame row")
            for i in range(10, len(fields), 3):
                if fields[i] == "CAMERA":
                    fields[i + 1] = mapped(fields[i + 1])
            lines.append(" ".join(fields))
        (target / "frames.txt").write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cams", type=Path, required=True)
    ap.add_argument("--points", type=Path, required=True)
    ap.add_argument("--images", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    source_poses = read_images(args.cams / "images.txt")
    target_rows = camera_rows(args.cams / "cameras.txt")
    target_cameras = _camera_rows(args.cams / "cameras.txt")
    valid_image_ids = {int(row[0]) for row, _obs in source_poses}
    tri_images = args.points / "images.txt"
    observations = {int(row[0]): obs for row, obs in source_poses}
    if tri_images.is_file():
        tri_poses = read_images(tri_images)
        tri_by_id = {int(row[0]): (row, obs) for row, obs in tri_poses}
        if set(tri_by_id) != valid_image_ids:
            raise ValueError("triangulation images must match the camera bundle image IDs")
        # Each side's CAMERA_ID resolves in its own table; equality of integers
        # never implies equality of intrinsics. IMAGE_ID equality above is strict.
        tri_rows = camera_rows(args.points / "cameras.txt")
        source_cameras = _camera_rows(args.points / "cameras.txt")
        for (row, _obs), (tri_row, tri_obs) in zip(source_poses, tri_poses, strict=True):
            if int(row[8]) not in target_rows or int(tri_row[8]) not in tri_rows:
                raise ValueError("image references absent CAMERA_ID")
            observations[int(row[0])] = _remap_observations(
                tri_obs, source_cameras[int(tri_row[8])], target_cameras[int(row[8])]
            )
    if any(int(row[8]) not in target_rows for row, _obs in source_poses):
        raise ValueError("image references absent CAMERA_ID")
    files = image_files(args.images) if args.images is not None else None
    if files is not None and len(files) != len(source_poses):
        raise ValueError(
            f"position length mismatch: images={len(files)}, poses={len(source_poses)}"
        )
    camera_ids = {cid: i + 1 for i, cid in enumerate(sorted(target_rows))}
    output_poses = []
    for position, (original, _obs) in enumerate(source_poses):
        row = list(original)
        row[8] = str(camera_ids[int(row[8])])
        if files is not None:
            row[9] = encode_position(position) + files[position].suffix
        output_poses.append((row, observations[int(row[0])]))
    sparse = args.out / "sparse" / "0"
    sparse.mkdir(parents=True, exist_ok=True)
    rows = []
    for cid in sorted(target_rows):
        row = list(target_rows[cid])
        row[0] = str(camera_ids[cid])
        rows.append(" ".join(row))
    (sparse / "cameras.txt").write_text("\n".join(rows) + "\n")
    write_images(sparse / "images.txt", output_poses)
    _sanitize_points3d_tracks(
        args.points / "points3D.txt", sparse / "points3D.txt", valid_image_ids
    )
    _validate_tracks(sparse / "images.txt", sparse / "points3D.txt")
    validate_tracks(sparse / "images.txt", sparse / "points3D.txt")
    _remap_auxiliary_cameras(args.cams, sparse, camera_ids)
    # Validate frame DATA_ID references while retaining IMAGE_ID values.
    from position_model import rewrite_frames

    if (sparse / "frames.txt").exists():
        validated = sparse / ".frames-validated"
        rewrite_frames(sparse / "frames.txt", validated, {iid: iid for iid in valid_image_ids})
        validated.replace(sparse / "frames.txt")
    if files is not None:
        images_out = args.out / "images"
        images_out.mkdir(parents=True, exist_ok=True)
        for source, (row, _obs) in zip(files, output_poses, strict=True):
            dest = images_out / row[9]
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            try:
                dest.symlink_to(source.resolve(strict=True))
            except OSError:
                shutil.copy2(source, dest)
    subprocess.run(
        [
            "colmap",
            "model_converter",
            "--input_path",
            str(sparse),
            "--output_path",
            str(sparse),
            "--output_type",
            "BIN",
        ],
        check=True,
    )
    print(f"[merge-colmap/0.5.0] done: {len(output_poses)} poses, complete positional domain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
