import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from hololab.content_scope import (
    Baselines,
    IdentityMismatch,
    Scope,
    capture,
    verification_fingerprint,
    verify,
)
from hololab.pack_install import install
from hololab.pack_publish import publish


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


def test_verification_fingerprint_detects_same_size_code_change(tmp_path):
    root, manifest, baselines = package(tmp_path)
    record = capture(manifest, baselines)
    before = verification_fingerprint(manifest, record)
    source = root / "stg_state.py"
    original_mtime = source.stat().st_mtime_ns
    # Deliberately preserve byte length and restore mtime: ctime is the
    # additional witness that prevents an ordinary metadata-only spoof from
    # borrowing a prior full-tree verification.
    source.write_text("print('changed!')\n")
    os.utime(source, ns=(original_mtime, original_mtime))
    after = verification_fingerprint(manifest, record)
    assert after != before
    with pytest.raises(IdentityMismatch, match=r"hot"):
        verify(manifest, record)


def test_content_inventory_includes_manifest_but_not_ignored_controls(tmp_path):
    root, manifest, baselines = package(tmp_path)
    record = capture(manifest, baselines, refresh_cold=True)
    # The semantic identity omits the selected manifest, but the package-size
    # display must count it. ``.packignore`` and its ignored asset stay out.
    assert record["content"]["stats"]["files"] == 3
    assert record["content"]["stats"]["bytes"] == (
        manifest.stat().st_size
        + (root / "stg_state.py").stat().st_size
        + (root / "thirdparty" / "lib.py").stat().st_size
    )


def test_declared_setup_script_is_hot_when_gitignore_excludes_it(tmp_path):
    root, manifest, _ = package(tmp_path)
    (root / "setup").mkdir()
    (root / "setup" / "pack-setup.sh").write_text("#!/usr/bin/env bash\n")
    (root / ".gitignore").write_text("*.sh\n")
    manifest.write_text(
        manifest.read_text() + "setup:\n  script: setup/pack-setup.sh\n  shell: bash\n"
    )

    entries = {entry.relative: entry.layer for entry in Scope.load(manifest).entries()}

    assert entries["setup/pack-setup.sh"] == "hot"


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


def test_symlinked_parent_does_not_change_identity(tmp_path):
    _, manifest, baselines = package(tmp_path)
    record = capture(manifest, baselines)
    (tmp_path / "link").symlink_to(tmp_path)
    linked = tmp_path / "link" / manifest.relative_to(tmp_path)
    assert capture(linked, baselines)["sha256"] == record["sha256"]
    verify(linked, record)


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


def test_gitignore_negation_requires_reincluding_parent(tmp_path):
    root, manifest, baselines = package(tmp_path)
    (root / "assets/code.py").write_text("code")
    (root / ".packignore").write_text("/assets/\n!assets/code.py\n")
    scope = Scope.load(manifest)
    assert next(e for e in scope.entries() if e.relative == "assets/code.py").layer == "ignored"
    capture(manifest, baselines)  # Full and pruned enumeration must agree.
    (root / ".packignore").write_text("/assets/*\n!assets/code.py\n")
    scope = Scope.load(manifest)
    assert next(e for e in scope.entries() if e.relative == "assets/code.py").layer == "hot"
    capture(manifest, baselines)


def test_content_inventory_reports_layers_top_level_and_gitignored_size(tmp_path):
    root, manifest, baselines = package(tmp_path)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text("/output/\n")
    (root / "output").mkdir()
    (root / "output" / "checkpoint.bin").write_bytes(b"x" * 12)
    record = capture(manifest, baselines, refresh_cold=True)

    content = record["content"]
    assert content["hot"]["files"] > 0
    assert content["cold"]["directories"] == ["thirdparty"]
    assert content["cold"]["bytes"] == len(b"cold = 1\n")
    assert content["excluded"]["bytes"] == len(b"asset") + 12
    assert ("thirdparty", "cold") in {
        (item["name"], item["layer"]) for item in content["top_level"]
    }
    assert content["excluded"]["top_level"][0]["name"] == "output"
    thirdparty = next(item for item in content["tree"] if item["name"] == "thirdparty")
    assert thirdparty["layer"] == "cold"
    assert thirdparty["kind"] == "directory"
    assert thirdparty["children"][0]["name"] == "lib.py"


