"""Contract tests for the split STG init/train packs."""

import re
from pathlib import Path

import pytest

from hololab.manifest import RenderContext, load_manifest, render_manifest

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
    assert manifest.inputs["flow_dir"].tags == ["t4w_flow"]
    assert manifest.inputs["flow_dir"].required is False
    assert manifest.outputs["initial_state"].tags == ["stg-training-state", "stg_model"]
    assert manifest.outputs["supervision"].tags == ["stg-supervision"]


def test_init_renders_when_optional_flow_is_unwired() -> None:
    manifest, _ = load_manifest(ROOT / "stg-init@0.1.0/manifest.yaml")
    rendered = render_manifest(
        manifest,
        RenderContext(
            inputs={"colmap_frames": "/tmp/frames"},
            outputs={"initial_state": "/tmp/state", "supervision": "/tmp/supervision"},
            params={"configpath": "configs/n3d_full/stg_state_default.json"},
            scratch_dir="/tmp/scratch",
            job_id="test-job",
            workflow_id="test-workflow",
        ),
    )
    assert 'if [ -n "" ]' in rendered.shell


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


@pytest.mark.parametrize(
    ("relative", "allowed"),
    [
        (
            "stg-init@0.1.0/manifest.yaml",
            {
                "inputs.colmap_frames",
                "inputs.get('flow_dir', '')",
                "outputs.supervision",
                "outputs.initial_state",
                "params.configpath",
                "scratch_dir",
            },
        ),
        (
            "stg-train@0.4.0/manifest.yaml",
            {
                "inputs.state",
                "inputs.supervision",
                "outputs.state",
                "params.iterations",
                "params.save_cam",
                "scratch_dir",
            },
        ),
    ],
)
def test_every_template_reference_is_declared_or_runtime_builtin(
    relative: str, allowed: set[str]
) -> None:
    """Prevent optional/unwired ports from becoming StrictUndefined regressions."""
    raw = (ROOT / relative).read_text()
    refs = {match.strip() for match in re.findall(r"\{\{\s*(.*?)\s*\}\}", raw)}
    assert refs <= allowed
