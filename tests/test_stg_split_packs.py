"""Contract tests for the split STG init/train packs."""

from pathlib import Path

import pytest

from hololab.manifest import load_manifest

ROOT = Path("/cloud/cloud-ssd1/Kiri4DGS/SpacetimeGaussians")


@pytest.mark.parametrize(
    ("relative", "name", "version"),
    [
        ("stg-init@0.1.0/manifest.yaml", "stg-init", "0.1.0"),
        ("stg-train@0.4.0/manifest.yaml", "stg-train", "0.4.0"),
    ],
)
def test_split_stg_manifests_load(relative: str, name: str, version: str) -> None:
    manifest, _ = load_manifest(ROOT / relative)
    assert manifest.name == name
    assert manifest.version == version


def test_init_emits_semantic_state_and_supervision() -> None:
    manifest, _ = load_manifest(ROOT / "stg-init@0.1.0/manifest.yaml")
    assert manifest.inputs["colmap_frames"].arrayed is True
    assert manifest.inputs["colmap_frames"].tags == ["colmap-folder"]
    assert manifest.outputs["initial_state"].tags == ["stg-training-state", "stg_model"]
    assert manifest.outputs["supervision"].tags == ["stg-supervision"]


def test_train_has_only_progress_and_diagnostic_knobs() -> None:
    manifest, _ = load_manifest(ROOT / "stg-train@0.4.0/manifest.yaml")
    assert set(manifest.params) == {"iterations", "save_cam"}
    assert manifest.params["iterations"].min == 1
    assert manifest.inputs["state"].tags == ["stg-training-state"]
    assert manifest.outputs["state"].tags == ["stg-training-state", "stg_model"]
    shell = manifest.exec.shell
    assert "--temporal-bridge-at-iter" not in shell  # encapsulated in state runner
    assert "--start_checkpoint" not in shell


def test_state_runner_hard_disables_difix_schedules() -> None:
    runner = (ROOT / "stg_state.py").read_text()
    assert '"--temporal-bridge-at-iter", "", "--difix-replace-at-iter", ""' in runner
    assert '"--start_checkpoint", str(checkpoint)' in runner
