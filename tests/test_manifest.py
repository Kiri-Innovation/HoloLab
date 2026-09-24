"""Manifest schema validation and template rendering."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hololab.manifest import (
    RenderContext,
    load_manifest,
    render_manifest,
)
from hololab.manifest.schema import API_VERSION, StorageForm

VALID_MANIFEST = {
    "apiVersion": API_VERSION,
    "kind": "Algorithm",
    "name": "demo",
    "version": "0.1.0",
    "description": "A tiny test pack.",
    "inputs": {"colmap": {"tags": ["colmap"]}},
    "outputs": {"model": {"tags": ["stg_model"]}},
    "params": {"iters": {"type": "int", "default": 100, "min": 1}},
    "runtime": {"env": "kiri", "gpu": {"required": True, "vram_gb_min": 8}},
    "exec": {
        "shell": "python train.py --iters {{ params.iters }} --out {{ outputs.model }}",
    },
    "idempotency": {"marker": "{{ outputs.model }}/.done"},
    "progress": {"stdout_regex": r"\[ITER (\d+)\]"},
    "previews": [{"id": "log", "type": "log", "path": "{{ outputs.model }}/log.txt"}],
}


def _write(tmp: Path, data: dict) -> Path:
    p = tmp / "manifest.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_valid_manifest_parses(tmp_path: Path) -> None:
    manifest, sha = load_manifest(_write(tmp_path, VALID_MANIFEST))
    assert manifest.name == "demo"
    assert manifest.outputs["model"].tags == ["stg_model"]
    # ``dir`` is the default storage form when the manifest omits it.
    assert manifest.outputs["model"].storage is StorageForm.DIR
    assert manifest.inputs["colmap"].required is True
    assert len(sha) == 64  # sha256 hex


def test_input_required_defaults_true_can_be_overridden(tmp_path: Path) -> None:
    """Ports are required by default; ``required: false`` makes them optional."""

    data = dict(VALID_MANIFEST)
    data["inputs"] = {
        "colmap": {"tags": ["colmap"], "required": False, "description": "opt"},
    }
    manifest, _ = load_manifest(_write(tmp_path, data))
    assert manifest.inputs["colmap"].required is False


def test_input_rejects_empty_tags(tmp_path: Path) -> None:
    """A port without tags has no object type — reject it early."""

    data = dict(VALID_MANIFEST)
    data["inputs"] = {"colmap": {"tags": []}}
    with pytest.raises(ValueError):
        load_manifest(_write(tmp_path, data))


def test_output_storage_hint_can_be_file(tmp_path: Path) -> None:
    """Storage is an internal transport hint, not part of the type."""

    data = dict(VALID_MANIFEST)
    data["outputs"] = {"model": {"tags": ["stg_model"], "storage": "file"}}
    manifest, _ = load_manifest(_write(tmp_path, data))
    assert manifest.outputs["model"].storage is StorageForm.FILE


def test_legacy_type_field_is_accepted_and_ignored(tmp_path: Path) -> None:
    """Older manifests carry ``type: PathDir`` on ports. Accept + ignore."""

    data = dict(VALID_MANIFEST)
    data["inputs"] = {"colmap": {"type": "PathDir", "tags": ["colmap"]}}
    data["outputs"] = {"model": {"type": "PathDir", "tags": ["stg_model"]}}
    manifest, _ = load_manifest(_write(tmp_path, data))
    assert manifest.inputs["colmap"].tags == ["colmap"]


def test_rejects_wrong_api_version(tmp_path: Path) -> None:
    bad = {**VALID_MANIFEST, "apiVersion": "hololab.dev/v0"}
    with pytest.raises(ValueError):
        load_manifest(_write(tmp_path, bad))


def test_rejects_no_outputs(tmp_path: Path) -> None:
    bad = {**VALID_MANIFEST, "outputs": {}}
    with pytest.raises(ValueError):
        load_manifest(_write(tmp_path, bad))


def test_rejects_bad_semver(tmp_path: Path) -> None:
    bad = {**VALID_MANIFEST, "version": "v1"}
    with pytest.raises(ValueError):
        load_manifest(_write(tmp_path, bad))


def test_rejects_uppercase_name(tmp_path: Path) -> None:
    bad = {**VALID_MANIFEST, "name": "MyPack"}
    with pytest.raises(ValueError):
        load_manifest(_write(tmp_path, bad))


def test_preview_requires_path_xor_glob(tmp_path: Path) -> None:
    bad = {
        **VALID_MANIFEST,
        "previews": [{"id": "pv", "type": "image"}],  # neither path nor glob
    }
    with pytest.raises(ValueError):
        load_manifest(_write(tmp_path, bad))


def test_enum_param_requires_values(tmp_path: Path) -> None:
    bad = dict(VALID_MANIFEST)
    bad["params"] = {"mode": {"type": "enum"}}
    with pytest.raises(ValueError):
        load_manifest(_write(tmp_path, bad))


def test_output_preview_declaration_parses(tmp_path: Path) -> None:
    """``preview`` on an output declares which viewer the frontend should mount."""

    data = dict(VALID_MANIFEST)
    data["outputs"] = {
        "model": {
            "tags": ["splatv_file"],
            "storage": "file",
            "preview": {"viewer": "splatv"},
        },
    }
    manifest, _ = load_manifest(_write(tmp_path, data))
    preview = manifest.outputs["model"].preview
    assert preview is not None
    assert preview.viewer == "splatv"
    assert preview.member is None


def test_output_preview_member_is_optional_for_dir(tmp_path: Path) -> None:
    """``member`` names a file inside a ``dir`` output for the viewer to open."""

    data = dict(VALID_MANIFEST)
    data["outputs"] = {
        "out_dir": {
            "tags": ["log"],
            "storage": "dir",
            "preview": {"viewer": "text", "member": "log.txt"},
        },
    }
    manifest, _ = load_manifest(_write(tmp_path, data))
    preview = manifest.outputs["out_dir"].preview
    assert preview is not None
    assert preview.viewer == "text"
    assert preview.member == "log.txt"


def test_output_preview_rejects_unknown_viewer(tmp_path: Path) -> None:
    """Viewer is a closed vocabulary — a typo should hard-fail at load."""

    data = dict(VALID_MANIFEST)
    data["outputs"] = {
        "model": {"tags": ["stg_model"], "preview": {"viewer": "hologram"}},
    }
    with pytest.raises(ValueError):
        load_manifest(_write(tmp_path, data))


def test_output_preview_ignores_extra_keys(tmp_path: Path) -> None:
    """Schema policy is ``extra=ignore`` since a long-running node used
    to zero out its pack list when a schema bump added new fields to
    manifests that were previously fine. A typo in a nested key (like
    ``membre`` for ``member``) is silently dropped rather than raising.
    """

    data = dict(VALID_MANIFEST)
    data["outputs"] = {
        "model": {
            "tags": ["stg_model"],
            "preview": {"viewer": "splatv", "membre": "typo.txt"},
        },
    }
    manifest, _ = load_manifest(_write(tmp_path, data))
    # ``member`` remained unset — the typo did not create it, and the
    # unknown ``membre`` key was silently ignored (no exception).
    assert manifest.outputs["model"].preview is not None
    assert manifest.outputs["model"].preview.member is None


def test_manifest_top_level_extra_field_warns(tmp_path: Path, caplog) -> None:
    """A schema bump that adds ``future_field`` at the manifest root
    must not crash an older node — it should log a single warning so
    authors know why the field is being ignored, then continue parsing.
    """

    import logging

    data = dict(VALID_MANIFEST)
    data["future_field"] = {"anything": True}

    with caplog.at_level(logging.WARNING, logger="hololab.manifest"):
        manifest, _ = load_manifest(_write(tmp_path, data))

    assert manifest.name == "demo"  # parse succeeded
    warned = [r for r in caplog.records if r.name == "hololab.manifest"]
    assert warned, "expected a warning about the unknown top-level field"
    assert "future_field" in warned[-1].getMessage()


def test_output_preview_absent_by_default(tmp_path: Path) -> None:
    """The default (no preview) means the node's canvas card has no expand caret."""

    manifest, _ = load_manifest(_write(tmp_path, VALID_MANIFEST))
    assert manifest.outputs["model"].preview is None


