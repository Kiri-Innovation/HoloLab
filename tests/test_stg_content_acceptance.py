"""Acceptance against a real STG tree, with all mutations confined to a temp copy."""

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from hololab.content_scope import Baselines, IdentityMismatch, Scope, capture, verify
from hololab.manifest import RenderContext, render_manifest

STG = Path(__file__).resolve().parents[2] / "SpacetimeGaussians"


@pytest.mark.skipif(not (STG / ".packignore").exists(), reason="migrated STG tree not installed")
def test_real_stg_relocation_and_five_changes(tmp_path):
    manifest = STG / "packs/stg-init@0.3.2/manifest.yaml"
    baselines = Baselines(tmp_path / "baselines")
    before = time.monotonic()
    original = capture(manifest, baselines)
    assert 100 <= original["hot"]["stats"]["files"] <= 200
    assert original["hot"]["stats"]["bytes"] < 2_000_000
    # The mmcv runtime inventory is currently 5967 files after excluding
    # non-runtime sources. Keep a floor that detects a material cold-layer loss
    # without depending on package-manager file-count drift.
    assert original["cold"]["stats"]["files"] >= 5900
    print(
        "REAL_STG",
        json.dumps(
            {
                "hot": original["hot"],
                "cold": original["cold"],
                "ignored": original["ignored"],
                "sha256": original["sha256"],
                "capture_seconds": time.monotonic() - before,
            }
        ),
    )
    moved = tmp_path / "moved"
    for entry in Scope.load(manifest).entries():
        if entry.layer not in ("hot", "cold"):
            continue
        target = moved / entry.relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.mode == "120000":
            target.symlink_to(os.readlink(entry.path))
        else:
            shutil.copy2(entry.path, target)
    target_manifest = moved / manifest.relative_to(STG)
    verify(target_manifest, original)
    relocated = capture(target_manifest, baselines)
    assert relocated["sha256"] == original["sha256"]
    # /packs/* excludes siblings; the selected manifest is forced into hot scope.
    sibling = moved / "packs/stg-train@0.4.7/manifest.yaml"
    sibling.parent.mkdir(parents=True, exist_ok=True)
    sibling.write_bytes((STG / "packs/stg-train@0.4.7/manifest.yaml").read_bytes())
    assert capture(target_manifest, baselines)["sha256"] == relocated["sha256"]
    sibling.write_text("a changed sibling manifest, deliberately invalid YAML: [")
    assert capture(target_manifest, baselines)["sha256"] == relocated["sha256"]
    original_manifest = target_manifest.read_bytes()
    # Real STG release metadata must not alter semantic identity, including a
    # release-time baseline refresh. Mutations only affect the temporary copy.
    dev_manifest = original_manifest.replace(b"version: 0.3.2", b"version: 0.3.2+")
    assert dev_manifest != original_manifest
    target_manifest.write_bytes(dev_manifest)
    dev = capture(target_manifest, baselines)
    target_manifest.write_bytes(original_manifest)
    released = capture(target_manifest, baselines, refresh_cold=True)
    assert dev["sha256"] == released["sha256"] == original["sha256"]
    target_manifest.write_bytes(original_manifest + b"\nyank: true\n")
    assert capture(target_manifest, baselines)["sha256"] == original["sha256"]
    target_manifest.write_bytes(original_manifest)
    relocated = released
    target_manifest.write_bytes(original_manifest + b"\n# changed own manifest\n")
    with pytest.raises(IdentityMismatch, match="manifest"):
        verify(target_manifest, relocated)
    target_manifest.write_bytes(original_manifest)
    scope = Scope.load(target_manifest)
    rendered = render_manifest(
        scope.manifest,
        RenderContext(
            package_root=str(moved),
            manifest_dir=str(target_manifest.parent),
            inputs={"colmap_4d": "/frames"},
            outputs={
                "initial_state": "/state",
                "supervision": "/supervision",
                "preview": "/preview",
            },
            params={"configpath": "configs/n3d_full/stg_state_default.json"},
            scratch_dir="/scratch",
        ),
    )
    assert Path(rendered.working_dir) == moved
    assert str(moved / "stg_state.py") in rendered.shell
    assert (Path(rendered.working_dir) / "configs/n3d_full/stg_state_default.json").is_file()

    cold = next(e.path for e in scope.entries() if e.layer == "cold" and e.mode != "120000")
    cold.write_bytes(cold.read_bytes() + b"\nchanged cold code\n")
    verify(target_manifest, relocated)
    assert capture(target_manifest, baselines)["cold"] == relocated["cold"]
    (moved / "assets").mkdir()
    (moved / "assets/new.bin").write_bytes(b"changed excluded content")
    assert capture(target_manifest, baselines)["sha256"] == relocated["sha256"]
    hot = moved / "stg_state.py"
    old_hot = hot.read_bytes()
    hot.write_bytes(old_hot + b"\n# changed hot code\n")
    with pytest.raises(IdentityMismatch, match="hot"):
        verify(target_manifest, relocated)
    assert capture(target_manifest, baselines)["sha256"] != relocated["sha256"]
    hot.write_bytes(old_hot)
    refreshed = capture(target_manifest, baselines, refresh_cold=True)
    assert refreshed["sha256"] != relocated["sha256"]
    assert refreshed["cold"]["baseline_status"] == "updated"
    assert refreshed["cold"]["verification"] == "not_checked"
    assert refreshed["cold"]["previous_sha256"] == relocated["cold"]["sha256"]
    ignore = moved / ".packignore"
    ignore.write_bytes(ignore.read_bytes() + b"\n")
    with pytest.raises(IdentityMismatch, match=r"\.packignore"):
        verify(target_manifest, relocated)
    assert capture(target_manifest, baselines)["sha256"] != refreshed["sha256"]
