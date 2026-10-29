"""The faster bundled version preserves selection, numbering and decoded pixels."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "hololab" / "builtin_packs"


def load(version):
    spec = importlib.util.spec_from_file_location(
        "extract_frames_" + version, ROOT / f"frame-extraction@{version}" / "extract_frames.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ffmpeg(*args):
    return subprocess.check_output(["ffmpeg", "-v", "error", "-y", *map(str, args)])


def rgb(directory):
    return ffmpeg(
        "-threads",
        "1",
        "-i",
        directory / "frame_%06d.png",
        "-threads",
        "1",
        "-pix_fmt",
        "rgb24",
        "-f",
        "rawvideo",
        "-",
    )


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
@pytest.mark.parametrize(
    ("transfer", "kwargs", "indices"),
    [
        (None, {"max_frames": 7}, list(range(7))),
        (None, {"start_frame": 3, "end_frame": 15, "frame_skip": 4}, [3, 7, 11]),
        (None, {"max_width": 48, "max_frames": 3}, None),
        ("arib-std-b67", {"max_frames": 3}, None),
        ("smpte2084", {"max_frames": 3}, None),
    ],
)
def test_lossless_pixels_and_source_selection(tmp_path, transfer, kwargs, indices):
    source = tmp_path / "input.mp4"
    color = (
        ["-color_trc", transfer, "-color_primaries", "bt2020", "-colorspace", "bt2020nc"]
        if transfer
        else []
    )
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=96x64:rate=30",
        "-frames:v",
        "18",
        "-c:v",
        "libx264",
        "-threads",
        "1",
        "-pix_fmt",
        "yuv420p",
        *color,
        source,
    )
    before, after = tmp_path / "before", tmp_path / "after"
    counts = [
        load(version).extract_frames(source, output, **kwargs)
        for version, output in [("0.1.0", before), ("0.1.1", after)]
    ]
    assert counts[0] == counts[1]
    expected_names = [f"frame_{i:06d}.png" for i in range(counts[0])]
    for directory in (before, after):
        assert sorted(p.name for p in directory.iterdir()) == expected_names
    old_pixels, new_pixels = rgb(before), rgb(after)
    assert old_pixels == new_pixels
    if indices is not None:
        decoded = ffmpeg(
            "-filter_threads",
            "1",
            "-threads",
            "1",
            "-i",
            source,
            "-threads",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        )
        frame_bytes = 96 * 64 * 3
        assert new_pixels == b"".join(
            decoded[i * frame_bytes : (i + 1) * frame_bytes] for i in indices
        )


def test_new_pack_identity_and_defaults():
    from hololab.bundled import verified_record
    from hololab.manifest import load_manifest

    path = ROOT / "frame-extraction@0.1.1" / "manifest.yaml"
    manifest, _ = load_manifest(path)
    assert manifest.params["png_compression"].default == 1
    assert manifest.params["png_predictor"].default == "sub"
    assert verified_record(path)["sha256"]
