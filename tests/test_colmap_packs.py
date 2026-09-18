"""M6 packs: colmap-sfm-cams-only + colmap-triangulate.

Locks the shape (tags, arrayable, params) so a future refactor of the tag
system or fan-out semantics doesn't silently break the STG production
pipeline. Runtime correctness (COLMAP invocation, output layout) is
exercised by the M8 cook_spinach E2E — not here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hololab.manifest import load_manifest

PACKS_ROOT = Path(__file__).resolve().parent.parent / "packs"


def _load(name: str):
    m, _sha = load_manifest(PACKS_ROOT / f"{name}@0.1.0" / "manifest.yaml")
    return m


def test_colmap_sfm_cams_only_shape() -> None:
    m = _load("colmap-sfm-cams-only")
    # Not arrayable — SfM is a whole-sequence solve, not per-shard.
    assert m.arrayable is False
    assert m.inputs["frames"].tags == ["frame_sequence"]
    assert m.inputs["frames"].arrayed is False
    assert m.outputs["cams"].tags == ["colmap-cams"]
    assert m.outputs["cams"].arrayed is False
    # SIFT knobs match the flag set the rig-* packs settled on.
    for p in ("use_gpu", "max_image_size", "max_num_features"):
        assert p in m.params, p


def test_colmap_triangulate_is_arrayable() -> None:
    m = _load("colmap-triangulate")
    # arrayable=true so the operator's per-node checkbox fans out shards.
    assert m.arrayable is True
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["cams"].arrayed is False
    assert m.inputs["frames"].tags == ["frame_sequence"]
    assert m.inputs["frames"].arrayed is False
    assert m.outputs["points"].tags == ["colmap-points"]
    assert m.outputs["points"].arrayed is False
    # Same frozen-intrinsic reproj filter default as rig-group-triangulation.
    assert m.params["filter_max_reproj_error"].default == 4.0


@pytest.mark.parametrize("pack_name", ["colmap-sfm-cams-only", "colmap-triangulate"])
def test_colmap_pack_docs_mention_rig_alternative(pack_name: str) -> None:
    """The `docs:` field distinguishes this pack from rig-group-triangulation
    so a future contributor doesn't ask "why are there two triangulators?"."""
    m = _load(pack_name)
    assert m.docs and "rig-group-triangulation" in m.docs, (
        f"{pack_name} docs must explain the rig-* alternative"
    )


@pytest.mark.parametrize(
    "pack_name", ["colmap-sfm-cams-only", "colmap-triangulate", "colmap-assemble"]
)
def test_colmap_pack_category(pack_name: str) -> None:
    m = _load(pack_name)
    assert m.category == ["reconstruction", "colmap"], m.category


def test_colmap_assemble_shape() -> None:
    """colmap-assemble fans in three arrayed<T> inputs → one non-arrayed
    ``colmap`` container that stg-train can consume verbatim."""
    m = _load("colmap-assemble")
    # Not arrayable — whole-array reshape, one process.
    assert m.arrayable is False
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["cams"].arrayed is True
    assert m.inputs["points"].tags == ["colmap-points"]
    assert m.inputs["points"].arrayed is True
    assert m.inputs["frames"].tags == ["frame_sequence"]
    assert m.inputs["frames"].arrayed is True
    assert m.outputs["colmap"].tags == ["colmap"]
    assert m.outputs["colmap"].arrayed is False
    assert m.source_entry == "assemble.py"
    script = PACKS_ROOT / "colmap-assemble@0.1.0" / m.source_entry
    assert script.is_file()
