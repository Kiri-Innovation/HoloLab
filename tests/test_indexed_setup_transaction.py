import subprocess
from pathlib import Path

import pytest

from hololab import pack_setup as setup


def test_indexed_packages_are_first_and_constrain_all_python_dependencies(tmp_path, monkeypatch):
    plan = setup.EnvironmentPlan(
        "gpu",
        ("timm", "torch==2.8.0+cu128", "torchvision==0.23.0+cu128"),
        None,
        (),
        tmp_path,
        package_indexes=(
            ("torch", "https://example.org/cu128"),
            ("torchvision", "https://example.org/cu128"),
        ),
    )
    calls = []
    installed = set()

    def run(command):
        calls.append(command)
        if "--index-url" in command:
            installed.add(command[-1])
        if "--constraint" in command:
            text = Path(command[command.index("--constraint") + 1]).read_text()
            assert text == "torch==2.8.0+cu128\ntorchvision==0.23.0+cu128\n"
        version = "0.23.0+cu128" if "torchvision" in command[-1] else "2.8.0+cu128"
        return subprocess.CompletedProcess(command, 0, version + "\n", "")

    monkeypatch.setattr(setup, "_run", run)
    monkeypatch.setattr(setup, "_source_satisfied", lambda c, p, raw, i, check: raw in installed)
    setup._install_indexed_packages(plan, "conda", tmp_path, [])
    installs = [c for c in calls if "pip" in c]
    assert installs[0][-1] == "torch==2.8.0+cu128"
    assert installs[1][-1] == "torchvision==0.23.0+cu128"
    assert installs[2][-1] == "timm"
    assert "--extra-index-url" not in installs[0]
    assert "--extra-index-url" not in installs[1]
    assert installs[2][installs[2].index("--extra-index-url") + 1] == setup.PYPI_SIMPLE_INDEX
    assert not any(c[1] == "install" for c in calls)


def test_changed_indexed_package_is_a_hard_failure(tmp_path, monkeypatch):
    plan = setup.EnvironmentPlan(
        "gpu",
        ("torch",),
        None,
        (),
        tmp_path,
        package_indexes=(("torch", "https://example.org/cu128"),),
    )
    monkeypatch.setattr(setup, "_source_satisfied", lambda *a: False)
    monkeypatch.setattr(
        setup, "_run", lambda c: subprocess.CompletedProcess(c, 0, "2.8.0+cu128\n", "")
    )
    with pytest.raises(setup.SetupError, match="changed during"):
        setup._install_indexed_packages(plan, "conda", tmp_path, [])
