"""Manifest-local scope rules are root-relative and subtractive only."""

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
from tests.test_content_scope import package


def test_rules_bind_selected_manifest_and_cannot_restore_root_ignored_files(tmp_path):
    root, manifest, baselines = package(tmp_path)
    other = root / "packs/other"
    other.mkdir()
    (other / "manifest.yaml").write_bytes(manifest.read_bytes())
    (other / ".packignore").write_text("!invalid-for-the-other-pack\n")
    # Root re-inclusions retain ordinary gitignore behavior.
    (root / ".packignore").write_text("/assets/\n/packs/\n*.py\n!stg_state.py\n")
    own = manifest.parent / ".packignore"
    own.write_text("/thirdparty/\n")
    scope = Scope.load(manifest)
    entries = {e.relative: e.layer for e in scope.entries()}
    assert entries["stg_state.py"] == "hot"
    assert entries["assets/large.bin"] == "ignored"
    assert entries["thirdparty/lib.py"] == "ignored"
    assert entries["packs/other/.packignore"] == "ignored"
    assert entries["packs/demo/.packignore"] == "hot"
    assert entries["packs/demo/manifest.yaml"] == "hot"
    assert set(e.relative for e in scope.entries(hot_only=True)) == {
        name for name, layer in entries.items() if layer == "hot"
    }
    record = capture(manifest, baselines)
    assert "package_packignore_sha256" in record["controls"]
    assert record["content"]["stats"]["files"] == 2
    verify(manifest, record)


@pytest.mark.parametrize(
    "rule",
    [
        "!assets/\n",
        "!\n",
        "../secret\n",
        "/utils/../secret\n",
        "/utils/..   \n",
        r"/utils/\.\./secret",
    ],
)
def test_reinclusion_and_traversal_are_rejected(tmp_path, rule):
    _, manifest, _ = package(tmp_path)
    (manifest.parent / ".packignore").write_text(rule)
    with pytest.raises(ValueError, match=r"package \.packignore:1"):
        Scope.load(manifest)


def test_escaped_literals_and_gitignore_patterns_work(tmp_path):
    root, manifest, _ = package(tmp_path)
    for name in ["!literal.py", "#literal.py", "temporary.log", "keep.txt"]:
        (root / name).write_text("data")
    (manifest.parent / ".packignore").write_text(
        "# comment\n\\!literal.py\n\\#literal.py\n**/*.log\n"
    )
    layers = {e.relative: e.layer for e in Scope.load(manifest).entries()}
    assert all(layers[p] == "ignored" for p in ["!literal.py", "#literal.py", "temporary.log"])
    assert layers["keep.txt"] == "hot"


def test_protected_files_survive_even_when_their_parents_are_excluded(tmp_path):
    root, manifest, baselines = package(tmp_path)
    (root / "setup").mkdir()
    (root / "setup/hook.sh").write_text("#!/bin/sh\n")
    manifest.write_text(manifest.read_text() + "setup: {script: setup/hook.sh, shell: bash}\n")
    (manifest.parent / ".packignore").write_text("*\n")
    record = capture(manifest, baselines)
    names = {e.relative for e in Scope.load(manifest).entries(hot_only=True)}
    assert names == {
        ".packignore",
        "packs/demo/.packignore",
        "packs/demo/manifest.yaml",
        "setup/hook.sh",
    }
    assert record["content"]["stats"]["files"] == 2  # manifest + setup, neither control
    assert record["cold"]["stats"]["files"] == 0
    verify(manifest, record)


@pytest.mark.parametrize("change", ["add", "remove", "edit", "symlink"])
def test_control_changes_invalidate_identity_and_fingerprint(tmp_path, change):
    root, manifest, baselines = package(tmp_path)
    control = manifest.parent / ".packignore"
    if change != "add":
        control.write_text("/thirdparty/\n")
    record = capture(manifest, baselines)
    if change == "remove":
        control.unlink()
    elif change == "symlink":
        control.unlink()
        control.symlink_to(root / ".packignore")
    else:
        control.write_text("!invalid-new-rules\n")
    for check in (verify, verification_fingerprint):
        with pytest.raises(IdentityMismatch, match=r"package \.packignore"):
            check(manifest, record)


