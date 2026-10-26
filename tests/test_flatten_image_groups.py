from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from hololab.manifest import load_manifest

REPO_ROOT = Path(__file__).parents[2]
PACKS = REPO_ROOT / "hololab" / "packs"
sys.path.insert(0, str(REPO_ROOT / "packages" / "colmap4d" / "src"))


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FLATTEN = _module(
    "flatten_image_groups_010",
    PACKS / "flatten-image-groups@0.1.0" / "flatten_image_groups.py",
)
MERGE = _module("merge_colmap4d_012", PACKS / "merge-colmap4d@0.1.2" / "merge_colmap4d.py")


def test_manifest_contract_is_two_dimensions_to_one(tmp_path: Path) -> None:
    flatten, _ = load_manifest(PACKS / "flatten-image-groups@0.1.0" / "manifest.yaml")
    merge, _ = load_manifest(PACKS / "merge-colmap4d@0.1.2" / "manifest.yaml")
    assert flatten.inputs["images"].dim_labels == ["group", "cam"]
    assert flatten.outputs["images"].arrayed is True
    assert flatten.outputs["images"].dim_labels == []
    assert merge.inputs["images"].arrayed is True
    assert merge.inputs["images"].dim_labels == []


def test_flatten_is_physical_one_dimensional_and_reversible(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for group, cameras in {"g000000": ["cam0", "cam1"], "g000001": ["cam0"]}.items():
        for camera in cameras:
            path = source / group / f"{camera}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"{group}/{camera}".encode())
    output = tmp_path / "flat"
    result = FLATTEN.flatten(source, output)

    visible = sorted(path.name for path in output.iterdir() if not path.name.startswith("."))
    assert visible == ["g000000__cam0.jpg", "g000000__cam1.jpg", "g000001__cam0.jpg"]
    assert all((output / name).is_file() for name in visible)
    assert MERGE._flat_group_images(output).keys() == {"g000000", "g000001"}
    assert result["groups"] == 2
    assert result["images"] == 3
    audit = json.loads((output / ".flatten-image-groups" / "manifest.json").read_text())
    assert [row["source"] for row in audit["members"]] == [
        "g000000/cam0.jpg",
        "g000000/cam1.jpg",
        "g000001/cam0.jpg",
    ]


def test_merge_rejects_unencoded_or_nested_images(tmp_path: Path) -> None:
    (tmp_path / "cam0.jpg").write_bytes(b"jpeg")
    with pytest.raises(ValueError, match="expected flattened name"):
        MERGE._flat_group_images(tmp_path)
    (tmp_path / "cam0.jpg").unlink()
    (tmp_path / "g000000").mkdir()
    with pytest.raises(ValueError, match="root-level files"):
        MERGE._flat_group_images(tmp_path)
