#!/usr/bin/env python3
"""Materialize XMP-timestamped rig images with colmap4d fixed-grid grouping."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from xml.etree import ElementTree as ET

import colmap4d
from colmap4d.groups import GridSample, group_by_grid
from timeline import render_exposure_timeline

MIN_COLMAP4D_VERSION = (1, 0, 2)
XMP_APP1_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
HOLOLAB_XMP_NS = "https://hololab.dev/ns/rig/1.0/"


def version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for part in value.split("."):
        digits = "".join(character for character in part if character.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def require_colmap4d_version() -> None:
    if version_tuple(colmap4d.__version__) < MIN_COLMAP4D_VERSION:
        minimum = ".".join(str(part) for part in MIN_COLMAP4D_VERSION)
        raise RuntimeError(f"colmap4d>={minimum} is required; found {colmap4d.__version__}")


def read_xmp(path: Path) -> dict[str, str]:
    data = path.read_bytes()
    position = 2 if data.startswith(b"\xff\xd8") else 0
    while position + 4 <= len(data) and data[position] == 0xFF:
        marker = data[position + 1]
        if marker == 0xDA:
            break
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            position += 2
            continue
        length = int.from_bytes(data[position + 2 : position + 4], "big")
        if length < 2 or position + 2 + length > len(data):
            break
        payload = data[position + 4 : position + 2 + length]
        if marker == 0xE1 and payload.startswith(XMP_APP1_HEADER):
            root = ET.fromstring(payload[len(XMP_APP1_HEADER) :])
            description = root.find(".//{http://www.w3.org/1999/02/22-rdf-syntax-ns/}Description")
            if description is None:
                return {}
            return {
                key.removeprefix(f"{{{HOLOLAB_XMP_NS}}}"): value
                for key, value in description.attrib.items()
                if key.startswith(f"{{{HOLOLAB_XMP_NS}}}")
            }
        position += 2 + length
    return {}


def load_frames(root: Path) -> list[dict]:
    records = []
    required = {"alias", "decodedIndex", "sensorTimestampNs", "wallClockEpochNs", "exposureNs"}
    for path in sorted(root.rglob("*.jpg")):
        metadata = read_xmp(path)
        missing = sorted(required - metadata.keys())
        if missing:
            raise ValueError(f"{path}: missing XMP fields {missing}")
        exposure_ns = int(metadata["exposureNs"])
        sensor_start_ns = int(metadata["sensorTimestampNs"])
        wall_start_ns = int(metadata["wallClockEpochNs"])
        records.append(
            {
                "device_id": metadata["alias"],
                "decoded_index": int(metadata["decodedIndex"]),
                "source_path": path.relative_to(root).as_posix(),
                "sensor_timestamp_ns": sensor_start_ns,
                "wall_clock_epoch_ns": wall_start_ns,
                "exposure_ns": exposure_ns,
                "sensor_mid_exposure_ns": sensor_start_ns + exposure_ns // 2,
                "t_ns": wall_start_ns + exposure_ns // 2,
                "cap_id": metadata.get("capId"),
                "source": metadata.get("source", "unknown"),
                "timebase": metadata.get("timebase", "unknown"),
            }
        )
    if not records:
        raise ValueError(f"{root}: no JPEG inputs")
    return records


def materialize(source_root: Path, output_root: Path, groups) -> tuple[list[dict], dict[str, int]]:
    frame_rows = []
    methods = {"hardlink": 0, "copy": 0}
    for output_index, group in enumerate(groups):
        group_name = f"g{output_index:06d}"
        group_dir = output_root / group_name
        group_dir.mkdir(parents=True, exist_ok=True)
        for member in group.members:
            record = member.value
            source = source_root / record["source_path"]
            target = group_dir / f"{record['device_id']}.jpg"
            try:
                os.link(source, target)
                method = "hardlink"
            except OSError:
                shutil.copy2(source, target)
                method = "copy"
            methods[method] += 1
            frame_rows.append(
                {
                    "group": group_name,
                    "group_output_index": output_index,
                    "grid_index": group.grid_index,
                    "t_center_ns": group.t_center_ns,
                    "device_id": record["device_id"],
                    "source_path": record["source_path"],
                    "output_path": target.relative_to(output_root).as_posix(),
                    **{
                        key: record[key]
                        for key in (
                            "decoded_index",
                            "sensor_timestamp_ns",
                            "sensor_mid_exposure_ns",
                            "wall_clock_epoch_ns",
                            "exposure_ns",
                            "t_ns",
                            "cap_id",
                            "source",
                        )
                    },
                    "materialization": method,
                }
            )
    return frame_rows, methods


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeline-output", type=Path, required=True)
    parser.add_argument("--grid-ns", type=int, default=33_333_333)
    parser.add_argument("--n-cams", type=int, default=0)
    parser.add_argument("--trim-edges", action="store_true")
    args = parser.parse_args()

    require_colmap4d_version()
    records = load_frames(args.frames)
    devices = sorted({record["device_id"] for record in records})
    expected_device_count = args.n_cams or len(devices)
    grouping = group_by_grid(
        [GridSample(record["device_id"], record["t_ns"], record) for record in records],
        args.grid_ns,
        trim_edges=args.trim_edges,
        expected_device_count=expected_device_count,
    )

    args.output.mkdir(parents=True, exist_ok=True)
    metadata_dir = args.output / ".colmap4d-grouping"
    metadata_dir.mkdir(exist_ok=True)
    frame_rows, methods = materialize(args.frames, args.output, grouping.groups)
    with (metadata_dir / "frames.jsonl").open("w", encoding="utf-8") as stream:
        for row in frame_rows:
            stream.write(json.dumps(row, sort_keys=True) + "\n")

    duplicate_drops = sum(drop.reason == "device_duplicate" for drop in grouping.discarded)
    trimmed_drops = sum(drop.reason == "trimmed_edge" for drop in grouping.discarded)
    manifest = {
        "format": "hololab.colmap4d-temporal-groups",
        "format_version": 1,
        "producer": {
            "pack": "colmap4d-temporal-grouping@0.1.1",
            "colmap4d_version": colmap4d.__version__,
            "minimum_colmap4d_version": "1.0.2",
        },
        "time": {
            "time_unit": "ns",
            "time_convention": "mid_exposure",
            "clock_domain": "hololab_clocksync_server_epoch",
            "input_timebases": sorted({record["timebase"] for record in records}),
            "midpoint_formula": "wallClockEpochNs + exposureNs // 2",
        },
        "grouping": {
            "method": "colmap4d.group_by_grid",
            "grid_ns": grouping.grid_ns,
            "grid_origin_ns": grouping.origin_ns,
            "trim_edges": grouping.trim_edges,
            "expected_device_count": grouping.expected_device_count,
            "devices": devices,
            "first_full_grid_index": grouping.first_full_grid_index,
            "last_full_grid_index": grouping.last_full_grid_index,
            "trimmed_head_buckets": grouping.trimmed_head_buckets,
            "trimmed_tail_buckets": grouping.trimmed_tail_buckets,
        },
        "statistics": {
            "input_frames": len(records),
            "retained_groups": len(grouping.groups),
            "retained_frames": len(frame_rows),
            "discarded_device_duplicates": duplicate_drops,
            "discarded_trimmed_edge_frames": trimmed_drops,
            "materialization": methods,
        },
        "members_file": "frames.jsonl",
    }
    (metadata_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    render_exposure_timeline(records, grouping, args.timeline_output)
    print(
        f"[ITER 1/1] grouped {len(records)} images into {len(grouping.groups)} retained buckets and rendered rig_timeline using colmap4d {colmap4d.__version__}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
