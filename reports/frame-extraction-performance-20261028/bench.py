#!/usr/bin/env python3
"""Controlled experiments: one output, fixed workload, per-output frame caps."""

import concurrent.futures as cf
import json
import os
import pathlib
import resource
import shutil
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parent
VIDEO = pathlib.Path("/cloud/cloud-ssd1/Kiri4DGS/data/camarray/taogs/4K_Actor1_Magic_2")
DISK = pathlib.Path("/cloud/cloud-ssd1/Kiri4DGS/fe-research-scratch")
RAM = pathlib.Path("/dev/shm/fe-research-scratch")
RESULT = ROOT / "measurements.jsonl"


def run(label, p=1, d=1, e=1, f=1, n=8, frames=50, mode="null", where="ram", extra=(), keep=False):
    root = (RAM if where == "ram" else DISK) / label
    root.mkdir(parents=True, exist_ok=False)

    def one(i):
        out = root / str(i)
        out.mkdir()
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-filter_threads",
            str(f),
            "-threads:v",
            str(d),
            "-i",
            str(VIDEO / f"data{i}.mp4"),
            "-map",
            "0:v:0",
            "-an",
            "-frames:v",
            str(frames),
            "-threads:v",
            str(e),
        ]
        if mode == "null":
            cmd += ["-f", "null", "-"]
        elif mode == "pngnull":
            cmd += ["-c:v", "png", *extra, "-f", "null", "-"]
        else:
            cmd += [*extra, "-f", "image2", str(out / "f_%06d.png")]
        started = time.monotonic()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode:
            raise RuntimeError(proc.stderr)
        files = list(out.glob("*"))
        if mode not in ("null", "pngnull"):
            assert len(files) == frames, (label, i, len(files))
        return dict(
            i=i,
            seconds=time.monotonic() - started,
            bytes=sum(x.stat().st_size for x in files),
            count=len(files),
            command=cmd,
        )

    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    wall = time.monotonic()
    with cf.ThreadPoolExecutor(max_workers=p) as pool:
        items = list(pool.map(one, range(1, n + 1)))
    wall = time.monotonic() - wall
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime
    sync_time = None
    if where == "disk" and mode == "png":
        start = time.monotonic()
        for x in root.glob("*/*"):
            with x.open("rb") as fd:
                os.fsync(fd.fileno())
        sync_time = time.monotonic() - start
    result = dict(
        label=label,
        p=p,
        d=d,
        e=e,
        f=f,
        n=n,
        frames=frames,
        mode=mode,
        where=where,
        extra=extra,
        wall_s=wall,
        cpu_s=cpu,
        cpu_cores=cpu / wall,
        fsync_s=sync_time,
        bytes=sum(x["bytes"] for x in items),
        items=items,
    )
    with RESULT.open("a") as fd:
        fd.write(json.dumps(result) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "items"}), flush=True)
    if not keep:
        shutil.rmtree(root)
    return result


if __name__ == "__main__":
    for p, d in [(1, 1), (1, 2), (1, 4), (1, 8), (1, 16), (2, 4), (4, 2), (8, 1), (8, 2), (8, 4)]:
        run(f"decode_p{p}_d{d}", p=p, d=d)
    for extra, label in [
        ((), "default"),
        (("-compression_level", "1"), "c1"),
        (("-compression_level", "1", "-pred", "sub"), "c1sub"),
        (("-compression_level", "1", "-pred", "none"), "c1none"),
        (("-compression_level", "0"), "c0"),
    ]:
        run("single_" + label, n=1, frames=10, mode="png", extra=extra)
    for where in ["ram", "disk"]:
        run("png_p8_e1_" + where, p=8, mode="png", where=where)
    run("png_p8_auto", p=8, d=0, e=0, f=0, mode="png", keep=True)
    run("png_p8_null", p=8, mode="pngnull")
