"""New source pack: temporary bytes, never real video datasets."""

import importlib.util
import os
from pathlib import Path

import pytest

from hololab.array_positions import decode_position, encode_position, enumerate_array_entries

PACK = Path(__file__).resolve().parents[1] / "hololab/builtin_packs/video-array-source@0.2.0"


def load():
    spec = importlib.util.spec_from_file_location("video_array_positions", PACK / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_byte_order_same_stem_and_inner_extension(tmp_path):
    source = tmp_path / "videos"
    source.mkdir()
    names = [
        "data2.mp4",
        "data10.mp4",
        "data1.mp4",
        "same.mp4",
        "same.mov",
        "Z.MP4",
        os.fsdecode(b"\xff.mp4"),
    ]
    for name in names:
        (source / name).write_bytes(os.fsencode(name))
    output = tmp_path / "out"
    assert load().build_array(source, output) == len(names)
    elements = enumerate_array_entries(output)
    assert [decode_position(p.name) for p in elements] == list(range(len(names)))
    for i, (element, name) in enumerate(zip(elements, sorted(names, key=os.fsencode), strict=True)):
        assert element.name == encode_position(i)
        assert [p.name for p in element.iterdir()] == ["input" + Path(name).suffix]
        assert next(element.iterdir()).read_bytes() == os.fsencode(name)


def test_broken_candidate_fails_before_output(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "x.mp4").symlink_to(source / "missing")
    with pytest.raises(ValueError, match="broken link"):
        load().build_array(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_does_not_rank_stems_or_link_targets(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "zzz"
    target.write_bytes(b"clip")
    (source / "aaa.mp4").symlink_to(target)
    load().build_array(source, tmp_path / "out")
    assert (tmp_path / "out" / encode_position(0) / "input.mp4").read_bytes() == b"clip"
    text = (PACK / "run.py").read_text()
    assert "source.stem" not in text
    assert "fsencode(p.name)" in text
