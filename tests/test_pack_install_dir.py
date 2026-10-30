from pathlib import Path

import pytest

from hololab.gateway.pack_manager import installation_destination
from hololab.node.config import NodeConfig
from hololab.node.packs import scan_multi_packs
from hololab.pack_install import install
from hololab.pack_publish import publish
from hololab.pack_registry import HashMismatch
from tests.test_content_scope import package
from tests.test_pack_dirs import _write_manifest


def test_config_and_destination(tmp_path):
    local = tmp_path / "repo"
    (local / "packs").mkdir(parents=True)
    target = tmp_path / "downloads"
    legacy = NodeConfig(pack_dirs=[local])
    assert legacy.install_dir is None
    assert legacy.pack_scan_dirs == [local]
    assert installation_destination(legacy.pack_dirs) == local / "packs"
    cfg = NodeConfig(pack_dirs=[local, target], install_dir=target)
    assert cfg.pack_scan_dirs == [target, local]
    assert installation_destination(cfg.pack_dirs, cfg.install_dir) == target
    assert not target.exists()
    assert NodeConfig.model_validate_json(cfg.model_dump_json()).install_dir == target
    with pytest.raises(ValueError, match="absolute"):
        NodeConfig(install_dir="relative")
    file = tmp_path / "file"
    file.touch()
    with pytest.raises(ValueError, match="directory"):
        NodeConfig(install_dir=file)


def test_installed_source_wins(tmp_path):
    local, target = tmp_path / "local", tmp_path / "downloads"
    for root in (local, target):
        _write_manifest(root / "demo", "demo", "1.0.0")
    cfg = NodeConfig(pack_dirs=[local], install_dir=target)
    packs = scan_multi_packs(cfg.pack_scan_dirs)
    assert len(packs) == 1
    assert packs[0].source_dir == target


def test_install_scan_repeat_and_mismatch(tmp_path):
    _, manifest, _ = package(tmp_path)
    source = (tmp_path / "registry").as_uri()
    release = publish(manifest, source, "test")
    cfg = NodeConfig(pack_dirs=[tmp_path / "development"], install_dir=tmp_path / "downloads")
    dest = installation_destination(cfg.pack_dirs, cfg.install_dir)
    result = install("kiri/demo@1.0.0", [source], dest, cfg.pack_scan_dirs, release.code_sha256)
    assert Path(result["manifest"]).is_relative_to(cfg.install_dir)
    assert len(scan_multi_packs(cfg.pack_scan_dirs)) == 1
    repeated = install("kiri/demo@1.0.0", [source], dest, cfg.pack_scan_dirs, release.code_sha256)
    assert repeated["manifest"] == result["manifest"]
    with pytest.raises(HashMismatch):
        install("kiri/demo@1.0.0", [source], dest, cfg.pack_scan_dirs, "0" * 64)
