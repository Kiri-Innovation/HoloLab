#!/usr/bin/env python3
"""Assemble points + flat XMP images + cams into one colmap4d model."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

import colmap4d
from colmap4d import colmap_io
from colmap4d.validate import exit_code

XMP_APP1_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
HOLOLAB_XMP_NS = "https://hololab.dev/ns/rig/1.0/"
REQUIRED_XMP_FIELDS = {
    "aeMode",
    "alias",
    "capId",
    "clockAgeMs",
    "clockOffsetMs",
    "decodedIndex",
    "exposureNs",
    "focusDistance",
    "iso",
    "sensorTimestampNs",
    "source",
    "syncVersion",
    "timebase",
    "wallClockEpochNs",
}
JPEG_SUFFIXES = {".jpg", ".jpeg"}
FLAT_IMAGE_RE = re.compile(r"^(g[0-9]{6})__(cam[0-9]+)(\.jpe?g)$", re.IGNORECASE)


def read_xmp(path: Path) -> dict[str, str]:
    """Read HoloLab attributes from a JPEG standard-XMP APP1 segment."""
    data = path.read_bytes()
    if not data.startswith(b"\xff\xd8"):
        raise ValueError(f"{path}: not a JPEG file")
    position = 2
    while position + 4 <= len(data) and data[position] == 0xFF:
        marker = data[position + 1]
        if marker == 0xDA:
            break
        if marker == 0xD9:
            break
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            position += 2
            continue
        length = int.from_bytes(data[position + 2 : position + 4], "big")
        if length < 2 or position + 2 + length > len(data):
            raise ValueError(f"{path}: malformed JPEG segment at byte {position}")
        payload = data[position + 4 : position + 2 + length]
        if marker == 0xE1 and payload.startswith(XMP_APP1_HEADER):
            root = ET.fromstring(payload[len(XMP_APP1_HEADER) :])
            rdf_ns = "http://www.w3.org/1999/02/22-rdf-syntax-ns/"
            description = root.find(f".//{{{rdf_ns}}}Description")
            if description is None:
                break
            return {
                key.removeprefix(f"{{{HOLOLAB_XMP_NS}}}"): value
                for key, value in description.attrib.items()
                if key.startswith(f"{{{HOLOLAB_XMP_NS}}}")
            }
        position += 2 + length
    raise ValueError(f"{path}: missing HoloLab standard XMP APP1 metadata")


def _image_metadata(path: Path, expected_alias: str | None = None) -> dict[str, str | int]:
    metadata = read_xmp(path)
    missing = sorted(REQUIRED_XMP_FIELDS - metadata.keys())
    if missing:
        raise ValueError(f"{path}: missing HoloLab XMP fields {missing}")
    alias = metadata["alias"]
    expected = expected_alias if expected_alias is not None else path.stem
    if alias != expected:
        raise ValueError(f"{path}: XMP alias {alias!r} does not match expected alias {expected!r}")
    try:
        wall_ns = int(metadata["wallClockEpochNs"])
        exposure_ns = int(metadata["exposureNs"])
    except ValueError as exc:
        raise ValueError(f"{path}: timestamp XMP fields must be integers") from exc
    if exposure_ns < 0:
        raise ValueError(f"{path}: exposureNs must be non-negative")
    return {
        **metadata,
        "wall_clock_epoch_ns": wall_ns,
        "exposure_ns": exposure_ns,
        "t_ns": wall_ns + exposure_ns // 2,
    }


def _group_dirs(root: Path) -> dict[str, Path]:
    groups = {
        path.name: path
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    }
    if not groups:
        raise ValueError(f"{root}: no array elements found")
    return groups


def _flat_group_images(root: Path) -> dict[str, dict[str, Path]]:
    """Decode root-level flattened image elements into a checked group map."""
    groups: dict[str, dict[str, Path]] = {}
    visible = sorted(path for path in root.iterdir() if not path.name.startswith("."))
    if not visible:
        raise ValueError(f"{root}: no one-dimensional image elements found")
    for path in visible:
        if not path.is_file():
            raise ValueError(f"{path}: one-dimensional image array requires root-level files")
        match = FLAT_IMAGE_RE.fullmatch(path.name)
        if match is None:
            raise ValueError(
                f"{path}: expected flattened name gXXXXXX__camN.jpg; run flatten-array@0.1.0 first"
            )
        group, alias, _suffix = match.groups()
        by_alias = groups.setdefault(group, {})
        if alias in by_alias:
            raise ValueError(f"{root}: duplicate flattened member {group}/{alias}")
        by_alias[alias] = path
    return groups


def _link_or_copy(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
        return "hardlink"
    except OSError:
        shutil.copy2(source, target)
        return "copy"


def _camera_signature(camera: colmap_io.Camera) -> tuple:
    return (camera.model, camera.width, camera.height, tuple(camera.params))


def _clock_domain(timebase: str) -> str:
    # This is the canonical domain already emitted by temporal-grouping 0.1.1.
    # XMP's shorter value names the same synchronized server epoch.
    aliases = {"server_epoch": "hololab_clocksync_server_epoch"}
    return aliases.get(timebase, timebase)


def merge(points_root: Path, images_root: Path, cams_root: Path, output: Path) -> dict:
    point_groups = _group_dirs(points_root)
    image_groups = _flat_group_images(images_root)
    camera_groups = _group_dirs(cams_root)
    if point_groups.keys() != image_groups.keys() or point_groups.keys() != camera_groups.keys():
        raise ValueError(
            "group axes differ: "
            f"points={sorted(point_groups)} images={sorted(image_groups)} cams={sorted(camera_groups)}"
        )

    cameras: dict[int, colmap_io.Camera] = {}
    camera_by_alias: dict[str, int] = {}
    camera_signatures: dict[str, tuple] = {}
    images: dict[int, colmap_io.Image] = {}
    points: dict[int, colmap_io.Point3D] = {}
    image_times: dict[int, int] = {}
    point_times: dict[int, int] = {}
    devices: dict[str, dict[str, list[int]]] = defaultdict(lambda: {"camera_ids": []})
    materialization: dict[str, int] = defaultdict(int)
    input_timebases: set[str] = set()
    incomplete_groups: dict[str, int] = {}
    dropped_track_elements = 0
    next_image_id = next_point_id = 1

    group_names = sorted(point_groups)
    for frame_index, group in enumerate(group_names):
        camera_dir = camera_groups[group]
        group_cameras = colmap_io.read_cameras_txt(camera_dir / "cameras.txt")
        group_image_rows = colmap_io.read_images_txt(camera_dir / "images.txt")
        group_points = colmap_io.read_points3D_txt(point_groups[group] / "points3D.txt")
        source_images = image_groups[group]

        row_aliases = {Path(row.name).stem for row in group_image_rows.values()}
        if row_aliases != source_images.keys():
            raise ValueError(
                f"{group}: cams/image aliases differ: cams={sorted(row_aliases)} "
                f"images={sorted(source_images)}"
            )
        if len(source_images) < 7:
            incomplete_groups[group] = len(source_images)

        image_map: dict[int, int] = {}
        point_map = {
            old_id: next_point_id + offset for offset, old_id in enumerate(sorted(group_points))
        }
        next_point_id += len(point_map)
        group_times: list[int] = []

        for old_image_id in sorted(group_image_rows):
            source_row = group_image_rows[old_image_id]
            alias = Path(source_row.name).stem
            source_image = source_images[alias]
            metadata = _image_metadata(source_image, alias)
            if metadata["alias"] != alias:
                raise ValueError(
                    f"{group}: XMP alias {metadata['alias']!r} does not match cams NAME {alias!r}"
                )
            input_timebases.add(str(metadata["timebase"]))

            source_camera = group_cameras[source_row.camera_id]
            signature = _camera_signature(source_camera)
            if alias in camera_by_alias:
                if camera_signatures[alias] != signature:
                    raise ValueError(f"{group}/{alias}: camera calibration changed across groups")
                new_camera_id = camera_by_alias[alias]
            else:
                new_camera_id = len(cameras) + 1
                camera_by_alias[alias] = new_camera_id
                camera_signatures[alias] = signature
                cameras[new_camera_id] = colmap_io.Camera(
                    new_camera_id,
                    source_camera.model,
                    source_camera.width,
                    source_camera.height,
                    list(source_camera.params),
                )
            devices[alias]["camera_ids"].append(new_camera_id)

            new_image_id = next_image_id
            next_image_id += 1
            image_map[old_image_id] = new_image_id
            t_ns = int(metadata["t_ns"])
            image_times[new_image_id] = t_ns
            group_times.append(t_ns)
            image_name = f"{alias}{source_image.suffix}"
            images[new_image_id] = colmap_io.Image(
                new_image_id,
                f"{group}/{image_name}",
                new_camera_id,
                list(source_row.qvec),
                list(source_row.tvec),
                # The triangulator's POINT2D_IDX addresses its database keypoint
                # table, not the older SfM-prior observations carried by cams.
                # merge-colmap@0.4.0 intentionally publishes blank observation
                # rows for the same reason; retain that established contract.
                [],
                [],
            )
            method = _link_or_copy(source_image.resolve(), output / "images" / group / image_name)
            materialization[method] += 1

        for old_point_id in sorted(group_points):
            source_point = group_points[old_point_id]
            new_point_id = point_map[old_point_id]
            track = [
                (image_map[image_id], point2d_idx)
                for image_id, point2d_idx in source_point.track
                if image_id in image_map
            ]
            dropped_track_elements += len(source_point.track) - len(track)
            if not track:
                raise ValueError(f"{group}: point {old_point_id} has no valid observations")
            points[new_point_id] = colmap_io.Point3D(
                new_point_id,
                list(source_point.xyz),
                list(source_point.rgb),
                source_point.error,
                track,
            )
            observed = [image_times[image_id] for image_id, _ in track] or group_times
            point_times[new_point_id] = (sum(observed) + len(observed) // 2) // len(observed)

        print(f"[ITER {frame_index + 1}/{len(group_names)}] merged {group}", flush=True)

    if len(input_timebases) != 1:
        raise ValueError(f"mixed XMP timebase values: {sorted(input_timebases)}")
    source_timebase = next(iter(input_timebases))
    sparse = output / "sparse" / "0"
    sparse.mkdir(parents=True, exist_ok=True)
    colmap_io.write_cameras_bin(sparse / "cameras.bin", cameras)
    colmap_io.write_images_bin(sparse / "images.bin", images)
    colmap_io.write_points3D_bin(sparse / "points3D.bin", points)
    colmap4d.write_times_bin(sparse / "times.bin", image_times)
    colmap4d.write_points_t_bin(sparse / "points_t.bin", point_times)
    colmap4d.write_time_meta(
        sparse / "time_meta.json",
        {
            "colmap4d_spec": "1.0",
            "time_convention": "mid_exposure",
            "time_unit": "ns",
            "clock_domain": _clock_domain(source_timebase),
            "points_t_method": "track_centroid",
            "devices": {
                alias: {"camera_ids": sorted(set(value["camera_ids"]))}
                for alias, value in sorted(devices.items())
            },
            "conversion": {
                "source": "hololab.merge-colmap4d@0.1.3",
                "source_groups": len(group_names),
                "input_timebase": source_timebase,
                "midpoint_formula": "wallClockEpochNs + exposureNs // 2",
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
        "source_groups": len(group_names),
        "images": len(images),
        "points": len(points),
        "cameras": len(cameras),
        "physical_devices": sorted(camera_by_alias),
        "input_timebase": source_timebase,
        "t_min_ns": min(image_times.values()),
        "t_max_ns": max(image_times.values()),
        "image_materialization": dict(materialization),
        "incomplete_groups": incomplete_groups,
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
    parser.add_argument("--points", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--cams", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = merge(
        args.points.resolve(), args.images.resolve(), args.cams.resolve(), args.output.resolve()
    )
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
