#!/usr/bin/env python3
"""Merge per-group COLMAP folders into one scalar colmap4d model."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import defaultdict
from pathlib import Path

import colmap4d
from colmap4d import colmap_io
from colmap4d.validate import exit_code


def _rows(root: Path) -> tuple[dict, dict[tuple[str, str], dict]]:
    metadata = root / ".colmap4d-grouping"
    manifest = json.loads((metadata / "manifest.json").read_text(encoding="utf-8"))
    rows = {}
    for line in (
        (metadata / manifest.get("members_file", "frames.jsonl"))
        .read_text(encoding="utf-8")
        .splitlines()
    ):
        row = json.loads(line)
        key = (row["group"], row["device_id"])
        if key in rows:
            raise ValueError(f"duplicate temporal member {key}")
        rows[key] = row
    return manifest, rows


def _link_or_copy(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
        return "hardlink"
    except OSError:
        shutil.copy2(source, target)
        return "copy"


def merge(colmap_frames: Path, temporal_groups: Path, output: Path) -> dict:
    grouping_manifest, time_rows = _rows(temporal_groups)
    frame_dirs = [
        path
        for path in sorted(colmap_frames.iterdir())
        if path.is_dir() and not path.name.startswith(".")
    ]
    if not frame_dirs:
        raise ValueError("no arrayed<colmap-folder> elements found")

    cameras = {}
    images = {}
    points = {}
    image_times = {}
    point_times = {}
    devices = defaultdict(lambda: {"camera_ids": []})
    materialization = defaultdict(int)
    dropped_track_elements = 0
    next_camera_id = next_image_id = next_point_id = 1

    for frame_index, frame_dir in enumerate(frame_dirs):
        group = frame_dir.name
        model_dir = frame_dir / "sparse" / "0"
        base = colmap4d.read_base_model(model_dir)
        camera_map = {}
        image_map = {}
        point_map = {
            old_id: next_point_id + offset for offset, old_id in enumerate(sorted(base.points3D))
        }
        next_point_id += len(point_map)

        for old_id in sorted(base.cameras):
            camera = base.cameras[old_id]
            new_id = next_camera_id
            next_camera_id += 1
            camera_map[old_id] = new_id
            cameras[new_id] = colmap_io.Camera(
                new_id, camera.model, camera.width, camera.height, list(camera.params)
            )

        for old_id in sorted(base.images):
            image = base.images[old_id]
            device_id = Path(image.name).stem
            row = time_rows.get((group, device_id))
            if row is None:
                raise ValueError(
                    f"no timestamp for group={group} image={image.name} device={device_id}"
                )
            new_id = next_image_id
            next_image_id += 1
            image_map[old_id] = new_id
            new_camera_id = camera_map[image.camera_id]
            new_name = f"{group}/{Path(image.name).name}"
            images[new_id] = colmap_io.Image(
                new_id,
                new_name,
                new_camera_id,
                list(image.qvec),
                list(image.tvec),
                list(image.xys),
                [point_map.get(pid, colmap_io.INVALID_POINT3D_ID) for pid in image.point3D_ids],
            )
            image_times[new_id] = int(row["t_ns"])
            devices[device_id]["camera_ids"].append(new_camera_id)
            source_image = frame_dir / "images" / Path(image.name).name
            if not source_image.is_file():
                raise FileNotFoundError(source_image)
            method = _link_or_copy(source_image.resolve(), output / "images" / new_name)
            materialization[method] += 1

        group_fallback_times = [image_times[new_id] for new_id in image_map.values()]
        for old_id in sorted(base.points3D):
            point = base.points3D[old_id]
            new_id = point_map[old_id]
            valid_track = [
                (image_map[image_id], point2d_idx)
                for image_id, point2d_idx in point.track
                if image_id in image_map
            ]
            dropped_track_elements += len(point.track) - len(valid_track)
            track = valid_track
            points[new_id] = colmap_io.Point3D(
                new_id, list(point.xyz), list(point.rgb), point.error, track
            )
            observed = [image_times[image_id] for image_id, _ in track]
            if not observed:
                observed = group_fallback_times
            point_times[new_id] = (sum(observed) + len(observed) // 2) // len(observed)

        print(f"[ITER {frame_index + 1}/{len(frame_dirs)}] merged {group}", flush=True)

    sparse = output / "sparse" / "0"
    sparse.mkdir(parents=True, exist_ok=True)
    colmap_io.write_cameras_bin(sparse / "cameras.bin", cameras)
    colmap_io.write_images_bin(sparse / "images.bin", images)
    colmap_io.write_points3D_bin(sparse / "points3D.bin", points)
    colmap4d.write_times_bin(sparse / "times.bin", image_times)
    colmap4d.write_points_t_bin(sparse / "points_t.bin", point_times)

    time_info = grouping_manifest["time"]
    colmap4d.write_time_meta(
        sparse / "time_meta.json",
        {
            "colmap4d_spec": "1.0",
            "time_convention": time_info["time_convention"],
            "time_unit": "ns",
            "clock_domain": time_info["clock_domain"],
            "points_t_method": "track_centroid",
            "devices": {
                key: {"camera_ids": sorted(set(value["camera_ids"]))}
                for key, value in sorted(devices.items())
            },
            "conversion": {
                "source": "hololab.merge-colmap4d",
                "source_groups": len(frame_dirs),
                "grouping": grouping_manifest["grouping"],
            },
        },
    )
    problems = colmap4d.validate_full(sparse)
    if exit_code(problems, strict=True):
        raise RuntimeError("colmap4d validation failed:\n" + "\n".join(map(str, problems)))

    hololab = output / ".hololab"
    hololab.mkdir(exist_ok=True)
    assembly = {
        "format": "hololab.colmap4d",
        "format_version": 1,
        "source_groups": len(frame_dirs),
        "images": len(images),
        "points": len(points),
        "t_min_ns": min(image_times.values()),
        "t_max_ns": max(image_times.values()),
        "image_materialization": dict(materialization),
        "dropped_dangling_track_elements": dropped_track_elements,
        "validation_problems": [str(problem) for problem in problems],
    }
    (hololab / "assembly.json").write_text(
        json.dumps(assembly, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / ".hololab-done").write_text("ok\n", encoding="utf-8")
    return assembly


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--colmap-frames", type=Path, required=True)
    parser.add_argument("--temporal-groups", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = merge(
        args.colmap_frames.resolve(), args.temporal_groups.resolve(), args.output.resolve()
    )
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