def test_render_manifest_substitutes(tmp_path: Path) -> None:
    manifest, _ = load_manifest(_write(tmp_path, VALID_MANIFEST))
    ctx = RenderContext(
        inputs={"colmap": "/data/c0"},
        outputs={"model": "/out/m"},
        params={"iters": 200},
        pack_dir="/packs/demo",
        workspace_root="/ws",
        job_id="j",
        workflow_id="w",
    )
    rendered = render_manifest(manifest, ctx)
    assert "python train.py --iters 200 --out /out/m" in rendered.shell
    assert rendered.idempotency_marker == "/out/m/.done"
    assert rendered.preview_paths["log"] == "/out/m/log.txt"


def test_render_rejects_undefined_variable(tmp_path: Path) -> None:
    manifest_data = dict(VALID_MANIFEST)
    manifest_data["exec"] = {"shell": "python {{ params.does_not_exist }}"}
    manifest, _ = load_manifest(_write(tmp_path, manifest_data))
    ctx = RenderContext(params={})
    with pytest.raises(ValueError):
        render_manifest(manifest, ctx)


def test_render_exposes_scratch_dir_binding(tmp_path: Path) -> None:
    """``{{ scratch_dir }}`` renders to the path the runtime passed in.

    Packs that used to shell out to ``mktemp -d`` on the system disk
    can now write into this per-job dir under ``workspace_root``.
    """

    manifest_data = dict(VALID_MANIFEST)
    manifest_data["exec"] = {"shell": "cd {{ scratch_dir }} && python train.py"}
    manifest, _ = load_manifest(_write(tmp_path, manifest_data))
    ctx = RenderContext(
        outputs={"model": "/out/m"},
        params={"iters": 1},
        scratch_dir="/ws/scratch/job-abc",
    )
    rendered = render_manifest(manifest, ctx)
    assert "cd /ws/scratch/job-abc" in rendered.shell


