"""Positional filesystem/COLMAP adapter; vendored unchanged by the four packs.

External pose order is ascending IMAGE_ID, never NAME. Internal IDs remain
references rather than array identities. Only exports of our own staging decode
position names; external model import never requires HoloLab filenames.
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
from pathlib import Path

from array_positions import decode_position, encode_position, enumerate_array_entries


def image_files(root: Path) -> list[Path]:
    files = [root] if root.is_file() else enumerate_array_entries(root)
    if not files or any(not p.is_file() for p in files):
        raise ValueError("expected one nonempty image array layer of files, not nested bundles")
    return files


def stage_images(source: Path, output: Path) -> list[Path]:
    files = image_files(source)
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    staged = []
    for i, source_file in enumerate(files):
        dest = output / (encode_position(i) + source_file.suffix)
        real = source_file.resolve(strict=True)
        try:
            os.link(real, dest)
        except OSError:
            shutil.copy2(real, dest)
        staged.append(dest)
    return staged


def read_images(path: Path) -> list[tuple[list[str], str]]:
    lines = path.read_text().splitlines()
    records = []
    i = 0
    seen = set()
    while i < len(lines):
        if not lines[i].strip() or lines[i].lstrip().startswith("#"):
            i += 1
            continue
        row = lines[i].split(maxsplit=9)
        if len(row) != 10 or i + 1 >= len(lines):
            raise ValueError(f"invalid COLMAP image/observation pair in {path}")
        iid, cid = int(row[0]), int(row[8])
        if (
            iid <= 0
            or cid <= 0
            or iid in seen
            or not all(math.isfinite(float(x)) for x in row[1:8])
        ):
            raise ValueError("invalid or duplicate image reference/pose")
        seen.add(iid)
        obs = lines[i + 1]
        values = obs.split()
        if len(values) % 3:
            raise ValueError("invalid observation triples")
        for j in range(0, len(values), 3):
            if not all(math.isfinite(float(x)) for x in values[j : j + 2]):
                raise ValueError("nonfinite observation")
            int(values[j + 2])
        records.append((row, obs))
        i += 2
    if not records:
        raise ValueError("model contains no registered image poses")
    return sorted(records, key=lambda record: int(record[0][0]))


def camera_rows(path: Path) -> dict[int, list[str]]:
    result = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        row = line.split()
        if len(row) < 5:
            raise ValueError("malformed camera row")
        cid = int(row[0])
        if (
            cid <= 0
            or cid in result
            or min(map(int, row[2:4])) <= 0
            or not all(math.isfinite(float(x)) for x in row[4:])
        ):
            raise ValueError("invalid or duplicate camera reference")
        result[cid] = row
    return result


def paired_poses(model: Path, files: list[Path]):
    poses = read_images(model / "images.txt")
    if len(poses) != len(files):
        raise ValueError(f"position length mismatch: images={len(files)}, poses={len(poses)}")
    cameras = camera_rows(model / "cameras.txt")
    for row, _ in poses:
        if int(row[8]) not in cameras:
            raise ValueError(f"missing CAMERA_ID={row[8]}")
    return poses


def write_images(path: Path, poses) -> None:
    path.write_text(
        "# Positional image list; IDs are internal references\n"
        + "".join(" ".join(row) + "\n" + obs + "\n" for row, obs in poses)
    )


def rewrite_frames(source: Path, target: Path, image_ids: dict[int, int]) -> None:
    """COLMAP4 frame DATA_ID references use IMAGE_ID for CAMERA sensors."""
    lines = []
    for line in source.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            lines.append(line)
            continue
        fields = line.split()
        if len(fields) < 10 or len(fields) != 10 + 3 * int(fields[9]):
            raise ValueError("malformed COLMAP frame data")
        for start in range(10, len(fields), 3):
            if fields[start] == "CAMERA":
                old = int(fields[start + 2])
                if old not in image_ids:
                    raise ValueError("frame references absent IMAGE_ID")
                fields[start + 2] = str(image_ids[old])
        lines.append(" ".join(fields))
    target.write_text("\n".join(lines) + "\n")


def copy_auxiliary(source: Path, target: Path, image_ids: dict[int, int]) -> None:
    if (source / "rigs.txt").exists():
        shutil.copyfile(source / "rigs.txt", target / "rigs.txt")
    if (source / "frames.txt").exists():
        rewrite_frames(source / "frames.txt", target / "frames.txt", image_ids)


def validate_tracks(images: Path, points: Path) -> None:
    """Validate both directions before any rewrite; never clean away bad tracks."""
    observations = {int(row[0]): obs.split() for row, obs in read_images(images)}
    references = set()
    point_ids = set()
    for line in points.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 8 or (len(fields) - 8) % 2:
            raise ValueError("malformed point track")
        pid = int(fields[0])
        if pid <= 0 or pid in point_ids:
            raise ValueError("invalid or duplicate POINT3D_ID")
        point_ids.add(pid)
        for i in range(8, len(fields), 2):
            iid, index = int(fields[i]), int(fields[i + 1])
            obs = observations.get(iid, [])
            if index < 0 or 3 * index + 2 >= len(obs) or int(obs[3 * index + 2]) != pid:
                raise ValueError("invalid or nonreciprocal point track reference")
            key = (iid, index, pid)
            if key in references:
                raise ValueError("duplicate point track reference")
            references.add(key)
    for iid, obs in observations.items():
        for index in range(len(obs) // 3):
            pid = int(obs[3 * index + 2])
            if pid != -1 and (iid, index, pid) not in references:
                raise ValueError("observation lacks reciprocal point track")


def export_sfm(model: Path, staged: Path, cams_out: Path, points_out: Path) -> None:
    files = image_files(staged)
    poses = paired_poses(model, files)
    validate_tracks(model / "images.txt", model / "points3D.txt")
    by_position = {}
    image_ids = {}
    for row, obs in poses:
        position = decode_position(Path(row[9]).stem)
        if position >= len(files) or row[9] != files[position].name or position in by_position:
            raise ValueError("SfM model does not cover each staged position exactly once")
        image_ids[int(row[0])] = position + 1
        row[0] = str(position + 1)
        by_position[position] = (row, obs)
    if set(by_position) != set(range(len(files))):
        raise ValueError("SfM registration is incomplete; mask handling is not implemented")
    points = []
    for line in (model / "points3D.txt").read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            points.append(line)
            continue
        fields = line.split()
        if len(fields) < 8 or (len(fields) - 8) % 2:
            raise ValueError("malformed point track")
        for i in range(8, len(fields), 2):
            old = int(fields[i])
            if old not in image_ids:
                raise ValueError("track references absent IMAGE_ID")
            fields[i] = str(image_ids[old])
        points.append(" ".join(fields))
    cams_out.mkdir(parents=True, exist_ok=True)
    points_out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(model / "cameras.txt", cams_out / "cameras.txt")
    write_images(cams_out / "images.txt", [by_position[i] for i in range(len(files))])
    (points_out / "points3D.txt").write_text("\n".join(points) + "\n")
    copy_auxiliary(model, cams_out, image_ids)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("stage", "export"))
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()
    paths = list(map(Path, args.paths))
    if args.operation == "stage":
        stage_images(*paths)
    else:
        export_sfm(*paths)


if __name__ == "__main__":
    main()
