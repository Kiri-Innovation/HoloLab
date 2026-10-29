"""Assemble local video paths in explicit order without modifying source bytes."""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}


def source(raw, index):
    if not isinstance(raw, str) or not raw or "\0" in raw:
        raise ValueError(f"paths_json[{index}]: expected a nonempty absolute path string")
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError(f"paths_json[{index}]: path must be absolute on the selected node")
    if path.is_dir():
        candidates = sorted(
            p
            for p in path.iterdir()
            if not p.name.startswith(".") and p.is_file() and p.suffix.lower() in VIDEO_EXT
        )
        if len(candidates) != 1:
            raise ValueError(
                f"paths_json[{index}]: expected exactly one video in {path}; found {len(candidates)}"
            )
        path = candidates[0]
    if not path.is_file() or path.suffix.lower() not in VIDEO_EXT:
        raise ValueError(f"paths_json[{index}]: missing or unsupported video file: {path}")
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode or not json.loads(result.stdout).get("streams"):
        raise ValueError(f"paths_json[{index}]: no readable video stream: {path}")
    return path.resolve()


def run(config):
    params = config["params"]
    paths = params["paths"] if "paths" in params else json.loads(params["paths_json"])
    if not isinstance(paths, list) or not paths:
        raise ValueError("paths_json must be a nonempty JSON array of absolute video paths")
    clips = [source(raw, index) for index, raw in enumerate(paths)]
    out = Path(config["outputs"]["videos"])
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError("output directory must be empty; use a fresh job output")
    stage = Path(tempfile.mkdtemp(prefix=".assemble-", dir=out.parent))
    try:
        width = max(4, len(str(len(clips) - 1)))
        for index, clip in enumerate(clips):
            layer = stage / f"{index:0{width}d}"
            layer.mkdir()
            (layer / clip.name).symlink_to(clip)
        stage.replace(out)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print(f"[ITER 1/1] assembled {len(clips)} video layers", flush=True)


if __name__ == "__main__":
    try:
        run(json.load(sys.stdin))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Video array assemble: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