def test_gitignore_checkout_identity_matches_archive_and_install(tmp_path):
    root, manifest, baselines = package(tmp_path)
    repo = root.parent
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True)

    (repo / ".gitignore").write_text("/source/ancestor.py\n/source/blocked/\n")
    (repo / ".git/info/exclude").write_text("machine-local.py\n")
    global_excludes = repo / "global-ignore"
    global_excludes.write_text("global-local.py\n")
    git("config", "core.excludesFile", str(global_excludes))
    (root / ".gitignore").write_text("*.bin\n!/blocked/keep.py\n")
    (root / "blocked").mkdir()
    (root / "blocked/.gitignore").write_text("!keep.py\n")
    (root / "thirdparty/.gitignore").write_text("/build/\n!keep.bin\n")
    (root / "thirdparty/build").mkdir()
    (manifest.parent / ".packignore").write_text("/thirdparty/pack-only.py\n")
    included = {
        "thirdparty/keep.bin",
        "thirdparty/build/tracked.bin",
        "machine-local.py",
        "global-local.py",
    }
    excluded = {
        "ancestor.py",
        "blocked/keep.py",
        "thirdparty/drop.bin",
        "thirdparty/build/untracked.bin",
        "thirdparty/pack-only.py",
    }
    for path in included | excluded:
        (root / path).write_text("original\n")
    git("add", "-f", "source/thirdparty/build/tracked.bin", "source/thirdparty/pack-only.py")
    entries = list(Scope.load(manifest).entries())
    selected = {e.relative for e in entries if e.layer in {"hot", "cold"}}
    assert included <= selected
    assert excluded.isdisjoint(selected)

    before = capture(manifest, baselines)
    for path in excluded:
        (root / path).write_text("changed ignored file\n")
    assert capture(manifest, baselines, refresh_cold=True)["sha256"] == before["sha256"]
    (root / "thirdparty/build/tracked.bin").write_text("changed tracked file\n")
    expected = capture(manifest, baselines, refresh_cold=True)["sha256"]
    assert expected != before["sha256"]

    source = (tmp_path / "registry").as_uri()
    release = publish(manifest, source, "test")
    assert release.code_sha256 == expected
    with tarfile.open(tmp_path / "registry" / release.archive) as archive:
        assert {member.name for member in archive if not member.isdir()} == selected
    installed = install("demo@1.0.0", [source], tmp_path / "installed", [])
    assert installed["code_sha256"] == expected
    assert capture(Path(installed["manifest"]), baselines)["sha256"] == expected

    # A cached cold digest must be refreshed when Git changes its membership.
    (root / "thirdparty/.gitignore").write_text("/build/\n")
    updated = capture(manifest, baselines)
    assert updated["cold"]["stats"]["files"] == before["cold"]["stats"]["files"] - 1
    assert updated["cold"]["sha256"] != before["cold"]["sha256"]


def test_gitignore_uses_submodule_index_and_rules(tmp_path):
    root, manifest, _ = package(tmp_path)
    submodule = root / "thirdparty" / "vendor"
    submodule.mkdir()
    for repo in (root, submodule):
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (submodule / ".gitignore").write_text("*.so\n")
    (submodule / "tracked.so").touch()
    (submodule / "generated.so").touch()
    subprocess.run(["git", "-C", str(submodule), "add", "-f", "tracked.so"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{'1' * 40},thirdparty/vendor",
        ],
        check=True,
    )
    layers = {e.relative: e.layer for e in Scope.load(manifest).entries()}
    assert layers["thirdparty/vendor/tracked.so"] == "cold"
    assert layers["thirdparty/vendor/generated.so"] == "ignored"
