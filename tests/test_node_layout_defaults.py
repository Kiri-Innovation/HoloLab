"""Node data layout: <node data dir>/{artifacts,installed_packs,weights} and former defaults."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hololab.node.config import NodeConfig


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "hololab"
    monkeypatch.setenv("HOLOLAB_HOME", str(root))
    return root


def test_defaults_are_node_owned_directories(home: Path) -> None:
    cfg = NodeConfig()
    node = home / "node"
    assert cfg.workspace_root == node / "artifacts"
    assert cfg.effective_install_dir == node / "installed_packs"
    assert cfg.cache_env()["TORCH_HOME"] == str(node / "weights" / "torch")


def test_former_default_workspace_stays_readable_only_for_default_layout(home: Path) -> None:
    former = home / "node" / "workspace"
    assert NodeConfig().effective_legacy_workspace_roots == []  # absent: nothing added
    former.mkdir(parents=True)
    assert NodeConfig().effective_legacy_workspace_roots == [former]
    assert NodeConfig(legacy_workspace_roots=[former]).effective_legacy_workspace_roots == [former]
    explicit = NodeConfig(workspace_root=home / "elsewhere")
    assert explicit.effective_legacy_workspace_roots == []
    assert NodeConfig().legacy_workspace_roots == []  # never written into the config


def _installed(root: Path, name: str = "demo@0.1.0") -> Path:
    target = root / "community" / name
    target.mkdir(parents=True)
    (target / ".hololab-install.json").write_text(json.dumps({"source": "file:///x"}))
    return target


def test_former_install_dir_is_scanned_when_it_holds_installed_packages(
    home: Path, tmp_path: Path
) -> None:
    former = home / "node" / "packs"
    dev = tmp_path / "dev"
    assert NodeConfig(pack_dirs=[dev]).former_install_dir_in_use is None
    former.mkdir(parents=True)
    assert NodeConfig(pack_dirs=[dev]).former_install_dir_in_use is None  # no receipts
    _installed(former)
    cfg = NodeConfig(pack_dirs=[dev])
    assert cfg.former_install_dir_in_use == former
    assert cfg.pack_scan_dirs == [home / "node" / "installed_packs", former, dev]


def test_explicit_installed_packs_does_not_pull_in_the_former_directory(
    home: Path, tmp_path: Path
) -> None:
    _installed(home / "node" / "packs")
    cfg = NodeConfig(pack_dirs=[tmp_path / "dev"], installed_packs=tmp_path / "mine")
    assert cfg.former_install_dir_in_use is None
    assert cfg.pack_scan_dirs == [tmp_path / "mine", tmp_path / "dev"]
