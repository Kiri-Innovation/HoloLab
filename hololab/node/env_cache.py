"""Env-dict snapshotting — the fast path around ``conda run``.

``conda run -p <prefix> ...`` was the historical spawn form for every
job. Its per-invocation cost is dominated by the conda CLI's own Python
startup + activate.d resolution — measured at **~2000 ms** on this box
regardless of the wrapped command. For a fan-out over N lightweight
shards (e.g. ``merge-colmap`` @ ~50 ms of real work) that translates to
~98% framework tax, per-shard.

This module captures the *same* env dict ``conda run`` would compute,
**once per env at daemon startup**. The executor then spawns each shard
with ``bash -c "<shell>"`` + ``env=<cached_dict>`` — activate.d effects
(``PATH``, ``LD_LIBRARY_PATH``, ``CUDA_HOME``, per-package exports) all
carry through, but the ~2 s CLI bootstrap is paid exactly once, not
N times.

Trade-off: package installs inside a running daemon's env are NOT
reflected in the cache — the operator must restart the node daemon to
re-snapshot. Documented in ``NodeConfig.use_env_cache``. A config kill
switch (``use_env_cache: false``) restores the historical behavior for
debugging.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from hololab.logging import get_logger

log = get_logger("node.env_cache")

# The env-snapshot subprocess runs the env's own python and prints a
# JSON dict of ``os.environ``. Python is guaranteed to exist inside a
# conda env (that's the whole point of ``runtime.env`` in a manifest),
# and JSON gives us a deterministic, encoding-safe wire format —
# unlike ``env -0`` which depends on locale handling.
_SNAPSHOT_PYTHON_SRC = "import os, json, sys; sys.stdout.write(json.dumps(dict(os.environ)))"

# What the daemon's own ``os.environ`` contributes to the snapshot.
# Same allow-list the historical executor used for ``env={}`` clean
# spawns — see the pre-refactor ``executor._clean_env``. Kept in sync
# so the "cache miss / fallback" path and the "cache hit" path behave
# identically w.r.t. inherited-from-daemon vars.
_DAEMON_ENV_KEEP = frozenset({"PATH", "HOME", "USER", "LANG", "LC_ALL", "TZ", "TMPDIR", "SHELL"})


def _daemon_env() -> dict[str, str]:
    """Return the minimal daemon-side env the snapshot subprocess inherits."""

    return {k: v for k, v in os.environ.items() if k in _DAEMON_ENV_KEEP}


async def snapshot_env(
    conda_bin: str,
    prefix: str,
    *,
    timeout_s: float = 30.0,
) -> dict[str, str]:
    """Return the fully-activated env dict for ``prefix``.

    Runs ``conda run -p <prefix> --no-capture-output python -c <src>``
    exactly once and parses the resulting JSON. Any activate.d side
    effects (``LD_LIBRARY_PATH``, ``CUDA_HOME``, per-pkg exports) end
    up in the returned dict just as ``conda run`` would apply them at
    the time of a live spawn — the caller can then hand this dict to
    ``asyncio.create_subprocess_exec(..., env=...)`` and skip the
    conda-run wrapper entirely.

    Raises:
        ``RuntimeError`` on subprocess failure, timeout, or JSON parse
        error. The caller is expected to log-and-fall-back-to-conda-run
        rather than crash the whole daemon on a single misconfigured
        env.
    """

    if not Path(prefix).is_dir():
        raise RuntimeError(f"env prefix {prefix!r} is not a directory")

    cmd = [
        conda_bin,
        "run",
        "-p",
        prefix,
        "--no-capture-output",
        "python",
        "-c",
        _SNAPSHOT_PYTHON_SRC,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_daemon_env(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        with __import__("contextlib").suppress(ProcessLookupError):
            proc.kill()
        raise RuntimeError(f"env snapshot for {prefix!r} timed out after {timeout_s:.0f}s") from exc

    if proc.returncode != 0:
        tail = stderr.decode(errors="replace")[-800:] if stderr else ""
        raise RuntimeError(
            f"env snapshot for {prefix!r} failed with exit {proc.returncode}: {tail}"
        )

    try:
        data = json.loads(stdout.decode() or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"env snapshot for {prefix!r} produced non-JSON stdout") from exc

    if not isinstance(data, dict) or not data:
        raise RuntimeError(f"env snapshot for {prefix!r} produced empty or non-dict payload")

    # Every value must be a string — subprocess env doesn't accept
    # non-str. json.loads already gives us str for JSON strings, but
    # guard against a caller-side surprise (e.g. an env var whose
    # value is legitimately unset would have been dropped by
    # ``dict(os.environ)`` already).
    return {str(k): str(v) for k, v in data.items()}


async def warmup_env_cache(
    conda_bin: str,
    envs: dict[str, str],
    *,
    timeout_s: float = 30.0,
) -> dict[str, dict[str, str]]:
    """Snapshot every configured env; return ``{prefix -> env_dict}``.

    Failures are logged and simply omitted from the returned map — the
    caller (:class:`hololab.node.runtime.NodeRuntime`) treats a missing
    entry as "fall back to ``conda run`` for this env" so a single
    broken env doesn't take the whole daemon down.

    Snapshots run sequentially. In steady state a node has 1-3 envs
    and each snapshot costs ~2 s; the total cold-start delay is
    small and paying it in parallel would only muddle logs.
    """

    cache: dict[str, dict[str, str]] = {}
    for logical, prefix in envs.items():
        try:
            env_dict = await snapshot_env(conda_bin, prefix, timeout_s=timeout_s)
        except RuntimeError as exc:
            log.warning(
                "env cache warmup failed; this env will fall back to 'conda run' per spawn",
                logical=logical,
                prefix=prefix,
                error=str(exc),
            )
            continue
        cache[prefix] = env_dict
        log.info(
            "env cache warmed",
            logical=logical,
            prefix=prefix,
            n_vars=len(env_dict),
        )
    return cache
