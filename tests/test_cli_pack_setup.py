"""The setup command plans safely and delegates mutations to conda."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from hololab.cli import app
from hololab.node.config import load_node_config
from hololab.pack_setup import (
    SetupError,
    _check_hook_needs,
    apply_plans,
    find_nvcc,
    installed_plans,
    select_python_version,
)

runner = CliRunner()


def _manifest(
    root: Path,
    name: str,
    version: str,
    env: str,
    packages: list[str],
    *,
    python: str | None = None,
    package_indexes: dict[str, str] | None = None,
    package_checks: dict[str, str] | None = None,
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
                "  packages: [" + ", ".join(json.dumps(package) for package in packages) + "]",
                *(
                    ["  package_indexes:"]
                    + [f"    {name}: {index}" for name, index in package_indexes.items()]
                    if package_indexes
                    else []
                ),
                *(
                    ["  package_checks:"]
                    + [
                        f"    {name}: {{module: {module}, attribute: version.cuda, truthy: true}}"
                        for name, module in package_checks.items()
                    ]
                    if package_checks
                    else []
                ),
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


def test_setup_merges_unconstrained_and_constrained_requirement(tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    _manifest(packs, "community/plain", "1.0.0", "shared", ["colmap4d"])
    _manifest(packs, "community/minimum", "1.0.0", "shared", ["colmap4d>=1.0.2"])
    config = tmp_path / "config.yaml"
    config.write_text(f"pack_dirs: [{packs}]\n")

    plan = installed_plans(load_node_config(config), "community/plain@1.0.0", False)[0]

    assert plan.packages == ("colmap4d",)
    # --all is the multi-pack path whose constraints need intersection.
    plan = installed_plans(load_node_config(config), None, True)[0]
    assert plan.packages == ("colmap4d>=1.0.2",)


def test_setup_intersects_compatible_requirements(tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    _manifest(packs, "community/low", "1.0.0", "shared", ["demo>=1,<4"])
    _manifest(packs, "community/tight", "1.0.0", "shared", ["demo>=2,<3"])
    config = tmp_path / "config.yaml"
    config.write_text(f"pack_dirs: [{packs}]\n")

    package = installed_plans(load_node_config(config), None, True)[0].packages[0]

    from packaging.requirements import Requirement
    from packaging.version import Version

    specifier = Requirement(package).specifier
    assert Version("2.5") in specifier
    assert Version("1.5") not in specifier
    assert Version("3.0") not in specifier


def test_setup_rejects_disjoint_requirements(tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    _manifest(packs, "community/old", "1.0.0", "shared", ["demo<1"])
    _manifest(packs, "community/new", "1.0.0", "shared", ["demo>=2"])
    config = tmp_path / "config.yaml"
    config.write_text(f"pack_dirs: [{packs}]\n")

    with __import__("pytest").raises(SetupError, match="conflicting package requirements"):
        installed_plans(load_node_config(config), None, True)


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


def test_cuda_hook_reports_missing_compiler_in_plain_language(tmp_path: Path, monkeypatch) -> None:
    """The native-hook probe must fail before an opaque pip/build failure."""
    monkeypatch.setattr("hololab.pack_setup.find_nvcc", lambda: None)
    with __import__("pytest").raises(SetupError, match=r"CUDA compiler \(nvcc\)"):
        _check_hook_needs("conda", tmp_path, SimpleNamespace(needs=["cuda-toolkit"]))


def test_find_nvcc_uses_cuda_home_when_not_on_path(tmp_path: Path, monkeypatch) -> None:
    nvcc = tmp_path / "cuda-12.8" / "bin" / "nvcc"
    nvcc.parent.mkdir(parents=True)
    nvcc.write_text("#!/bin/sh\n")
    nvcc.chmod(0o755)
    monkeypatch.setattr("hololab.pack_setup.shutil.which", lambda _: None)
    monkeypatch.setattr("hololab.pack_setup.glob", lambda _: [])
    monkeypatch.setenv("CUDA_HOME", str(nvcc.parent.parent))
    assert find_nvcc() == str(nvcc.resolve())


def test_find_nvcc_uses_newest_usr_local_toolkit(tmp_path: Path, monkeypatch) -> None:
    old = tmp_path / "cuda-11.8" / "bin" / "nvcc"
    new = tmp_path / "cuda-12.8" / "bin" / "nvcc"
    for nvcc in (old, new):
        nvcc.parent.mkdir(parents=True)
        nvcc.write_text("#!/bin/sh\n")
        nvcc.chmod(0o755)
    monkeypatch.delenv("CUDA_HOME", raising=False)
    monkeypatch.setattr("hololab.pack_setup.shutil.which", lambda _: None)
    monkeypatch.setattr("hololab.pack_setup.glob", lambda _: [str(old), str(new)])
    assert find_nvcc() == str(new.resolve())


def test_cuda_hook_distinguishes_cpu_only_torch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("hololab.pack_setup.find_nvcc", lambda: "/cuda/bin/nvcc")
    monkeypatch.setattr("hololab.pack_setup.shutil.which", lambda _: "/usr/bin/c++")
    monkeypatch.setattr(
        "hololab.pack_setup._run_in_env",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps({"cuda_build": None, "available": False, "count": 0}), ""
        ),
    )
    with __import__("pytest").raises(SetupError, match="CPU-only PyTorch"):
        _check_hook_needs("conda", tmp_path, SimpleNamespace(needs=["cuda-toolkit"]))


def test_cuda_hook_distinguishes_no_visible_gpu(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("hololab.pack_setup.find_nvcc", lambda: "/cuda/bin/nvcc")
    monkeypatch.setattr("hololab.pack_setup.shutil.which", lambda _: "/usr/bin/c++")
    monkeypatch.setattr(
        "hololab.pack_setup._run_in_env",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps({"cuda_build": "12.8", "available": False, "count": 0}), ""
        ),
    )
    with __import__("pytest").raises(SetupError, match="no usable GPU is visible"):
        _check_hook_needs("conda", tmp_path, SimpleNamespace(needs=["cuda-toolkit"]))


def test_declared_package_index_replaces_unproven_existing_wheel(
    tmp_path: Path, monkeypatch
) -> None:
    packs = tmp_path / "packs"
    index = "https://download.example.invalid/whl/cu128"
    _manifest(
        packs, "community/gpu", "1.0.0", "gpu-env", ["torch"], package_indexes={"torch": index}
    )
    prefix = tmp_path / "gpu-env"
    prefix.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(f"pack_dirs: [{packs}]\nenvs:\n  gpu-env: {prefix}\n")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[1:3] == ["list", "--prefix"]:
            return subprocess.CompletedProcess(command, 0, '[{"version": "2.0"}]', "")
        if command[1:3] == ["run", "--prefix"]:
            return subprocess.CompletedProcess(command, 0, "Python 3.12.0\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("hololab.pack_setup.find_conda", lambda: "/fake/conda")
    monkeypatch.setattr("hololab.pack_setup._run", fake_run)
    cfg = load_node_config(config)
    apply_plans(installed_plans(cfg, "community/gpu@1.0.0", False), cfg, config, dry_run=False)

    pip = next(command for command in commands if "pip" in command)
    assert ["--index-url", index] == pip[pip.index("--index-url") : pip.index("--index-url") + 2]
    assert "--force-reinstall" in pip


def test_failed_package_check_reinstalls_despite_matching_source_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    packs = tmp_path / "packs"
    index = "https://download.example.invalid/whl/cu128"
    _manifest(
        packs,
        "community/gpu",
        "1.0.0",
        "gpu-env",
        ["torch"],
        package_indexes={"torch": index},
        package_checks={"torch": "torch"},
    )
    prefix = tmp_path / "gpu-env"
    prefix.mkdir()
    (prefix / ".hololab-package-sources.json").write_text(
        json.dumps({"torch": {"requirement": "torch", "index": index}})
    )
    config = tmp_path / "config.yaml"
    config.write_text(f"pack_dirs: [{packs}]\nenvs:\n  gpu-env: {prefix}\n")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[1:3] == ["list", "--prefix"]:
            return subprocess.CompletedProcess(command, 0, '[{"version": "2.0"}]', "")
        if command[1:3] == ["run", "--prefix"] and "import importlib" in command[-1]:
            return subprocess.CompletedProcess(command, 1, "", "CPU-only torch")
        if command[1:3] == ["run", "--prefix"]:
            return subprocess.CompletedProcess(command, 0, "Python 3.12.0\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("hololab.pack_setup.find_conda", lambda: "/fake/conda")
    monkeypatch.setattr("hololab.pack_setup._run", fake_run)
    cfg = load_node_config(config)
    apply_plans(installed_plans(cfg, "community/gpu@1.0.0", False), cfg, config, dry_run=False)

    assert any("--force-reinstall" in command for command in commands)