def test_package_control_must_be_regular_file(tmp_path):
    root, manifest, _ = package(tmp_path)
    control = manifest.parent / ".packignore"
    control.symlink_to(root / "missing")
    with pytest.raises(ValueError, match="regular control file"):
        Scope.load(manifest)
    control.unlink()
    control.mkdir()
    with pytest.raises(ValueError, match="regular control file"):
        Scope.load(manifest)


def test_cold_baseline_is_recomputed_for_changed_package_rules(tmp_path):
    _root, manifest, baselines = package(tmp_path)
    original = capture(manifest, baselines)
    control = manifest.parent / ".packignore"
    control.write_text("/thirdparty/\n")
    narrow = capture(manifest, baselines)
    assert narrow["cold"]["stats"]["files"] == 0
    assert narrow["cold"]["sha256"] != original["cold"]["sha256"]
    control.write_text("")
    restored = capture(manifest, baselines)
    assert restored["cold"]["sha256"] == original["cold"]["sha256"]


def test_absent_package_control_and_root_equals_pack_keep_legacy_controls(tmp_path):
    root, manifest, baselines = package(tmp_path)
    original = capture(manifest, baselines)
    assert set(original["controls"]) == {"manifest_sha256", "packignore_sha256", "gitignore_sha256"}
    # When manifest and root coincide, this is the existing root control, not
    # a second package rule file. Its negation syntax remains supported.
    flat = root / "flat"
    flat.mkdir()
    target = flat / "manifest.yaml"
    target.write_text(
        manifest.read_text()
        .replace("root: ../..", "root: .")
        .replace("cold: [thirdparty/]", "cold: []")
    )
    (flat / ".packignore").write_text("*\n!keep.py\n")
    (flat / "keep.py").write_text("x=1")
    scope = Scope.load(target)
    assert scope.package_ignore_bytes is None
    assert {e.relative for e in scope.entries(hot_only=True)} == {
        ".packignore",
        "manifest.yaml",
        "keep.py",
    }


@pytest.mark.parametrize("root_below_manifest", [False, True])
def test_publish_and_install_use_the_same_subtractive_scope(tmp_path, root_below_manifest):
    root, manifest, baselines = package(tmp_path)
    if root_below_manifest:
        # Cover the existing root: child/ layout, whose package control sits
        # beside the manifest outside the walked root.
        manifest = root / "manifest.yaml"
        manifest.write_text(
            (root / "packs/demo/manifest.yaml")
            .read_text()
            .replace("root: ../..", "root: content")
            .replace("cold: [thirdparty/]", "cold: []")
        )
        content = root / "content"
        content.mkdir()
        (content / "keep.py").write_text("keep=1")
        (content / "remove.py").write_text("remove=1")
        control = manifest.parent / ".packignore"
        control.write_text("/remove.py\n")
    else:
        control = manifest.parent / ".packignore"
        control.write_text("/thirdparty/\n")
    expected = capture(manifest, baselines)
    source = (tmp_path / "registry").as_uri()
    release = publish(manifest, source, "test")
    assert release.code_sha256 == expected["sha256"]
    with tarfile.open(tmp_path / "registry" / release.archive) as archive:
        members = archive.getnames()
        assert not any(
            name.endswith(("thirdparty/lib.py", "remove.py", "large.bin")) for name in members
        )
        assert ("packs/demo/.packignore" if not root_below_manifest else ".packignore") in members
    result = install("demo@1.0.0", [source], tmp_path / "installed", [])
    installed = Path(result["manifest"])
    installed_record = capture(installed, Baselines(tmp_path / "installed-baselines"))
    assert installed_record["sha256"] == expected["sha256"]
    assert (
        installed_record["controls"]["package_packignore_sha256"]
        == expected["controls"]["package_packignore_sha256"]
    )
    verify(installed, installed_record)
