"""Small CPU-only ffmpeg helpers. No shell interpolation of user data."""

import json
import math
import subprocess
import sys
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}


def entries(path):
    return sorted(
        (p for p in Path(path).iterdir() if not p.name.startswith(".")), key=lambda p: p.name
    )


def video(path):
    p = Path(path)
    candidates = (
        [p]
        if p.is_file()
        else [f for f in entries(p) if f.is_file() and f.suffix.lower() in VIDEO_EXT]
    )
    if len(candidates) != 1:
        raise ValueError(f"Expected exactly one video in {p}; found {len(candidates)}")
    return candidates[0].resolve()


def probe(path):
    data = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            text=True,
        )
    )
    stream = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if not stream:
        raise ValueError(f"No video stream: {path}")
    return stream, float(data.get("format", {}).get("duration", 0))


def number(value, name, minimum=0, positive=False):
    value = float(value)
    if not math.isfinite(value) or value < minimum or (positive and value == 0):
        raise ValueError(f"{name} must be finite and {'>' if positive else '>='} {minimum}")
    return value


def encode(args, output, filters=None):
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    target = out / "output.mp4"
    temporary = out / ".encoding.mp4"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-filter_threads",
        "2",
        "-filter_complex_threads",
        "2",
        *args,
    ]
    if filters:
        cmd += ["-vf", filters]
    cmd += [
        "-an",
        "-c:v",
        "libx264",
        "-threads",
        "2",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(temporary),
    ]
    try:
        subprocess.run(cmd, check=True)
        _, duration = probe(temporary)
        if duration <= 0:
            raise ValueError("No output frames; check the requested time range")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    print("[ITER 1/1] video ready", flush=True)


def main(run):
    try:
        config = json.load(sys.stdin)
        run(config["inputs"], config["params"], config["outputs"]["video"])
    except (ValueError, OSError, subprocess.CalledProcessError, KeyError) as exc:
        print(f"Video pack: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
