"""The four M5 utility packs load, declare correct arrayed/tags_from wiring.

Locks the pack surface so a future refactor of tag names / arrayable rules
doesn't silently break the arrayed<T> utility layer that M6+M8 depend on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hololab.manifest import load_manifest

PACKS_ROOT = Path(__file__).resolve().parent.parent / "packs"


def _load(name: str):
    manifest, _sha = load_manifest(PACKS_ROOT / f"{name}@0.1.0" / "manifest.yaml")
    return manifest


def test_array_length_shape() -> None:
    m = _load("array-length")
    assert m.arrayable is False
    assert m.inputs["arr"].tags == ["any"]
    assert m.inputs["arr"].arrayed is True
    assert m.outputs["n"].tags == ["int"]
    assert m.outputs["n"].storage.value == "file"
    assert m.outputs["n"].arrayed is False


def test_arrayfy_shape_with_tags_from() -> None:
    m = _load("arrayfy")
    assert m.arrayable is False
    assert m.inputs["data"].tags == ["any"]
    assert m.inputs["data"].arrayed is False
    assert m.inputs["count"].tags == ["int"]
    assert m.outputs["out"].tags == ["any"]
    assert m.outputs["out"].arrayed is True
    # Propagation: output element type follows the connected ``data`` input.
    assert m.outputs["out"].tags_from == "data"


def test_get_index_shape_with_idx_param() -> None:
    m = _load("get-index")
    assert m.arrayable is False
    assert m.inputs["arr"].tags == ["any"]
    assert m.inputs["arr"].arrayed is True
    # idx is a PARAM (static "pick this one"), not an input port —
    # documented rationale in the design report.
    assert "idx" in m.params
    assert m.params["idx"].type.value == "int"
    assert m.params["idx"].default == 0
    assert m.outputs["item"].tags == ["any"]
    assert m.outputs["item"].arrayed is False
    assert m.outputs["item"].tags_from == "arr"


def test_regroup_by_frame_shape() -> None:
    m = _load("regroup-by-frame")
    # Whole-array operation — NOT arrayable (framework passes the arrayed
    # dir path as-is; the pack does the transpose in one process).
    assert m.arrayable is False
    assert m.inputs["by_cam"].tags == ["image_sequence"]
    assert m.inputs["by_cam"].arrayed is True
    assert m.outputs["by_frame"].tags == ["image_sequence"]
    assert m.outputs["by_frame"].arrayed is True


def test_regroup_by_frame_has_source_entry() -> None:
    m = _load("regroup-by-frame")
    assert m.source_entry == "regroup.py"
    # The referenced script actually exists.
    script = PACKS_ROOT / "regroup-by-frame@0.1.0" / m.source_entry
    assert script.is_file()


@pytest.mark.parametrize("pack_name", ["array-length", "arrayfy", "get-index", "regroup-by-frame"])
def test_pack_category_marks_utility(pack_name: str) -> None:
    m = _load(pack_name)
    assert m.category == ["utility", "arrayed"], m.category
