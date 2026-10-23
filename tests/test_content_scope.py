import os
import shutil
import subprocess
from pathlib import Path

import pytest

from hololab.content_scope import Baselines, IdentityMismatch, Scope, capture, verify


def package(tmp_path):
    root = tmp_path / "source"
    pack = root / "packs" / "demo"
    pack.mkdir(parents=True)
    manifest = pack / "manifest.yaml"
    manifest.write_text("""apiVersion: hololab.dev/v1
kind: Algorithm
name: demo
version: 1.0.0
root: ../..
cold: [thirdparty/]
inputs: {}
outputs: {out: {tags: [text]}}
runtime: {env: test}
exec: {shell: 'python "{{ package_root }}/stg_state.py"'}
""")
    (root / "stg_state.py").write_text("print('original')\n")
    (root / "thirdparty").mkdir()
    (root / "thirdparty" / "lib.py").write_text("cold = 1\n")
    (root / "assets").mkdir()
    (root / "assets" / "large.bin").write_bytes(b"asset")
    (root / ".packignore").write_text("/assets/\n.packignore\n")
    return root, manifest, Baselines(tmp_path / "baselines")


def test_hot_cold_ignored_and_rule_controls(tmp_path, monkeypatch):
    root, manifest, baselines = package(tmp_path)
    record = capture(manifest, baselines)
    cold_time = record["cold"]["baseline_at"]
    (root / "thirdparty" / "lib.py").write_text("cold = 2\n")
    (root / "assets" / "large.bin").write_bytes(b"changed asset")
    verify(manifest, record)
    again = capture(manifest, baselines)
    assert again["sha256"] == record["sha256"]
    assert again["cold"]["baseline_at"] == cold_time
    # Verification must not visit cold contents, even for metadata.
    original = os.scandir

    def checked(path):
        assert Path(path) != root / "thirdparty"
        assert Path(path) != root / "assets"
        return original(path)

    monkeypatch.setattr(os, "scandir", checked)
    verify(manifest, record)
    (root / "stg_state.py").write_text("print('new code')\n")
    with pytest.raises(IdentityMismatch, match=r"hot.*提交时=.*当前=.*Fork"):
        verify(manifest, record)
    (root / ".packignore").write_text("*\n")
    with pytest.raises(IdentityMismatch, match=r"\.packignore"):
        verify(manifest, record)


def test_manifest_compared_before_loading_or_ignore(tmp_path):
    root, manifest, baselines = package(tmp_path)
    record = capture(manifest, baselines)
    manifest.write_text("invalid: [yaml")
    (root / ".packignore").write_text("*\n")
    with pytest.raises(IdentityMismatch, match="manifest"):
        verify(manifest, record)


def test_relocation_symlinks_modes_and_new_files(tmp_path):
    root, manifest, baselines = package(tmp_path)
    (root / "outside").symlink_to(tmp_path / "not-present", target_is_directory=True)
    record = capture(manifest, baselines)
    moved = tmp_path / "moved"
    shutil.copytree(root, moved, symlinks=True)
    target = moved / manifest.relative_to(root)
    verify(target, record)
    assert Scope.load(target).root == moved
    assert capture(target, baselines)["sha256"] == record["sha256"]
    (moved / "new.bak").write_text("new")
    with pytest.raises(IdentityMismatch):
        verify(target, record)
    (moved / "new.bak").unlink()
    (moved / "stg_state.py").chmod(0o755)
    with pytest.raises(IdentityMismatch):
        verify(target, record)


def test_cold_refresh_and_release(tmp_path):
    root, manifest, baselines = package(tmp_path)
    before = capture(manifest, baselines)
    (root / "thirdparty" / "lib.py").write_text("cold = 99")
    manual = capture(manifest, baselines, refresh_cold=True)
    assert manual["cold"]["sha256"] != before["cold"]["sha256"]
    assert manual["cold"]["baseline_at"] > before["cold"]["baseline_at"]
    verify(manifest, before)  # Manual refresh never mutates historical records.
    (root / "thirdparty" / "lib.py").write_text("cold = 100")
    manifest.write_text(manifest.read_text().replace("1.0.0", "1.0.1"))
    release = capture(manifest, baselines)
    assert release["cold"]["sha256"] != manual["cold"]["sha256"]


def test_unrestricted_ignore_includes_controls(tmp_path):
    root, manifest, baselines = package(tmp_path)
    (root / ".packignore").write_text("*\n!stg_state.py\n")
    record = capture(manifest, baselines)
    names = {e.relative for e in Scope.load(manifest).entries(hot_only=True)}
    assert names == {".packignore", "stg_state.py", "packs/demo/manifest.yaml"}
    assert not any("ignore" in str(w) for w in record["quality_hints"])
    (root / "untracked.py").write_text("ignored code")
    verify(manifest, record)


def test_git_boundary(tmp_path):
    root, manifest, _ = package(tmp_path)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    manifest.write_text(manifest.read_text().replace("root: ../..", "root: ../../.."))
    with pytest.raises(ValueError, match="escapes repository"):
        Scope.load(manifest)
