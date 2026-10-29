"""Mock ffmpeg, preserve bytes and window options in the positional extractor."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from hololab.array_positions import decode_position, encode_position

PACK = Path(__file__).resolve().parents[1] / "hololab/builtin_packs/frame-extraction@0.2.0"


def load():
    spec = importlib.util.spec_from_file_location("frame_positions", PACK / "extract_frames.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_output_local_positions_and_source_selection(tmp_path, monkeypatch):
    mod = load()
    calls = []

    def run(cmd, **_kwargs):
        calls.append(cmd)
        if cmd[0] == "ffprobe":
            return SimpleNamespace(stdout="nb_frames=20\nr_frame_rate=30/1\n")
        for i in [3, 1, 2]:
            Path(cmd[-1] % i).write_bytes(bytes([i]))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod, "_detect_hdr_transfer", lambda p: None)
    output = tmp_path / encode_position(8)
    assert (
        mod.extract_frames(
            tmp_path / "input.mp4", output, start_frame=3, end_frame=12, frame_skip=2
        )
        == 3
    )
    paths = sorted(p for p in output.iterdir() if not p.name.startswith("."))
    assert [decode_position(p.stem) for p in paths] == [0, 1, 2]
    assert [p.read_bytes() for p in paths] == [b"\x01", b"\x02", b"\x03"]
    assert all(p.name == encode_position(i) + ".png" for i, p in enumerate(paths))
    assert "gte(n" in " ".join(calls[-1])
    assert mod.extract_frames(tmp_path / "input.mp4", output) == 3
    assert len(calls) == 2
    manifest = yaml.safe_load((PACK / "manifest.yaml").read_text())
    preview = manifest["previews"][0]["path"].replace("{{ outputs.frame_sequence }}", str(output))
    assert Path(preview).is_file()
    assert Path(preview).name == encode_position(0) + ".png"


def test_reject_existing_nonconsecutive_slots(tmp_path):
    (tmp_path / (encode_position(1) + ".png")).touch()
    with pytest.raises(ValueError, match="consecutive"):
        load().extract_frames(tmp_path / "in.mp4", tmp_path)


def test_no_old_frame_name_writes():
    text = (PACK / "extract_frames.py").read_text()
    assert 'f"frame_{saved:' not in text
    assert "encode_position(saved)" in text
