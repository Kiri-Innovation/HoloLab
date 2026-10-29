import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from hololab.array_positions import decode_position, encode_position

PACK = Path(__file__).resolve().parents[1] / "community_packs/colmap-sfm@0.5.0"


def load(monkeypatch):
    monkeypatch.syspath_prepend(str(PACK))
    spec = importlib.util.spec_from_file_location("sfm_positions", PACK / "position_model.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def model(root, rows, points=""):
    root.mkdir(parents=True)
    (root / "cameras.txt").write_text("7 PINHOLE 20 10 10 10 10 5\n")
    (root / "images.txt").write_text(
        "".join(f"{iid} 1 0 0 0 0 0 0 7 {name}\n\n" for iid, name in rows)
    )
    (root / "points3D.txt").write_text(points)


def test_staging_export_reorders_and_remaps_tracks_frames(tmp_path, monkeypatch):
    mod = load(monkeypatch)
    source = tmp_path / "input"
    source.mkdir()
    for name in ["z.jpg", "a.png"]:
        (source / name).write_text(name)
    staged = mod.stage_images(source, tmp_path / "stage")
    assert [decode_position(p.stem) for p in staged] == [0, 1]
    model(
        tmp_path / "model", [(9, staged[0].name), (2, staged[1].name)], "3 0 0 0 1 1 1 0 9 0 2 0\n"
    )
    image_text = (tmp_path / "model/images.txt").read_text().replace("\n\n", "\n0 0 3\n")
    (tmp_path / "model/images.txt").write_text(image_text)
    (tmp_path / "model/frames.txt").write_text("4 1 1 0 0 0 0 0 0 2 CAMERA 7 9 CAMERA 7 2\n")
    mod.export_sfm(tmp_path / "model", tmp_path / "stage", tmp_path / "cams", tmp_path / "points")
    rows = mod.read_images(tmp_path / "cams/images.txt")
    assert [row[0] for row, _ in rows] == ["1", "2"]
    assert [row[9] for row, _ in rows] == [p.name for p in staged]
    assert (tmp_path / "points/points3D.txt").read_text().split()[-4:] == ["1", "0", "2", "0"]
    assert (tmp_path / "cams/frames.txt").read_text().split()[-6:] == [
        "CAMERA",
        "7",
        "1",
        "CAMERA",
        "7",
        "2",
    ]


def test_length_mismatch_and_external_names(tmp_path, monkeypatch):
    mod = load(monkeypatch)
    model(tmp_path / "model", [(90, "totally arbitrary.jpg")])
    assert (
        mod.paired_poses(tmp_path / "model", [Path("other.png")])[0][0][9]
        == "totally arbitrary.jpg"
    )
    with pytest.raises(ValueError, match="length mismatch"):
        mod.paired_poses(tmp_path / "model", [Path("one"), Path("two")])


def test_all_parameter_modes_unchanged_and_codec_vendored():
    old = yaml.safe_load((PACK.with_name("colmap-sfm@0.4.3") / "manifest.yaml").read_text())
    new = yaml.safe_load((PACK / "manifest.yaml").read_text())
    assert old["params"] == new["params"]
    assert new["params"]["camera_assignment"]["values"] == ["single", "per_folder", "per_image"]
    assert (PACK / "array_positions.py").read_bytes() == (
        PACK.parents[1] / "hololab/array_positions.py"
    ).read_bytes()
    assert "sfm_key" not in (PACK / "position_model.py").read_text()
    assert "position_model.py" in new["exec"]["shell"]
    assert sys.executable
    assert encode_position(0) == "p_0000000000000000"
