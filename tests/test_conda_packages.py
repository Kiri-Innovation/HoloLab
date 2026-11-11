"""Declarative conda-only dependencies: parsing, source checks and installation."""

import json
import subprocess
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from hololab import pack_setup as setup
from hololab.manifest.requirements import Requirements


@pytest.mark.parametrize("raw", ["cuda-nvcc", "cuda-nvcc=13.0", "nvidia::cuda-nvcc=13.0.*"])
def test_conda_roundtrip(raw):
    requirements = Requirements(conda_packages=[raw])
    assert requirements.as_dict() == {"conda_packages": [raw]}
    assert not requirements.is_empty()
    assert Requirements().as_dict() == {}


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "--file=x",
        "x;id",
        "$(id)",
        "x\ny",
        "x >=1",
        "https://host::x",
        "../x",
        "nvidia/label/x::x",
        "x[build=y]",
        "x==1",
        "x=1*",
        "x=",
    ],
)
def test_invalid_conda_spec(raw):
    with pytest.raises(ValidationError):
        Requirements(conda_packages=[raw])


@pytest.mark.parametrize(
    "row, expected",
    [
        ({"name": "cuda-nvcc", "version": "13.0.88", "channel": "nvidia"}, True),
        ({"name": "cuda-nvcc", "version": "12.8.1", "channel": "nvidia"}, False),
        ({"name": "cuda-nvcc", "version": "13.0.88", "channel": "pypi"}, False),
        ({"name": "cuda-nvcc", "version": "13.0.88", "channel": "conda-forge"}, False),
        ({"name": "cuda-nvcc-tools", "version": "13.0.88", "channel": "nvidia"}, False),
    ],
)
def test_installed_records(monkeypatch, tmp_path, row, expected):
    monkeypatch.setattr(
        setup, "_run", lambda cmd: subprocess.CompletedProcess(cmd, 0, json.dumps([row]), "")
    )
    assert setup._conda_satisfied("conda", tmp_path, "nvidia::cuda-nvcc=13.0.*") is expected


def test_install_order_and_idempotence(monkeypatch, tmp_path):
    installed = False
    calls = []
    monkeypatch.setattr(setup, "find_conda", lambda: "conda")
    monkeypatch.setattr(setup, "_environment_python_version", lambda *args: None)
    monkeypatch.setattr(setup, "_satisfied", lambda *args: False)
    monkeypatch.setattr(setup, "_conda_satisfied", lambda *args: installed)

    def run(cmd):
        nonlocal installed
        calls.append(cmd)
        if "nvidia::cuda-nvcc=13.0.*" in cmd:
            installed = True
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(setup, "_run", run)
    monkeypatch.setattr(
        setup, "_install_hololab_runtime", lambda *args, **kwargs: calls.append(["hololab-runtime"])
    )
    monkeypatch.setattr(setup, "_apply_hooks", lambda *args: calls.append(["hook"]))
    plan = setup.EnvironmentPlan(
        "verify", ("demo",), None, (), tmp_path, conda_packages=("nvidia::cuda-nvcc=13.0.*",)
    )
    cfg = SimpleNamespace(ensure_cache_dirs=lambda: {})
    setup.apply_plans([plan], cfg, tmp_path / "node.yaml", dry_run=False)
    assert calls[0][-2:] == ["nvidia::cuda-nvcc=13.0.*", "--no-channel-priority"]
    assert calls[1][-2:] == ["demo", "--no-channel-priority"]
    assert calls[2:] == [["hololab-runtime"], ["hook"]]
    calls.clear()
    setup.apply_plans([plan], cfg, tmp_path / "node.yaml", dry_run=False)
    assert all("nvidia::cuda-nvcc=13.0.*" not in cmd for cmd in calls)


def test_failure_no_pip_fallback_and_dry_run(monkeypatch, tmp_path):
    plan = setup.EnvironmentPlan("verify", (), None, (), tmp_path, conda_packages=("x",))
    monkeypatch.setattr(setup, "_conda_satisfied", lambda *args: False)
    calls = []

    def run(cmd):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "solver conflict")

    monkeypatch.setattr(setup, "_run", run)
    setup._install_conda_packages(plan, "conda", tmp_path, [], True)
    assert calls == []
    with pytest.raises(setup.SetupError, match="solver conflict"):
        setup._install_conda_packages(plan, "conda", tmp_path, [], False)
    assert len(calls) == 1


def test_conda_priority_option_falls_back_only_when_unsupported(monkeypatch, tmp_path):
    plan = setup.EnvironmentPlan("verify", (), None, (), tmp_path, conda_packages=("x",))
    installed = False
    calls = []

    def satisfied(*args):
        return installed

    def run(cmd):
        nonlocal installed
        calls.append(cmd)
        if "--no-channel-priority" in cmd:
            return subprocess.CompletedProcess(
                cmd, 2, "", "error: unrecognized arguments: --no-channel-priority"
            )
        installed = True
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(setup, "_conda_satisfied", satisfied)
    monkeypatch.setattr(setup, "_run", run)
    events = []
    setup._install_conda_packages(plan, "conda", tmp_path, events, False)
    assert calls[0][-1] == "--no-channel-priority"
    assert calls[1] == calls[0][:-1]
    assert any("does not support --no-channel-priority" in event for event in events)


