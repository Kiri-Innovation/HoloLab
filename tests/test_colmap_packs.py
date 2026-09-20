"""M6 packs: colmap-sfm-cams-only + colmap-triangulate + colmap-assemble.

Locks the shape (tags, arrayable, params) so a future refactor of the tag
system or fan-out semantics doesn't silently break the STG production
pipeline. Runtime correctness (COLMAP invocation, output layout) is
exercised by end-to-end runs — not here.

Both @0.1.0 and @0.2.0 pack versions are validated. @0.2.0 is the
canonical STG no_prior mirror; @0.1.0 is kept for any historic workflow
that still references it (jobs record ``(name, version)`` at assignment,
so bumping the pack doesn't disturb them).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hololab.manifest import load_manifest

PACKS_ROOT = Path(__file__).resolve().parent.parent / "packs"


def _load(name: str, version: str = "0.2.0"):
    m, _sha = load_manifest(PACKS_ROOT / f"{name}@{version}" / "manifest.yaml")
    return m


# ---------------------------------------------------------------------------
# @0.2.0 — canonical STG no_prior mirror (single_camera+OPENCV, undistort,
# self-contained colmap-frame output).
# ---------------------------------------------------------------------------


def test_colmap_sfm_cams_only_shape_v020() -> None:
    m = _load("colmap-sfm-cams-only", "0.2.0")
    assert m.version == "0.2.0"
    assert m.arrayable is False  # SfM is a whole-sequence solve, not per-shard.
    assert m.inputs["frames"].tags == ["image_sequence"]
    assert m.inputs["frames"].arrayed is False
    assert m.outputs["cams"].tags == ["colmap-cams"]
    assert m.outputs["cams"].arrayed is False
    for p in ("use_gpu", "max_image_size", "max_num_features"):
        assert p in m.params, p
    # Defaults raised to COLMAP defaults — v0.1.0's 2048/2400 was too weak.
    assert m.params["max_num_features"].default == 8192
    assert m.params["max_image_size"].default == 3200
    # single_camera + OPENCV are baked into the shell (mirroring pre_no_prior.py).
    assert "--ImageReader.single_camera 1" in m.exec.shell
    assert "--ImageReader.camera_model OPENCV" in m.exec.shell
    # BA tolerance override (mirrors SpacetimeGaussians/thirdparty/gaussian_splatting/convert.py:83)
    assert "ba_global_function_tolerance=0.000001" in m.exec.shell


def test_colmap_triangulate_shape_v020() -> None:
    m = _load("colmap-triangulate", "0.2.0")
    assert m.version == "0.2.0"
    assert m.arrayable is True  # framework fans out one shard per element.
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["cams"].arrayed is False
    assert m.inputs["frames"].tags == ["image_sequence"]
    assert m.inputs["frames"].arrayed is False
    # v0.2.0 output is a self-contained per-frame COLMAP dir (sparse/0 + images/).
    assert m.outputs["frame"].tags == ["colmap-frame"]
    assert m.outputs["frame"].arrayed is False
    assert m.params["filter_max_reproj_error"].default == 4.0
    assert m.params["max_num_features"].default == 8192
    assert m.params["max_image_size"].default == 3200
    assert m.source_entry == "triangulate.py"
    assert (PACKS_ROOT / "colmap-triangulate@0.2.0" / m.source_entry).is_file()


def test_colmap_assemble_shape_v020() -> None:
    """v0.2.0 collapses the three-input fan-in (cams + points + frames) into
    one ``arrayed<colmap-frame>`` input — the per-frame dirs are already
    self-contained (PINHOLE cams + points + undistorted images)."""
    m = _load("colmap-assemble", "0.2.0")
    assert m.version == "0.2.0"
    assert m.arrayable is False  # whole-array reshape, one process.
    assert list(m.inputs.keys()) == ["frames"], (
        "v0.2.0 assemble has exactly one input (arrayed<colmap-frame>)"
    )
    assert m.inputs["frames"].tags == ["colmap-frame"]
    assert m.inputs["frames"].arrayed is True
    assert m.outputs["colmap"].tags == ["colmap"]
    assert m.outputs["colmap"].arrayed is False
    assert m.source_entry == "assemble.py"
    assert (PACKS_ROOT / "colmap-assemble@0.2.0" / m.source_entry).is_file()


@pytest.mark.parametrize("pack_name", ["colmap-sfm-cams-only", "colmap-triangulate"])
def test_colmap_pack_docs_mention_rig_alternative_v020(pack_name: str) -> None:
    """The ``docs:`` field distinguishes this pack from rig-group-triangulation
    so a future contributor doesn't ask "why are there two triangulators?"."""
    m = _load(pack_name, "0.2.0")
    assert m.docs and "rig-group-triangulation" in m.docs, (
        f"{pack_name}@0.2.0 docs must explain the rig-* alternative"
    )


@pytest.mark.parametrize(
    "pack_name", ["colmap-sfm-cams-only", "colmap-triangulate", "colmap-assemble"]
)
def test_colmap_pack_category_v020(pack_name: str) -> None:
    m = _load(pack_name, "0.2.0")
    assert m.category == ["reconstruction", "colmap"], m.category


# ---------------------------------------------------------------------------
# @0.1.0 — kept alongside for historic workflows. Only shape locks; the
# canonical STG behavior lives in @0.2.0 and is checked above.
# ---------------------------------------------------------------------------


def test_colmap_sfm_cams_only_shape_v010() -> None:
    m = _load("colmap-sfm-cams-only", "0.1.0")
    assert m.arrayable is False
    assert m.inputs["frames"].tags == ["image_sequence"]
    assert m.outputs["cams"].tags == ["colmap-cams"]


def test_colmap_triangulate_shape_v010() -> None:
    m = _load("colmap-triangulate", "0.1.0")
    assert m.arrayable is True
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["frames"].tags == ["image_sequence"]
    # v0.1.0 emitted only points3D.
    assert m.outputs["points"].tags == ["colmap-points"]


def test_colmap_assemble_shape_v010() -> None:
    m = _load("colmap-assemble", "0.1.0")
    assert m.arrayable is False
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["cams"].arrayed is True
    assert m.inputs["points"].tags == ["colmap-points"]
    assert m.inputs["frames"].tags == ["image_sequence"]
    assert m.outputs["colmap"].tags == ["colmap"]
