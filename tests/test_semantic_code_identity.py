import shutil

import pytest
import yaml

from hololab.content_scope import (
    RELEASE_METADATA_FIELDS,
    IdentityMismatch,
    Scope,
    canonical,
    capture,
    tree_digest,
    verify,
)
from tests.test_content_scope import package


def test_dev_release_rename_and_metadata_preserve_identity(tmp_path):
    root, manifest, baselines = package(tmp_path)
    (root / ".packignore").write_text("/assets/\n/packs/*\n")
    manifest.write_text(manifest.read_text().replace("1.0.0", "0.1.1+"))
    dev = capture(manifest, baselines)
    moved = tmp_path / "published-elsewhere"
    shutil.copytree(root, moved, symlinks=True)
    (moved / "packs/demo").rename(moved / "packs/demo@0.1.1")
    released_manifest = moved / "packs/demo@0.1.1/manifest.yaml"
    released_manifest.write_text(released_manifest.read_text().replace("0.1.1+", "0.1.1"))
    released = capture(released_manifest, baselines, refresh_cold=True)
    assert released["sha256"] == dev["sha256"]
    assert released["manifest"]["sha256"] == dev["manifest"]["sha256"]
    assert released["controls"] != dev["controls"]
    with pytest.raises(IdentityMismatch, match="manifest"):
        verify(released_manifest, dev)  # Semantic equality never bypasses byte guards.
    for field in RELEASE_METADATA_FIELDS - {"version"}:
        released_manifest.write_text(released_manifest.read_text() + f"\n{field}: true\n")
        assert capture(released_manifest, baselines)["sha256"] == released["sha256"]


def test_semantic_changes_and_cold_refresh(tmp_path):
    root, manifest, baselines = package(tmp_path)
    before = capture(manifest, baselines)
    unchanged = capture(manifest, baselines, refresh_cold=True)
    assert unchanged["sha256"] == before["sha256"]
    assert unchanged["cold"]["baseline_status"] == "updated"
    assert unchanged["cold"]["content_changed"] is False
    (root / "thirdparty/lib.py").write_text("changed cold code")
    assert capture(manifest, baselines)["sha256"] == before["sha256"]
    refreshed = capture(manifest, baselines, refresh_cold=True)
    assert refreshed["sha256"] != before["sha256"]
    assert refreshed["cold"]["previous_sha256"] == before["cold"]["sha256"]
    assert refreshed["cold"]["content_changed"] is True
    assert refreshed["cold"]["verification"] == "not_checked"
    assert refreshed["hot"]["verification"] == "verified_at_submission"
    verify(manifest, before)
    (root / "stg_state.py").write_text("changed hot code")
    assert capture(manifest, baselines)["sha256"] != refreshed["sha256"]


@pytest.mark.parametrize(
    "addition", ["unknown_future_field: true", "params: {version: {type: string, default: new}}"]
)
def test_unknown_and_nested_version_fields_stay_in_identity(tmp_path, addition):
    _, manifest, baselines = package(tmp_path)
    before = capture(manifest, baselines)
    manifest.write_text(manifest.read_text() + "\n" + addition + "\n")
    assert capture(manifest, baselines)["sha256"] != before["sha256"]


def test_format_one_records_remain_verifiable(tmp_path):
    _, manifest, baselines = package(tmp_path)
    record = capture(manifest, baselines)
    record["format"] = 1
    record["hot"]["sha256"] = tree_digest(list(Scope.load(manifest).entries(hot_only=True)))
    record["sha256"] = canonical(
        [1, record["controls"], record["hot"]["sha256"], record["cold"]["sha256"]]
    )
    verify(manifest, record)


def test_old_package_without_root_cold_or_ignore(tmp_path):
    _, manifest, baselines = package(tmp_path)
    data = yaml.safe_load(manifest.read_bytes())
    del data["root"], data["cold"]
    manifest.write_text(yaml.safe_dump(data))
    (manifest.parent / "main.py").write_text("print('legacy package')")
    before = capture(manifest, baselines)
    assert before["root"] == "."
    assert before["hot"]["stats"]["files"] == 1
    assert before["cold"]["stats"]["files"] == 0
    assert before["controls"]["packignore_sha256"] is None
    data["version"] = "1.0.1"
    manifest.write_text(yaml.safe_dump(data))
    assert capture(manifest, baselines)["sha256"] == before["sha256"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("exec", {"shell": "python other.py"}),
        ("source_entry", "other.py"),
        ("inputs", {"new_input": {"tags": ["text"]}}),
        ("outputs", {"new_output": {"tags": ["text"]}}),
        ("params", {"steps": {"type": "int", "default": 42}}),
        ("runtime", {"env": "another-env"}),
    ],
)
def test_execution_and_interface_changes_alter_identity(tmp_path, field, value):
    _, manifest, baselines = package(tmp_path)
    before = capture(manifest, baselines)
    data = yaml.safe_load(manifest.read_bytes())
    data[field] = value
    manifest.write_text(yaml.safe_dump(data))
    assert capture(manifest, baselines)["sha256"] != before["sha256"]