def test_rendered_scratch_dir_lives_flat_under_workspace_root(tmp_path: Path) -> None:
    """Path convention is ``{workspace_root}/scratch/{job_id}`` — no
    workflow_id nesting, so the Artifacts sweeper can iterate one dir."""

    from hololab.manifest.render import rendered_scratch_dir

    p = rendered_scratch_dir(tmp_path, "job-xyz")
    assert Path(p) == tmp_path / "scratch" / "job-xyz"


def test_rendered_staging_dir_falls_back_to_scratch_when_unset(tmp_path: Path) -> None:
    """No ``staging_root`` configured → binding matches ``scratch_dir``.

    Keeps packs that reference ``{{ staging_dir }}`` working on nodes
    where the operator hasn't opted into the tmpfs fast path.
    """

    from hololab.manifest.render import rendered_scratch_dir, rendered_staging_dir

    assert rendered_staging_dir(None, tmp_path, "job-xyz") == rendered_scratch_dir(
        tmp_path, "job-xyz"
    )


def test_rendered_staging_dir_uses_staging_root_when_set(tmp_path: Path) -> None:
    """Configured ``staging_root`` diverts staging to its own tree."""

    from hololab.manifest.render import rendered_staging_dir

    staging_root = tmp_path / "shm-hololab"
    p = rendered_staging_dir(staging_root, tmp_path, "job-xyz")
    assert Path(p) == staging_root / "job-xyz"


def test_render_context_staging_dir_binding_defaults_to_scratch(tmp_path: Path) -> None:
    """``{{ staging_dir }}`` always resolves — falls back to scratch."""

    from hololab.manifest.render import RenderContext

    ctx = RenderContext(scratch_dir=str(tmp_path / "scratch" / "j"))
    bindings = ctx.to_bindings()
    assert bindings["staging_dir"] == bindings["scratch_dir"]

    ctx2 = RenderContext(
        scratch_dir=str(tmp_path / "scratch" / "j"),
        staging_dir=str(tmp_path / "shm" / "j"),
    )
    bindings2 = ctx2.to_bindings()
    assert bindings2["staging_dir"] == str(tmp_path / "shm" / "j")
    assert bindings2["scratch_dir"] != bindings2["staging_dir"]


# ---------------------------------------------------------------------------
# arrayed / arrayable / tags_from (M1: type-system foundation)
# ---------------------------------------------------------------------------


def test_manifest_arrayable_defaults_false(tmp_path: Path) -> None:
    manifest, _ = load_manifest(_write(tmp_path, VALID_MANIFEST))
    assert manifest.arrayable is False


def test_manifest_arrayable_accepts_true(tmp_path: Path) -> None:
    data = dict(VALID_MANIFEST)
    data["arrayable"] = True
    manifest, _ = load_manifest(_write(tmp_path, data))
    assert manifest.arrayable is True


def test_port_arrayed_defaults_false(tmp_path: Path) -> None:
    manifest, _ = load_manifest(_write(tmp_path, VALID_MANIFEST))
    assert manifest.inputs["colmap"].arrayed is False
    assert manifest.outputs["model"].arrayed is False


def test_input_arrayed_can_be_declared(tmp_path: Path) -> None:
    data = dict(VALID_MANIFEST)
    data["inputs"] = {"colmap": {"tags": ["colmap"], "arrayed": True}}
    manifest, _ = load_manifest(_write(tmp_path, data))
    assert manifest.inputs["colmap"].arrayed is True


def test_output_arrayed_can_be_declared(tmp_path: Path) -> None:
    data = dict(VALID_MANIFEST)
    data["outputs"] = {"model": {"tags": ["stg_model"], "arrayed": True}}
    manifest, _ = load_manifest(_write(tmp_path, data))
    assert manifest.outputs["model"].arrayed is True


def test_output_tags_from_references_valid_input(tmp_path: Path) -> None:
    """``tags_from`` may reference any declared input port name."""
    data = dict(VALID_MANIFEST)
    data["inputs"] = {"src": {"tags": ["any"]}}
    data["outputs"] = {"passthrough": {"tags": ["any"], "tags_from": "src", "arrayed": True}}
    manifest, _ = load_manifest(_write(tmp_path, data))
    assert manifest.outputs["passthrough"].tags_from == "src"


def test_output_tags_from_rejects_unknown_input(tmp_path: Path) -> None:
    """``tags_from`` naming a nonexistent input is a manifest error."""
    data = dict(VALID_MANIFEST)
    data["outputs"] = {"model": {"tags": ["stg_model"], "tags_from": "nonexistent"}}
    with pytest.raises(ValueError, match="tags_from"):
        load_manifest(_write(tmp_path, data))
