"""The setup command plans safely and delegates mutations to conda."""

from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from hololab.cli import app
from hololab.node.config import load_node_config
from hololab.pack_setup import apply_plans, installed_plans, select_python_version

runner = CliRunner()


def _manifest(
    root: Path,
    name: str,
    version: str,
    env: str,
    packages: list[str],
    *,
    python: str | None = None,
    host=False,
) -> None:
    target = root / f"{name.replace('/', '_')}@{version}"
    target.mkdir(parents=True)
    target.joinpath("manifest.yaml").write_text(
        "\n".join(
            [
                "apiVersion: hololab.dev/v1",
                "kind: Algorithm",
                f"name: {name}",
                f"version: {version}",
                "requires:",
                *([f'  python: "{python}"'] if python else []),
                "  packages: [" + ", ".join(packages) + "]",
                "runtime:",
                f"  mode: {'host' if host else 'conda'}",
                *([] if host else [f"  env: {env}"]),
                "exec:",
                "  shell: 'true'",
                "idempotency:",
                "  marker: '{{ outputs.out }}'",
                "outputs:",
                "  out: {tags: [out]}",
            ]
        )
    )


def test_setup_dry_run_merges_shared_environment(tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    _manifest(packs, "community/one", "1.0.0", "test-setup-env", ["numpy"])
    _manifest(packs, "community/two", "1.0.0", "test-setup-env", ["ffmpeg"])
    config = tmp_path / "config.yaml"
    config.write_text(f"pack_dirs: [{packs}]\n")

    result = runner.invoke(app, ["pack", "setup", "--all", "--dry-run", "--config", str(config)])

    assert result.exit_code == 0, result.output
    assert result.output.count("create or reuse conda environment named test-setup-env") == 1
    assert "numpy" in result.output and "ffmpeg" in result.output
    assert "test-setup-env" not in config.read_text().split("envs:")[-1]


def test_setup_existing_mapping_installs_only_unsatisfied_and_never_rebuilds(
    tmp_path: Path, monkeypatch
) -> None:
    packs = tmp_path / "packs"
    _manifest(packs, "community/one", "1.0.0", "kiri", ["numpy", "ffmpeg"])
    prefix = tmp_path / "existing-kiri"
    prefix.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(f"pack_dirs: [{packs}]\nenvs:\n  kiri: {prefix}\n")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[1:3] == ["list", "--prefix"]:
            package = command[-1]
            rows = [{"version": "2.0"}] if package == "numpy" else []
            return subprocess.CompletedProcess(command, 0, __import__("json").dumps(rows), "")
        if command[1:3] == ["run", "--prefix"]:
            return subprocess.CompletedProcess(command, 0, "Python 3.15.2\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("hololab.pack_setup.find_conda", lambda: "/fake/conda")
    monkeypatch.setattr("hololab.pack_setup._run", fake_run)
    cfg = load_node_config(config)
    events = apply_plans(
        installed_plans(cfg, "community/one@1.0.0", False), cfg, config, dry_run=False
    )

    assert any("numpy already satisfied" in event for event in events)
    assert any("existing Python 3.15.2" in event for event in events)
    assert any("outside the ecosystem-compatible range" in event for event in events)
    assert any(command[1] == "install" and command[-1] == "ffmpeg" for command in commands)
    assert not any(command[1] == "create" for command in commands)
    assert config.read_text() == f"pack_dirs: [{packs}]\nenvs:\n  kiri: {prefix}\n"


def test_setup_new_environment_writes_mapping_and_backup(tmp_path: Path, monkeypatch) -> None:
    packs = tmp_path / "packs"
    _manifest(packs, "community/one", "1.0.0", "test-setup-env", [])
    config = tmp_path / "config.yaml"
    config.write_text(f"pack_dirs: [{packs}]\nnode_name: before\n")

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[1:4] == ["env", "list", "--json"]:
            prefixes = []
            if any(c[1] == "create" for c in commands):
                prefixes = [str(tmp_path / "conda" / "envs" / "test-setup-env")]
            return subprocess.CompletedProcess(
                command, 0, __import__("json").dumps({"envs": prefixes}), ""
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    commands: list[list[str]] = []
    original = fake_run

    def recording(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return original(command)

    monkeypatch.setattr("hololab.pack_setup.find_conda", lambda: "/fake/conda")
    monkeypatch.setattr("hololab.pack_setup._run", recording)
    cfg = load_node_config(config)
    apply_plans(installed_plans(cfg, "community/one@1.0.0", False), cfg, config, dry_run=False)

    assert any(command[1] == "create" for command in commands)
    assert next(command for command in commands if command[1] == "create")[-1] == "python=3.11"
    assert "test-setup-env:" in config.read_text()
    assert list(tmp_path.glob("config.yaml.setup-*.bak"))


def test_python_selection_uses_default_for_broad_requirement() -> None:
    assert select_python_version(None) == "3.11"
    assert select_python_version(">=3.6") == "3.11"


def test_setup_new_environment_honors_python_constraint(tmp_path: Path, monkeypatch) -> None:
    packs = tmp_path / "packs"
    _manifest(
        packs,
        "community/constrained",
        "1.0.0",
        "constrained-env",
        [],
        python=">=3.10,<3.13",
    )
    config = tmp_path / "config.yaml"
    config.write_text(f"pack_dirs: [{packs}]\n")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[1:4] == ["env", "list", "--json"]:
            prefixes = []
            if any(c[1] == "create" for c in commands):
                prefixes = [str(tmp_path / "conda" / "envs" / "constrained-env")]
            return subprocess.CompletedProcess(
                command, 0, __import__("json").dumps({"envs": prefixes}), ""
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("hololab.pack_setup.find_conda", lambda: "/fake/conda")
    monkeypatch.setattr("hololab.pack_setup._run", fake_run)
    cfg = load_node_config(config)
    apply_plans(
        installed_plans(cfg, "community/constrained@1.0.0", False), cfg, config, dry_run=False
    )

    assert next(command for command in commands if command[1] == "create")[-1] == "python=3.11"
