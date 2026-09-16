"""`hololab pack init` scaffold produces a valid manifest."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from hololab.cli import app
from hololab.manifest import load_manifest

runner = CliRunner()


def test_pack_init_creates_valid_manifest(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["pack", "init", "sample-algo@0.1.0", "--packs-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output

    pack_dir = tmp_path / "sample-algo@0.1.0"
    assert (pack_dir / "manifest.yaml").is_file()
    assert (pack_dir / "README.md").is_file()

    # The scaffolded manifest must round-trip through the real validator.
    manifest, _sha = load_manifest(pack_dir / "manifest.yaml")
    assert manifest.name == "sample-algo"
    assert manifest.version == "0.1.0"
    assert "out_dir" in manifest.outputs


def test_pack_init_rejects_bad_spec(tmp_path: Path) -> None:
    result = runner.invoke(app, ["pack", "init", "no-at-sign", "--packs-dir", str(tmp_path)])
    assert result.exit_code != 0


def test_pack_init_refuses_to_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "already-there@0.1.0"
    target.mkdir()
    result = runner.invoke(
        app, ["pack", "init", "already-there@0.1.0", "--packs-dir", str(tmp_path)]
    )
    assert result.exit_code != 0


@pytest.mark.parametrize("bad_spec", ["", "@", "name@", "@0.1.0"])
def test_pack_init_rejects_empty_parts(tmp_path: Path, bad_spec: str) -> None:
    result = runner.invoke(app, ["pack", "init", bad_spec, "--packs-dir", str(tmp_path)])
    assert result.exit_code != 0
