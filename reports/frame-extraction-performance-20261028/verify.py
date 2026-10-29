#!/usr/bin/env python3
"""Compare every output RGB pixel plus frame names/count against old pack and source."""

import hashlib
import json
import pathlib
import subprocess

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent
SCRATCH = pathlib.Path("/cloud/cloud-ssd1/Kiri4DGS/fe-research-scratch")
VIDEO = pathlib.Path("/cloud/cloud-ssd1/Kiri4DGS/data/camarray/taogs/4K_Actor1_Magic_2")


def pixels(p):
    with Image.open(p) as im:
        assert im.mode == "RGB" and im.size == (3840, 2160), (p, im.mode, im.size)
        return hashlib.sha256(im.tobytes()).hexdigest()


def main():
    rows = []
    for cam in range(1, 9):
        dirs = [SCRATCH / label / str(cam) for label in ["pack_before", "pack_after"]]
        files = [sorted(p.glob("frame_*.png")) for p in dirs]
        names = [f"frame_{i:06d}.png" for i in range(50)]
        for fs in files:
            assert [f.name for f in fs] == names
        hashes = [[pixels(p) for p in fs] for fs in files]
        assert hashes[0] == hashes[1], cam
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-filter_threads",
            "1",
            "-threads:v",
            "1",
            "-i",
            str(VIDEO / f"data{cam}.mp4"),
            "-map",
            "0:v:0",
            "-frames:v",
            "50",
            "-pix_fmt",
            "rgb24",
            "-threads:v",
            "1",
            "-f",
            "framehash",
            "-hash",
            "sha256",
            "-",
        ]
        output = subprocess.check_output(cmd, text=True)
        source = [
            line.split(",")[-1].strip()
            for line in output.splitlines()
            if line and not line.startswith("#")
        ]
        assert len(source) == 50 and source == hashes[0], cam
        rows.append(
            dict(
                camera=cam, frames=50, RGB_SHA256=source, source_equal=True, before_after_equal=True
            )
        )
        print("verified", cam, flush=True)
    (ROOT / "pixel-verification.json").write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
