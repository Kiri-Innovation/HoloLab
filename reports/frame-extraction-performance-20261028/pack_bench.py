#!/usr/bin/env python3
"""Actual host entrypoints, identical eight-camera workload, no gateway mutation."""

import concurrent.futures as cf
import json
import os
import resource
import subprocess
import sys
import time

from bench import DISK, ROOT, VIDEO

REPO = ROOT.parents[1]
for version, label in [("0.1.0", "pack_before"), ("0.1.1", "pack_after")]:
    root = DISK / label
    root.mkdir(parents=True, exist_ok=False)

    def one(i, root=root, version=version):
        out = root / str(i)
        cmd = [
            sys.executable,
            str(
                REPO
                / "hololab"
                / "builtin_packs"
                / f"frame-extraction@{version}"
                / "extract_frames.py"
            ),
            str(VIDEO / f"data{i}.mp4"),
            "--output",
            str(out),
            "--max-frames",
            "50",
            "--skip",
            "1",
            "--start-frame",
            "0",
            "--end-frame",
            "0",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        files = sorted(out.glob("frame_*.png"))
        assert [p.name for p in files] == [f"frame_{j:06d}.png" for j in range(50)]
        return dict(
            camera=i, command=cmd, bytes=sum(p.stat().st_size for p in files), count=len(files)
        )

    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    t = time.monotonic()
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        items = list(pool.map(one, range(1, 9)))
    elapsed = time.monotonic() - t
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    sync_start = time.monotonic()
    for path in root.glob("*/frame_*.png"):
        with path.open("rb") as fd:
            os.fsync(fd.fileno())
    sync_time = time.monotonic() - sync_start
    row = dict(
        version=version,
        p=8,
        n=8,
        frames=50,
        wall_s=elapsed,
        fsync_s=sync_time,
        durable_s=elapsed + sync_time,
        cpu_s=after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime,
        bytes=sum(i["bytes"] for i in items),
        items=items,
    )
    with (ROOT / "pack-measurements.jsonl").open("a") as fd:
        fd.write(json.dumps(row) + "\n")
    print(json.dumps({k: v for k, v in row.items() if k != "items"}), flush=True)
