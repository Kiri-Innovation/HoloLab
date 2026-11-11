"""Automatic weight runtime installation never depends on pack declarations."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from hololab import pack_setup as setup
from hololab.node.config import NodeConfig


def _healthy():
    return {
        "versions": {
            "httpx": "0.28.1",
            "pydantic": "2.8.0",
            "pyyaml": "6.0.3",
            "websockets": "14.0",
            "structlog": "24.4.0",
            "jinja2": "3.1.0",
            "packaging": "24.0",
            "huggingface-hub": "0.36.2",
        },
        "editable": True,
        "error": "",
    }


@pytest.mark.parametrize("indexed", [False, True])
@pytest.mark.parametrize("initial", ["absent", "partial", "other-checkout", "old-dependency"])
def test_setup_repairs_runtime_before_hooks_and_skips_second_install(
    tmp_path, monkeypatch, indexed, initial
):
    state = _healthy()
    if initial in {"absent", "other-checkout"}:
        state["editable"] = False
    if initial == "absent":
        state["versions"] = {}
    if initial == "partial":
        del state["versions"]["structlog"]
        state["error"] = "No module named 'structlog'"
    if initial == "old-dependency":
        state["versions"]["pydantic"] = "1.10.0"
    commands = []

    def run(command):
        commands.append(command)
        assert command[2:4] == ["--prefix", str(tmp_path)]
        if "-c" in command:
            assert "-I" in command  # cwd/PYTHONPATH cannot fake installation
            return subprocess.CompletedProcess(command, 0, json.dumps(state), "")
        if "pip" in command:
            if "-e" in command:
                assert "--no-deps" in command
                assert Path(command[-1]) == Path(setup.__file__).resolve().parent.parent
                state["editable"] = True
            else:
                assert "-e" not in command
                state["versions"] = _healthy()["versions"]
                state["error"] = ""
        return subprocess.CompletedProcess(command, 0, "Python 3.11.0\n", "")

    hook_calls = []

    def hooks(*args):
        assert state == _healthy()
        hook_calls.append(True)

    monkeypatch.setattr(setup, "find_conda", lambda: "conda")
    monkeypatch.setattr(setup, "_run", run)
    monkeypatch.setattr(setup, "_apply_hooks", hooks)
    monkeypatch.setattr(setup, "_install_indexed_packages", lambda *args: None)
    cfg = NodeConfig(cache_root=tmp_path / "cache")
    plan = setup.EnvironmentPlan(
        "model",
        (),
        None,
        (),
        tmp_path,
        package_indexes=(("torch", "https://example.org/cu128"),) if indexed else (),
    )
    first = setup.apply_plans([plan], cfg, tmp_path / "config.yaml", dry_run=False)
    installs = [command for command in commands if "pip" in command]
    assert len(installs) == (2 if initial == "absent" else 1)
    assert any("verified weight runtime" in event for event in first)
    if initial == "partial":
        assert installs[0][-1] == "structlog>=24.4"
        assert "--no-deps" not in installs[0]
    commands.clear()
    second = setup.apply_plans([plan], cfg, tmp_path / "config.yaml", dry_run=False)
    assert not any("pip" in command for command in commands)
    assert any("already satisfied" in event for event in second)
    assert len(hook_calls) == 2


@pytest.mark.parametrize("failure", ["dependencies", "editable", "probe", "oserror", "verify"])
def test_runtime_failure_warns_and_allows_setup_to_continue(tmp_path, monkeypatch, failure):
    state = _healthy()
    state["editable"] = False
    if failure == "dependencies":
        state["versions"] = {}

    def run(command):
        if failure == "oserror":
            raise OSError("cannot start interpreter")
        if "-c" in command and failure != "probe":
            return subprocess.CompletedProcess(command, 0, json.dumps(state), "")
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "Python 3.11.0\n", "")
        return subprocess.CompletedProcess(command, 0 if failure == "verify" else 1, "", "offline")

    monkeypatch.setattr(setup, "_run", run)
    monkeypatch.setattr(setup, "find_conda", lambda: "conda")
    monkeypatch.setattr(setup, "_environment_python_version", lambda *args: None)
    hooks = []
    monkeypatch.setattr(setup, "_apply_hooks", lambda *args: hooks.append(True))
    plan = setup.EnvironmentPlan("model", (), None, (), tmp_path)
    events = setup.apply_plans(
        [plan], NodeConfig(cache_root=tmp_path / "cache"), tmp_path / "config.yaml", dry_run=False
    )
    warnings = [event for event in events if "warning: hololab runtime setup failed" in event]
    assert len(warnings) == 1
    assert "rerun pack setup" in warnings[0]
    assert hooks == [True]


@pytest.mark.parametrize("mapped", [False, True])
def test_dry_run_describes_runtime_without_executing(tmp_path, monkeypatch, mapped):
    monkeypatch.setattr(setup, "find_conda", lambda: None)
    monkeypatch.setattr(setup, "_run", lambda _: pytest.fail("dry run executed a command"))
    plan = setup.EnvironmentPlan("model", (), None, (), tmp_path if mapped else None)
    events = setup.apply_plans(
        [plan], NodeConfig(cache_root=tmp_path / "cache"), tmp_path / "config.yaml", dry_run=True
    )
    assert any("editable hololab" in event and "--no-deps" in event for event in events)


def test_weight_import_and_offline_resolution_in_target_interpreter(tmp_path):
    content = b"verified model weight"
    home = tmp_path / "weights"
    weight = home / "models--test--model" / "snapshots" / "v1" / "model.bin"
    weight.parent.mkdir(parents=True)
    weight.write_bytes(content)
    spec = tmp_path / "weights.json"
    spec.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "model",
                        "source": "hf:test/model@v1",
                        "patterns": ["model.bin"],
                        "sha256": {"model.bin": hashlib.sha256(content).hexdigest()},
                    }
                ]
            }
        )
    )
    # Use the same isolated interpreter mode as the installer, outside the
    # checkout, exercising cache_home's node/config import chain as well.
    program = f"""
from pathlib import Path
from hololab.weights import resolve_weights
assert resolve_weights(Path({str(spec)!r}), only=['model'], cache_dir={str(home)!r}) == {{'model': Path({str(weight.parent)!r})}}
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", program], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
