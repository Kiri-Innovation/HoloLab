"""Installation reminders are advisory, but use the real capability checker."""

from __future__ import annotations

import sys
import venv
from pathlib import Path

import yaml
from typer.testing import CliRunner

from hololab.cli import app
from hololab.pack_publish import publish
from tests.test_content_scope import package

runner = CliRunner()


def _conda_passthrough(path: Path) -> Path:
    """A tiny conda-run compatible wrapper so the test checks a real venv."""
    path.write_text('#!/bin/sh\nwhile [ "$1" != python ]; do shift; done\nexec "$@"\n')
    path.chmod(0o755)
    return path


def _install(
    tmp_path: Path,
    requires: object,
    name: str,
    *,
    map_environment: bool = True,
) -> str:
    _root, manifest, _ = package(tmp_path)
    data = yaml.safe_load(manifest.read_text())
    data.update(name=f"community/{name}", version="0.5.2", requires=requires)
    manifest.write_text(yaml.safe_dump(data))
    registry = tmp_path / "registry"
    publish(manifest, registry.as_uri(), "test")

    env = tmp_path / "env"
    venv.EnvBuilder(with_pip=False).create(env)
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "pack_dirs": [str(tmp_path / "installed")],
                "pack_sources": [registry.as_uri()],
                "envs": {"test": str(env)} if map_environment else {},
                "conda_bin": str(_conda_passthrough(tmp_path / "conda")),
            }
        )
    )
    result = runner.invoke(
        app,
        ["pack", "install", f"community/{name}@0.5.2", "--config", str(config)],
    )
    assert result.exit_code == 0, result.output
    return result.output


def test_install_warns_for_missing_declared_packages(tmp_path: Path) -> None:
    output = _install(tmp_path, {"packages": ["hololab-notice-missing"]}, "notice-missing")

    assert "declared requirements are not yet installed in environment 'test'" in output
    assert "  - hololab-notice-missing" in output
    assert "hololab pack setup community/notice-missing@0.5.2" in output


def test_install_is_quiet_when_declared_packages_are_satisfied(tmp_path: Path) -> None:
    output = _install(tmp_path, {"python": f">={sys.version_info.major}"}, "notice-satisfied")

    assert "Note:" not in output
    assert "hololab pack setup" not in output


def test_install_is_quiet_for_explicitly_empty_requires(tmp_path: Path) -> None:
    output = _install(tmp_path, {}, "notice-empty")

    assert "Note:" not in output
    assert "hololab pack setup" not in output


def test_install_mentions_unconfigured_environment(tmp_path: Path) -> None:
    output = _install(
        tmp_path,
        {"packages": ["hololab-notice-missing"]},
        "notice-no-environment",
        map_environment=False,
    )

    assert "environment 'test' is not configured" in output
    assert "hololab pack setup community/notice-no-environment@0.5.2" in output
