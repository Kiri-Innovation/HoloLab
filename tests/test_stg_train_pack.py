"""``k4d/stg-train@0.4.7`` consumes the portable state created by ``k4d/stg-init``."""

from __future__ import annotations

from pathlib import Path

import pytest

from hololab.manifest import RenderContext, load_manifest, render_manifest

_MANIFEST = Path(
    "/cloud/cloud-ssd1/Kiri4DGS/SpacetimeGaussians/packs/stg-train@0.4.7/manifest.yaml"
)

if not _MANIFEST.exists():
    pytest.skip("needs SpacetimeGaussians checkout at local path", allow_module_level=True)


def _load() -> object:
    manifest, _sha = load_manifest(_MANIFEST)
    return manifest


def test_manifest_declares_split_state_contract() -> None:
    m = _load()
    assert m.name == "k4d/stg-train"
    assert m.version == "0.4.7"
    assert set(m.inputs) == {"state", "supervision"}
    assert m.inputs["state"].tags == ["stg-training-state"]
    assert m.inputs["supervision"].tags == ["stg-supervision"]
    assert set(m.outputs) == {"state"}
    assert m.outputs["state"].tags == ["stg-training-state", "stg_model"]


def test_train_parameters_are_additional_iterations_and_diagnostics() -> None:
    m = _load()
    assert set(m.params) == {"iterations", "save_cam", "preview_every"}
    assert m.params["iterations"].default == 500
    assert m.params["iterations"].min == 1
    assert m.params["save_cam"].default == 5
    assert m.params["preview_every"].default == 100


def test_exec_shell_uses_only_split_state_inputs() -> None:
    m = _load()
    params = {name: spec.default for name, spec in m.params.items()}
    ctx = RenderContext(
        inputs={"state": "/tmp/input-state", "supervision": "/tmp/supervision"},
        outputs={"state": "/tmp/output-state"},
        params=params,
        scratch_dir="/tmp/scratch/job-1",
        job_id="job-1",
        workflow_id="wf-1",
    )
    shell = render_manifest(m, ctx).shell
    assert "train" in shell
    assert '--state "/tmp/input-state"' in shell
    assert '--supervision "/tmp/supervision"' in shell
    assert '--output "/tmp/output-state"' in shell
    assert "--iterations 500" in shell
    assert "--save-cam 5" in shell
    assert "--preview-every 100" in shell
    assert "colmap_frames" not in shell
