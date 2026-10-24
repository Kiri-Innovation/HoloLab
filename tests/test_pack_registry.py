import json

import pytest

from hololab.content_scope import capture
from hololab.pack_publish import publish
from hololab.pack_registry import Index, RegistryError, SourceUnavailable, find_release, read_index
from tests.test_content_scope import package


def test_static_source_and_absence(tmp_path):
    (tmp_path / "index.json").write_text(Index(authority="central").model_dump_json())
    assert read_index(tmp_path.as_uri()).authority == "central"
    with pytest.raises(RegistryError, match="找不到"):
        find_release("demo@0.1.0", [tmp_path.as_uri()])
    with pytest.raises(SourceUnavailable, match="源不可达"):
        read_index((tmp_path / "absent").as_uri())
    (tmp_path / "index.json").write_text(json.dumps({"format": 99, "authority": "x"}))
    with pytest.raises(ValueError):
        read_index(tmp_path.as_uri())


def test_publish_preserves_dev_identity_and_rejects_overwrites(tmp_path):
    root, manifest, baselines = package(tmp_path)
    manifest.write_text(manifest.read_text().replace("1.0.0", "0.1.1+"))
    original = manifest.read_bytes()
    identity = capture(manifest, baselines)
    source = (tmp_path / "registry").as_uri()
    release = publish(manifest, source, "test")
    assert release.name == "kiri/demo" and release.version == "0.1.1"
    assert release.code_sha256 == identity["sha256"]
    assert manifest.read_bytes() == original
    assert read_index(source).releases["kiri/demo@0.1.1"] == release
    with pytest.raises(RegistryError, match="不允许重复"):
        publish(manifest, source, "test")
    (root / "stg_state.py").write_text("changed")
    with pytest.raises(RegistryError, match="不可变版本冲突"):
        publish(manifest, source, "test")


def test_install_checks_archive_and_content_and_is_local_first(tmp_path):
    from hololab.node.packs import scan_packs
    from hololab.pack_install import install, resolve
    from hololab.pack_registry import HashMismatch

    _, manifest, _ = package(tmp_path)
    source = (tmp_path / "registry").as_uri()
    release = publish(manifest, source, "test")
    destination = tmp_path / "installed"
    result = install("kiri/demo@1.0.0", [source], destination, [])
    assert result["code_sha256"] == release.code_sha256
    assert len(scan_packs(destination)) == 1
    offline = resolve("demo@1.0.0", [(tmp_path / "offline").as_uri()], [destination])
    assert offline["installed"] and offline["hash_verified"]
    index = tmp_path / "registry/index.json"
    value = json.loads(index.read_text())
    value["releases"]["kiri/demo@1.0.0"]["code_sha256"] = "0" * 64
    index.write_text(json.dumps(value))
    with pytest.raises(HashMismatch, match="哈希不匹配"):
        install("demo@1.0.0", [source], tmp_path / "clean", [])
    assert not (tmp_path / "clean/kiri/demo@1.0.0").exists()