def test_target_nvcc_precedes_host(monkeypatch, tmp_path):
    nvcc = tmp_path / "bin/nvcc"
    nvcc.parent.mkdir()
    nvcc.write_text("#!/bin/sh\n")
    nvcc.chmod(0o755)
    monkeypatch.setattr(setup, "find_nvcc", lambda: "/host/bin/nvcc")
    assert setup._target_nvcc(tmp_path) == str(nvcc)


def test_shared_environment_conda_merge_and_conflict(tmp_path):
    for name in ["one", "two"]:
        root = tmp_path / name
        root.mkdir()
        (root / "manifest.yaml").write_text(
            "apiVersion: hololab.dev/v1\nkind: Algorithm\n"
            f"name: test/{name}\nversion: 0.1.0\n"
            'requires: {conda_packages: ["nvidia::cuda-nvcc=13.0.*"]}\n'
            "runtime: {env: verify}\noutputs: {out: {tags: [any]}}\n"
            'exec: {shell: "true"}\n'
        )
    cfg = SimpleNamespace(pack_scan_dirs=[tmp_path], envs={})
    plans = setup.installed_plans(cfg, None, True)
    plan = next(p for p in plans if p.name == "verify")
    assert plan.conda_packages == ("nvidia::cuda-nvcc=13.0.*",)
    path = tmp_path / "two/manifest.yaml"
    path.write_text(path.read_text().replace("13.0.*", "12.8.*"))
    with pytest.raises(setup.SetupError, match="conflicting conda requirements"):
        setup.installed_plans(cfg, None, True)


@pytest.mark.parametrize("version, expected", [("13.01", False), ("13.0.88", True), ("13", True)])
def test_conda_version_components(monkeypatch, tmp_path, version, expected):
    row = {"name": "cuda-nvcc", "version": version, "channel": "nvidia"}
    monkeypatch.setattr(
        setup, "_run", lambda cmd: subprocess.CompletedProcess(cmd, 0, json.dumps([row]), "")
    )
    assert setup._conda_satisfied("conda", tmp_path, "nvidia::cuda-nvcc=13.0.*") is expected


def test_new_environment_dry_run(monkeypatch, tmp_path):
    monkeypatch.setattr(setup, "find_conda", lambda: None)
    plan = setup.EnvironmentPlan(
        "verify", (), None, (), None, conda_packages=("nvidia::cuda-nvcc=13.0.*",)
    )
    events = setup.apply_plans([plan], SimpleNamespace(), tmp_path / "node.yaml", dry_run=True)
    assert any("nvidia::cuda-nvcc=13.0.*" in event for event in events)
    assert not (tmp_path / "node.yaml").exists()


@pytest.mark.parametrize(
    "name", ["c++", "g++", "clang++", "x86_64-conda-linux-gnu-c++", "x86_64-conda-linux-gnu-g++"]
)
def test_cxx_needs_uses_target_environment_without_host(monkeypatch, tmp_path, name):
    compiler = tmp_path / "bin" / name
    compiler.parent.mkdir()
    compiler.write_text("#!/bin/sh\n")
    compiler.chmod(0o755)
    monkeypatch.setattr(setup.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(setup.shutil, "which", lambda _: None)
    assert setup._target_cxx(tmp_path) == str(compiler)
    setup._check_hook_needs("conda", tmp_path, SimpleNamespace(needs=["cxx-compiler"]))


def test_cxx_needs_host_fallback_and_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/host/g++" if name == "g++" else None)
    assert setup._target_cxx(tmp_path) == "/host/g++"
    setup._check_hook_needs("conda", tmp_path, SimpleNamespace(needs=["cxx-compiler"]))
    monkeypatch.setattr(setup.shutil, "which", lambda _: None)
    with pytest.raises(setup.SetupError, match=r"requires\.conda_packages"):
        setup._check_hook_needs("conda", tmp_path, SimpleNamespace(needs=["cxx-compiler"]))


def test_target_cxx_precedes_host_and_rejects_non_executable(monkeypatch, tmp_path):
    compiler = tmp_path / "bin/x86_64-conda-linux-gnu-c++"
    compiler.parent.mkdir()
    compiler.write_text("#!/bin/sh\n")
    monkeypatch.setattr(setup.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(setup.shutil, "which", lambda _: "/host/c++")
    assert setup._target_cxx(tmp_path) == "/host/c++"
    compiler.chmod(0o755)
    assert setup._target_cxx(tmp_path) == str(compiler)
