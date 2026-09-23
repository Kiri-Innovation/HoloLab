"""Unit tests for :mod:`hololab.node.env_cache`.

We don't have a real conda env available in the test harness, so
``snapshot_env`` is exercised against a synthetic "conda run" script
that impersonates the real thing: it emits a JSON dict on stdout and
exits 0 (happy path), returns non-zero (error path), or blocks for
longer than the timeout (timeout path).

``warmup_env_cache`` is then covered end-to-end against the same
synthetic script so we lock in the "per-env failure is logged and
omitted, other envs still cache" contract.
"""

from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path

import pytest

from hololab.node.env_cache import snapshot_env, warmup_env_cache


def _fake_conda(tmp_path: Path, *, script_body: str, name: str = "fake-conda") -> Path:
    """Write an executable script that impersonates ``conda run``.

    The real command line is:
        conda run -p <prefix> --no-capture-output python -c "<src>"
    Our fake script ignores every arg and runs the caller-supplied body,
    which decides what to print on stdout / stderr and what exit code to
    return.
    """

    path = tmp_path / name
    path.write_text(f"#!/usr/bin/env bash\n{script_body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_snapshot_env_happy_path(tmp_path: Path) -> None:
    """A well-behaved fake ``conda run`` yields a full env dict."""

    prefix = tmp_path / "envs" / "kiri"
    prefix.mkdir(parents=True)

    payload = {
        "PATH": "/fake/envs/kiri/bin:/usr/bin",
        "LD_LIBRARY_PATH": "/fake/envs/kiri/lib",
        "CUDA_HOME": "/fake/envs/kiri",
        "PYTHONHASHSEED": "0",
    }
    body = f"printf '%s' {json.dumps(json.dumps(payload))}"
    fake = _fake_conda(tmp_path, script_body=body)

    result = asyncio.run(snapshot_env(str(fake), str(prefix)))

    assert result == payload
    # Every value round-trips as a str — subprocess env cannot take bytes.
    for k, v in result.items():
        assert isinstance(k, str) and isinstance(v, str)


def test_snapshot_env_missing_prefix_raises(tmp_path: Path) -> None:
    """A prefix path that doesn't exist must fail fast — we don't want
    to blame ``conda run`` for what is really a config typo."""

    fake = _fake_conda(tmp_path, script_body="printf '{}'")
    with pytest.raises(RuntimeError, match="not a directory"):
        asyncio.run(snapshot_env(str(fake), str(tmp_path / "does-not-exist")))


def test_snapshot_env_nonzero_exit_raises(tmp_path: Path) -> None:
    """A conda-side failure surfaces as RuntimeError, not silent empty dict."""

    prefix = tmp_path / "envs" / "broken"
    prefix.mkdir(parents=True)
    fake = _fake_conda(
        tmp_path,
        script_body="echo 'CondaError: prefix does not exist' >&2; exit 1",
    )

    with pytest.raises(RuntimeError, match="failed with exit 1"):
        asyncio.run(snapshot_env(str(fake), str(prefix)))


def test_snapshot_env_non_json_stdout_raises(tmp_path: Path) -> None:
    """Malformed stdout must not silently become an empty cache entry
    — that would mask a real breakage and let the daemon spawn shards
    with no PATH."""

    prefix = tmp_path / "envs" / "kiri"
    prefix.mkdir(parents=True)
    fake = _fake_conda(tmp_path, script_body="echo 'not json at all'")

    with pytest.raises(RuntimeError, match="non-JSON stdout"):
        asyncio.run(snapshot_env(str(fake), str(prefix)))


def test_snapshot_env_empty_dict_rejected(tmp_path: Path) -> None:
    """An empty JSON dict is treated as a snapshot failure — an env
    with zero variables is either misconfigured or the caller passed
    a broken command; the fallback should kick in."""

    prefix = tmp_path / "envs" / "kiri"
    prefix.mkdir(parents=True)
    fake = _fake_conda(tmp_path, script_body="printf '{}'")

    with pytest.raises(RuntimeError, match="empty or non-dict"):
        asyncio.run(snapshot_env(str(fake), str(prefix)))


def test_snapshot_env_timeout(tmp_path: Path) -> None:
    """A hanging ``conda run`` must be killed after the timeout, not
    stall daemon startup indefinitely."""

    prefix = tmp_path / "envs" / "slow"
    prefix.mkdir(parents=True)
    fake = _fake_conda(tmp_path, script_body="sleep 5")

    with pytest.raises(RuntimeError, match="timed out"):
        asyncio.run(snapshot_env(str(fake), str(prefix), timeout_s=0.5))


def test_warmup_env_cache_skips_broken_env_but_caches_others(tmp_path: Path) -> None:
    """One misconfigured env must not take down the whole warm-up.

    Fake ``conda`` inspects its ``-p`` arg (positional index 2 in the
    call ``conda run -p <prefix> --no-capture-output …``) and either
    emits a valid dict for known-good prefixes or fails for the
    broken one. The returned cache must contain the good env and
    silently omit the broken one — the executor's fallback path will
    handle the missing entry.
    """

    good_prefix = tmp_path / "envs" / "good"
    good_prefix.mkdir(parents=True)
    bad_prefix = tmp_path / "envs" / "bad"
    bad_prefix.mkdir(parents=True)

    payload_good = {"PATH": "/good/bin", "TAG": "good"}
    body = f"""
if [[ "$3" == "{good_prefix}" ]]; then
  printf '%s' {json.dumps(json.dumps(payload_good))}
else
  echo 'CondaError: bad env' >&2
  exit 1
fi
"""
    fake = _fake_conda(tmp_path, script_body=body)

    cache = asyncio.run(
        warmup_env_cache(str(fake), {"good": str(good_prefix), "bad": str(bad_prefix)})
    )

    assert str(good_prefix) in cache
    assert cache[str(good_prefix)] == payload_good
    assert str(bad_prefix) not in cache, (
        "broken env must be omitted so the executor falls back to 'conda run' for it"
    )


def test_warmup_env_cache_empty_input_returns_empty(tmp_path: Path) -> None:
    """No configured envs → empty cache, no side effects."""

    fake = _fake_conda(tmp_path, script_body="printf '{}'")
    cache = asyncio.run(warmup_env_cache(str(fake), {}))
    assert cache == {}
