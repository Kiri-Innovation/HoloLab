"""Contract tests for the split STG init/train packs."""

import re
from pathlib import Path

import pytest

from hololab.manifest import RenderContext, load_manifest, render_manifest

ROOT = Path("/cloud/cloud-ssd1/Kiri4DGS/SpacetimeGaussians")
SPLIT_MANIFESTS = tuple(sorted((ROOT / "packs").glob("stg-*/manifest.yaml")))


@pytest.mark.parametrize("path", SPLIT_MANIFESTS, ids=lambda path: path.parent.name)
def test_split_stg_manifests_load(path: Path) -> None:
    """Every currently shipped split STG manifest remains loadable."""
    manifest, _ = load_manifest(path)
    name, version = path.parent.name.rsplit("@", 1)
    assert (manifest.name, manifest.version) == (f"community/{name}", version)


def test_init_emits_semantic_state_and_supervision() -> None:
    manifest, _ = load_manifest(ROOT / "packs" / "stg-init@0.3.1/manifest.yaml")
    assert [variant.tags for variant in manifest.inputs["colmap_4d"].any_of] == [
        ["colmap4d"],
        ["colmap-folder"],
    ]
    assert manifest.inputs["flow_dir"].tags == ["t4w_flow"]
    assert manifest.inputs["flow_dir"].required is False
    assert manifest.outputs["initial_state"].tags == ["stg-training-state", "stg_model"]
    assert manifest.outputs["supervision"].tags == ["stg-supervision"]


def test_init_renders_when_optional_flow_is_unwired() -> None:
    manifest, _ = load_manifest(ROOT / "packs" / "stg-init@0.3.1/manifest.yaml")
    rendered = render_manifest(
        manifest,
        RenderContext(
            inputs={"colmap_4d": "/tmp/frames"},
            outputs={
                "initial_state": "/tmp/state",
                "supervision": "/tmp/supervision",
                "preview": "/tmp/preview.png",
            },
            params={"configpath": "configs/n3d_full/stg_state_default.json"},
            scratch_dir="/tmp/scratch",
            job_id="test-job",
            workflow_id="test-workflow",
        ),
    )
    assert 'if [ -n "" ]' in rendered.shell
    assert "init-union" in rendered.shell


def test_train_has_only_progress_and_diagnostic_knobs() -> None:
    manifest, _ = load_manifest(ROOT / "packs" / "stg-train@0.4.6/manifest.yaml")
    assert set(manifest.params) == {"iterations", "save_cam", "preview_every"}
    assert manifest.params["iterations"].min == 1
    assert manifest.params["save_cam"].min == 0
    assert manifest.params["preview_every"].min == 0
    assert "comparison" in manifest.params["preview_every"].description
    assert manifest.inputs["state"].tags == ["stg-training-state"]
    assert manifest.outputs["state"].tags == ["stg-training-state", "stg_model"]
    shell = manifest.exec.shell
    assert "--temporal-bridge-at-iter" not in shell  # encapsulated in state runner
    assert "--start_checkpoint" not in shell


def test_state_runner_hard_disables_difix_schedules() -> None:
    runner = (ROOT / "stg_state.py").read_text()
    assert '"--temporal-bridge-at-iter", "", "--difix-replace-at-iter", ""' in runner
    assert '"--start_checkpoint", str(checkpoint)' in runner


@pytest.mark.parametrize("path", SPLIT_MANIFESTS, ids=lambda path: path.parent.name)
def test_every_template_reference_is_declared_or_runtime_builtin(path: Path) -> None:
    """Prevent optional/unwired ports from becoming StrictUndefined regressions."""
    manifest, _ = load_manifest(path)
    allowed = {
        *(f"inputs.{name}" for name in manifest.inputs),
        *(f"inputs.get('{name}', '')" for name in manifest.inputs),
        *(f"outputs.{name}" for name in manifest.outputs),
        *(f"params.{name}" for name in manifest.params),
        "scratch_dir",
    }
    raw = path.read_text()
    refs = {match.strip() for match in re.findall(r"\{\{\s*(.*?)\s*\}\}", raw)}
    assert refs <= allowed | {"package_root", "manifest_dir"}
