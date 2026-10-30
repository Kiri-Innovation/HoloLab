import importlib.util
import json
from pathlib import Path

import pytest

from hololab.array_positions import decode_position, encode_position

PACK = Path(__file__).resolve().parents[1] / "hololab/builtin_packs/regroup@0.3.0"


def load():
    spec = importlib.util.spec_from_file_location("regroup_positions", PACK / "regroup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tree(root, second=("x.png", "y.png")):
    for parent, names in [("source_a", ("a.png", "b.png")), ("source_z", second)]:
        for i, name in enumerate(names):
            path = root / parent / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(parent + str(i))


def test_transpose_locally_enumerates_unrelated_names(tmp_path, monkeypatch):
    tree(tmp_path / "in")
    monkeypatch.setattr(
        "sys.argv",
        [
            "regroup",
            "--input",
            str(tmp_path / "in"),
            "--output",
            str(tmp_path / "out"),
            "--input-dims",
            json.dumps(["cam", "frame"]),
            "--output-dims",
            json.dumps(["frame", "cam"]),
            "--content-dims",
            "1",
        ],
    )
    assert load().main() == 0
    for j in range(2):
        for i, parent in enumerate(["source_a", "source_z"]):
            path = tmp_path / "out" / encode_position(j) / (encode_position(i) + ".png")
            assert path.read_text() == parent + str(j)
            assert decode_position(path.stem) == i


def test_ragged_array_rejected_before_output(tmp_path, monkeypatch):
    tree(tmp_path / "in", ("x.png",))
    monkeypatch.setattr(
        "sys.argv",
        [
            "regroup",
            "--input",
            str(tmp_path / "in"),
            "--output",
            str(tmp_path / "out"),
            "--input-dims",
            '["cam","frame"]',
            "--output-dims",
            '["frame","cam"]',
        ],
    )
    assert load().main() == 2
    assert not (tmp_path / "out").exists()


def test_axis_labels_only_define_permutation():
    mod = load()
    assert mod._permutation_of(["frame", "camera"], ["camera", "frame"]) == [1, 0]
    with pytest.raises(ValueError):
        mod._permutation_of(["cam"], ["frame"])
    text = (PACK / "regroup.py").read_text()
    assert "_collect_axis_values" not in text
    assert "positions[index][name]" not in text
