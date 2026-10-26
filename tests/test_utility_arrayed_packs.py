"""The four M5 utility packs load, declare correct arrayed/tags_from wiring.

Locks the pack surface so a future refactor of tag names / arrayable rules
doesn't silently break the arrayed<T> utility layer that M6+M8 depend on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hololab.manifest import load_manifest

PACKS_ROOT = Path(__file__).resolve().parent.parent / "packs"
BUILTINS_ROOT = Path(__file__).resolve().parent.parent / "hololab" / "builtin_packs"


def _load(name: str):
    root = BUILTINS_ROOT if name in {"array-length", "get-index"} else PACKS_ROOT
    manifest, _sha = load_manifest(root / f"{name}@0.1.0" / "manifest.yaml")
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
    # get-index collapses the OUTER dim of the wired arrayed input. The
    # declarative derivation lets gateway + frontend both resolve the
    # displayed dim_labels without the pack author re-declaring them.
    assert m.outputs["item"].dim_labels_from_input == "arr"
    assert m.outputs["item"].dim_labels_drop_outer == 1


@pytest.mark.parametrize("pack_name", ["array-length", "arrayfy", "get-index"])
def test_pack_category_marks_basic_array_operation(pack_name: str) -> None:
    m = _load(pack_name)
    assert m.category == ["basic", "array", pack_name], m.category
