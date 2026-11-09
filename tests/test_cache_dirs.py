"""Node-configured shared download caches (torch.hub, Hugging Face).

The same variables must reach ``pack setup`` hooks and every job spawn path, and
an unconfigured node must inject nothing (historical behaviour).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import hololab.pack_setup as pack_setup
from hololab.manifest import load_manifest
from hololab.node import executor as executor_mod
from hololab.node.config import NodeConfig, load_node_config, write_node_config
from hololab.node.runtime import _make_exec_plan

CACHE_KEYS = ("TORCH_HOME", "HF_HOME", "HF_HUB_CACHE")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch) -> Path:
    """Keep the default cache root (``<user data dir>/weights``) out of the real home."""
    home = tmp_path / "hololab-home"
    monkeypatch.setenv("HOLOLAB_HOME", str(home))
    return home


def _weights(root: Path) -> dict[str, str]:
    return {
        "TORCH_HOME": str(root / "torch"),
        "HF_HOME": str(root / "huggingface"),
        "HF_HUB_CACHE": str(root / "huggingface" / "hub"),
    }


def test_unconfigured_node_defaults_to_node_weights(isolated_home: Path) -> None:
    cfg = NodeConfig()
    assert cfg.cache_env() == _weights(isolated_home / "node" / "weights")
    env = cfg.ensure_cache_dirs()
    assert all(Path(env[key]).is_dir() for key in CACHE_KEYS)
    # The default is resolved at use time and is never written into the config.
    assert cfg.cache_root is None


def test_default_follows_xdg_data_home_when_hololab_home_unset(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("HOLOLAB_HOME")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert NodeConfig().cache_env() == _weights(tmp_path / "xdg" / "hololab" / "node" / "weights")


def test_cache_root_derives_one_directory_per_library(tmp_path: Path) -> None:
    cfg = NodeConfig(cache_root=tmp_path / "cache")
    assert cfg.cache_env() == {
        "TORCH_HOME": str(tmp_path / "cache" / "torch"),
        "HF_HOME": str(tmp_path / "cache" / "huggingface"),
        "HF_HUB_CACHE": str(tmp_path / "cache" / "huggingface" / "hub"),
    }
    env = cfg.ensure_cache_dirs()
    assert all(Path(env[key]).is_dir() for key in CACHE_KEYS)


def test_per_library_override_and_partial_configuration(tmp_path: Path) -> None:
    cfg = NodeConfig(cache_root=tmp_path / "c", cache_dirs={"huggingface": tmp_path / "hf"})
    assert cfg.cache_env()["TORCH_HOME"] == str(tmp_path / "c" / "torch")
    assert cfg.cache_env()["HF_HOME"] == str(tmp_path / "hf")
    only_torch = NodeConfig(cache_dirs={"torch": tmp_path / "t"})
    env = only_torch.cache_env()
    assert env["TORCH_HOME"] == str(tmp_path / "t")
    assert env["HF_HOME"] == str(tmp_path / "hololab-home" / "node" / "weights" / "huggingface")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cache_root": "relative/cache"},
        {"cache_dirs": {"torch": "relative"}},
        {"cache_dirs": {"unknown": "/x"}},
    ],
)
def test_invalid_cache_configuration_is_rejected(kwargs: dict) -> None:
    with pytest.raises(ValidationError):
        NodeConfig(**kwargs)


def test_cache_root_that_is_a_file_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("x")
    with pytest.raises(ValidationError):
        NodeConfig(cache_root=tmp_path / "file")


def test_cache_configuration_round_trips_through_config_file(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write_node_config(NodeConfig(cache_root=tmp_path / "c"), path)
    assert load_node_config(path).cache_env()["TORCH_HOME"] == str(tmp_path / "c" / "torch")
    write_node_config(NodeConfig(), path)
    assert load_node_config(path).cache_root is None
    assert load_node_config(path).cache_env() == _weights(
        tmp_path / "hololab-home" / "node" / "weights"
    )


# --- job spawn: every executor path -------------------------------------------------


def _child_env(plan: executor_mod.ExecPlan) -> dict[str, str]:
    lines: list[str] = []

    async def run() -> executor_mod.ExecResult:
        return await executor_mod.run_subprocess(
            plan,
            on_log=lambda stream, line: lines.append(line) if stream == "stdout" else None,
            on_progress=lambda *_: None,
            cancel_event=asyncio.Event(),
        )

    result = asyncio.run(run())
    assert result.exit_code == 0, result.stderr_tail
    return dict(line.split("=", 1) for line in lines if "=" in line)


def _fake_conda(tmp_path: Path) -> Path:
    """``conda run -p <prefix> --no-capture-output bash -lc <shell>`` without conda."""
    script = tmp_path / "fake-conda"
    script.write_text('#!/usr/bin/env bash\nshift 4\nexec "$@"\n')
    script.chmod(0o755)
    return script


@pytest.mark.parametrize("mode", ["cached_env", "conda_run", "command"])
def test_every_spawn_path_receives_cache_variables(tmp_path: Path, mode: str) -> None:
    cache = NodeConfig(cache_root=tmp_path / "cache").cache_env()
    base = {"PATH": os.environ["PATH"], "TORCH_HOME": "/env/default/torch"}
    plan = executor_mod.ExecPlan(
        shell="env",
        conda_bin=str(_fake_conda(tmp_path)),
        conda_prefix=str(tmp_path),
        working_dir=tmp_path,
        cached_env=base if mode == "cached_env" else None,
        command=["env"] if mode == "command" else None,
        extra_env=cache,
    )
    env = _child_env(plan)
    for key in CACHE_KEYS:
        assert env[key] == cache[key]
    # The shared snapshot is not mutated by one spawn's overlay.
    assert base["TORCH_HOME"] == "/env/default/torch"


def test_spawn_without_cache_configuration_is_unchanged(tmp_path: Path, monkeypatch) -> None:
    for key in CACHE_KEYS:
        monkeypatch.delenv(key, raising=False)
    plan = executor_mod.ExecPlan(
        shell="env",
        conda_bin="",
        conda_prefix="",
        working_dir=tmp_path,
        cached_env={"PATH": os.environ["PATH"], "TORCH_HOME": "/env/default/torch"},
    )
    env = _child_env(plan)
    assert env["TORCH_HOME"] == "/env/default/torch"
    assert "HF_HOME" not in env and "HF_HUB_CACHE" not in env


def _rendered(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        shell="true", working_dir=str(tmp_path), python_args=[], python_arg_batches=None
    )


def _pack(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        manifest=SimpleNamespace(runtime=SimpleNamespace(mode="conda"), progress=None),
        package_root=tmp_path,
        pack_dir=tmp_path,
    )


def test_job_plan_carries_the_node_cache_configuration(tmp_path: Path) -> None:
    cfg = NodeConfig(cache_root=tmp_path / "cache")
    plan = _make_exec_plan(
        _rendered(tmp_path), cfg, "/env", _pack(tmp_path), cached_env={}, cpu_mask=None
    )
    assert plan.extra_env == cfg.cache_env()
    plain = _make_exec_plan(
        _rendered(tmp_path), NodeConfig(), "/env", _pack(tmp_path), cached_env={}, cpu_mask=None
    )
    assert plain.extra_env == _weights(tmp_path / "hololab-home" / "node" / "weights")


# --- pack setup hook ------------------------------------------------------------------


def _hook_pack(tmp_path: Path) -> Path:
    root = tmp_path / "pack"
    root.mkdir()
    (root / "setup.sh").write_text(
        "set -euo pipefail\n"
        'printf \'{"TORCH_HOME":"%s","HF_HOME":"%s","HF_HUB_CACHE":"%s"}\' '
        '"${TORCH_HOME:-}" "${HF_HOME:-}" "${HF_HUB_CACHE:-}" > "$PACKAGE_ROOT/../seen.json"\n'
    )
    (root / "manifest.yaml").write_text(
        "apiVersion: hololab.dev/v1\nkind: Algorithm\nname: test/cache-hook\nversion: 0.1.0\n"
        "outputs: {out: {tags: [any]}}\nruntime: {env: test}\nexec: {shell: 'true'}\n"
        "setup: {script: setup.sh, shell: bash}\n"
    )
    return root / "manifest.yaml"


def _run_hooks(tmp_path: Path, monkeypatch, cfg: NodeConfig, prefix: Path) -> list[str]:
    manifest_path = (
        _hook_pack(tmp_path)
        if not (tmp_path / "pack").exists()
        else (tmp_path / "pack" / "manifest.yaml")
    )
    manifest, _ = load_manifest(manifest_path)

    def fake_run_in_env(conda, prefix, command, *, env=None, stream_output=False):
        if command[0] == "python":  # fingerprint / smoke probes
            return subprocess.CompletedProcess(command, 0, "3.12.0\n2.9.1\n13.0\n", "")
        return subprocess.run(command, text=True, capture_output=True, env=env, check=False)

    monkeypatch.setattr(pack_setup, "_run_in_env", fake_run_in_env)
    monkeypatch.setattr(pack_setup, "find_nvcc", lambda: None)
    for key in CACHE_KEYS:
        monkeypatch.delenv(key, raising=False)
    plan = pack_setup.EnvironmentPlan(
        name="test",
        packages=(),
        python=None,
        packs=(),
        prefix=prefix,
        hooks=((manifest, manifest_path),),
    )
    events: list[str] = []
    env = cfg.ensure_cache_dirs()
    pack_setup._apply_hooks(plan, "conda", prefix, events, False, env)
    return events


def test_setup_hook_and_jobs_see_identical_cache_variables(tmp_path: Path, monkeypatch) -> None:
    cfg = NodeConfig(cache_root=tmp_path / "cache")
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    _run_hooks(tmp_path, monkeypatch, cfg, prefix)
    seen = json.loads((tmp_path / "seen.json").read_text())
    job_plan = _make_exec_plan(
        _rendered(tmp_path), cfg, str(prefix), _pack(tmp_path), cached_env={}, cpu_mask=None
    )
    assert seen == cfg.cache_env() == job_plan.extra_env


def test_setup_hook_without_configuration_uses_default_weights(tmp_path: Path, monkeypatch) -> None:
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    _run_hooks(tmp_path, monkeypatch, NodeConfig(), prefix)
    seen = json.loads((tmp_path / "seen.json").read_text())
    default = _weights(tmp_path / "hololab-home" / "node" / "weights")
    assert seen == default
    receipt = next((prefix / ".hololab-pack-setups").glob("*.json"))
    assert json.loads(receipt.read_text())["fingerprint"]["cache_env"] == default


def test_moving_the_cache_reruns_the_hook(tmp_path: Path, monkeypatch) -> None:
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    first = NodeConfig(cache_root=tmp_path / "a")
    assert _run_hooks(tmp_path, monkeypatch, first, prefix)[-1].endswith("setup hook completed")
    assert _run_hooks(tmp_path, monkeypatch, first, prefix)[-1].endswith(
        "setup hook already matches fingerprint"
    )
    moved = NodeConfig(cache_root=tmp_path / "b")
    assert _run_hooks(tmp_path, monkeypatch, moved, prefix)[-1].endswith("setup hook completed")
    seen = json.loads((tmp_path / "seen.json").read_text())
    assert seen["TORCH_HOME"] == str(tmp_path / "b" / "torch")
