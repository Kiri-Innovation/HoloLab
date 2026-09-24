"""Node configuration — machine-specific settings.

The manifest is machine-independent (``env: kiri``); the node config supplies
the *real* prefix for every logical env name, plus gateway URL, token, ports,
workspace root, and packs directory. See docs/architecture.md#manifest-and-packs.
"""

from __future__ import annotations

import socket
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hololab.paths import default_workspace_root, node_data_dir


class NodeConfig(BaseModel):
    """Machine-specific node settings, loaded from ``config.yaml``."""

    model_config = ConfigDict(extra="forbid")

    # Identity — see docs/architecture.md#node-identity.
    #
    # ``node_name`` is a human-readable alias that can be changed at any
    # time without affecting identity matching. ``node_id`` and
    # ``node_token`` together are the persistent identity: on first
    # register the gateway mints both and returns them in RegisterOk,
    # and the node writes them here so every subsequent reconnect
    # presents the same pair (see :meth:`NodeRuntime._handshake`).
    node_name: str = Field(default_factory=socket.gethostname)
    node_id: str | None = None
    node_token: str | None = None

    # Where to connect
    gateway_url: str = "ws://127.0.0.1:8828"  # base http/ws URL; /ws/node is appended
    token: str | None = None

    # Where to listen for the file server
    file_server_host: str = "127.0.0.1"
    file_server_port: int = 8829
    advertised_url: str | None = None  # e.g. "http://192.168.0.5:8829"

    # Where packs live. ``pack_dirs`` is the modern list-of-paths form —
    # a node scans every entry so a developer can drop custom packs in
    # a separate directory outside the HoloLab repo (ComfyUI custom_nodes
    # style) and register just the path in this list, no vendoring
    # required. See docs/writing-a-pack.md.
    #
    # Backward compatibility: the legacy scalar ``packs_dir`` field is
    # still accepted on load. If the config declares only ``packs_dir``,
    # the model validator promotes it to a single-entry ``pack_dirs``.
    # If both are present, ``packs_dir`` is prepended (first-wins) so
    # existing artifacts stay reachable. New writes always use
    # ``pack_dirs`` — the legacy field is set to None so unrelated
    # keys don't linger in config.yaml.
    pack_dirs: list[Path] = Field(
        default_factory=list,
        description=(
            "List of directories the node scans for packs; conflicts "
            "on ``name@version`` resolve first-wins (later dirs' "
            "duplicates are skipped with a warning). See "
            "docs/writing-a-pack.md. Defaulted from the legacy "
            "``packs_dir`` scalar or from ``node_data_dir()/packs`` "
            "when neither field is set on load."
        ),
    )
    packs_dir: Path | None = Field(
        default=None,
        description=(
            "DEPRECATED — legacy single-directory form. Read on load "
            "and merged into ``pack_dirs`` (front of the list). Written "
            "back as None."
        ),
    )

    @model_validator(mode="after")
    def _merge_legacy_packs_dir(self) -> NodeConfig:
        """Fold ``packs_dir`` (legacy scalar) into ``pack_dirs`` (list).

        Merge rules:
            * If the config declares only the legacy scalar, promote it
              to a single-entry list — the pre-migration behavior stays
              identical (``config.pack_dirs == [<the scalar>]``).
            * If both are set, prepend the scalar to the list — the
              first-wins scan order means the legacy path retains its
              historical primacy over anything the operator added
              later.
            * If neither is set, fall back to the default
              ``node_data_dir()/packs`` so a fresh install has a
              working root without touching config.yaml.

        The scalar is cleared on the way out so a subsequent
        ``write_node_config`` doesn't carry the deprecated key
        forward.
        """

        if self.packs_dir is not None:
            merged: list[Path] = [self.packs_dir]
            for p in self.pack_dirs:
                if p not in merged:
                    merged.append(p)
            # Bypass pydantic's frozen-by-default via object.__setattr__;
            # NodeConfig isn't frozen but future-proofing costs nothing.
            object.__setattr__(self, "pack_dirs", merged)
            object.__setattr__(self, "packs_dir", None)
        if not self.pack_dirs:
            # Both fields absent — apply the historical default.
            object.__setattr__(self, "pack_dirs", [node_data_dir() / "packs"])
        return self

    # Where to put job workspaces. Precedence when starting a node:
    #   CLI --workspace-root > config file value > default (~/.hololab/node/workspace).
    # Change this to relocate future artifacts (e.g. onto a data disk).
    # Existing artifacts under the old root are NOT migrated; add the old
    # path to ``legacy_workspace_roots`` below to keep those handles
    # resolvable via the preview proxy without a copy.
    workspace_root: Path = Field(default_factory=default_workspace_root)

    # Optional fast-path root for **input staging** only — the per-job
    # directory exposed to packs as ``{{ staging_dir }}``. Point this at
    # a tmpfs mount (e.g. ``/dev/shm/hololab``) to let I/O-heavy packs
    # (COLMAP image_undistorter, ffmpeg decode) stage their inputs in
    # RAM and lift the shared-SSD contention that dominates 8-way fan-out
    # wall time (see the image-undistort perf analysis: colmap wall
    # time expanded 5.3 s → 22 s under 8 concurrent shards, ~90% of
    # which was disk queue). Packs that don't reference
    # ``{{ staging_dir }}`` are unaffected; when this is ``None`` the
    # binding transparently equals ``scratch_dir`` so an existing pack
    # keeps its historical single-directory layout.
    #
    # **Do not** put output-side scratch on tmpfs by conflating this
    # with ``workspace_root``: cross-filesystem ``os.link`` returns
    # ``EXDEV`` and the pack's publish step falls back to a full copy,
    # which cancels the tmpfs win. Packs are expected to keep
    # scratch-for-output (``{{ scratch_dir }}``, on ext4) hardlink-able
    # to their declared output handles.
    #
    # Sizing: image-undistort at ``parallelism=8`` peaks at ~1 GiB of
    # per-shard staging (~58 MiB input x 8 shards + headroom). ``/dev/shm``
    # defaults to half of RAM on Linux so a 32 GiB tmpfs is trivial;
    # keep an eye on any co-tenant that also targets it.
    staging_root: Path | None = Field(default=None)

    # Additional roots the file server should search for older artifacts.
    # Read-only in the sense that the node never writes here — new jobs
    # always land under ``workspace_root``. When a proxy request comes
    # in, the file server tries each root in order (primary first, then
    # legacy in list order) and serves the first hit. Gateway performs
    # the equivalent strip when computing preview URLs.
    #
    # Add the old default here when you move ``workspace_root`` off
    # ~/.hololab so previously-produced .splatv / logs / etc. still
    # open in the UI without a filesystem copy.
    legacy_workspace_roots: list[Path] = Field(default_factory=list)

    # Conda plumbing (explicit — never guessed from PATH)
    conda_bin: str = "conda"
    envs: dict[str, str] = Field(default_factory=dict)  # logical name → prefix path

    # Machine-wide cap on how many jobs this node runs concurrently. The
    # gateway schedules a per-node-per-fanout cap via ``GraphNode.parallelism``;
    # this daemon-side cap is the hard ceiling that protects the box's
    # GPU/CPU/RAM from a mis-configured graph or from many workflows
    # dispatching against the same node at once. 0 = unlimited (preserves
    # historical behavior; every accepted ``job_assign`` spawns
    # immediately). Effective concurrency for one fan-out is
    # ``min(graph_node.parallelism, max_concurrent_jobs)`` when this is
    # non-zero.
    max_concurrent_jobs: int = Field(default=0, ge=0)

    # Env cache — cold-snapshot each configured conda env at daemon
    # startup and reuse that env dict for every spawn, bypassing
    # ``conda run``'s ~2 s CLI bootstrap per shard. On a 100-shard
    # fan-out of a lightweight pack (e.g. ``merge-colmap``) this
    # collapses the per-shard framework tax from ~2 s to ~40-80 ms.
    #
    # Trade-off — hot updates:
    #   Installing a new package into a running env is NOT reflected
    #   in the cache. Restart the node daemon after
    #   ``conda install`` / ``pip install`` inside a configured env
    #   so the snapshot re-runs.
    #
    # Set to ``false`` to fully restore the pre-refactor behavior
    # (every spawn goes through ``conda run``). See
    # :mod:`hololab.node.env_cache` for how the snapshot is built.
    use_env_cache: bool = True

    # Timeout for the one-shot env snapshot subprocess (``conda run
    # python -c "..."``). A misconfigured env that hangs at import
    # time must not block daemon startup forever. On timeout the
    # affected env falls back to the ``conda run`` path per spawn
    # (a warning is logged; other envs still cache normally).
    env_cache_timeout_s: float = Field(default=30.0, gt=0.0)

    # Cobrowser integration — the Flops device id for this compute node.
    # When the HoloLab UI runs inside the Flops built-in browser, the
    # "Open in Cocoder" button feeds this to ``window.flops.showDocument``
    # as ``deviceId`` so Cocoder opens the artifact on the right
    # machine. Null / absent when the operator hasn't configured Flops
    # integration on this node. See docs/cobrowser-integration.md.
    flops_executor_id: str | None = None


def _default_config_path() -> Path:
    return node_data_dir() / "config.yaml"


def load_node_config(path: Path | None = None) -> NodeConfig:
    """Load config from ``path`` (default ``~/.hololab/node/config.yaml``).

    If the file doesn't exist, returns a default config. Missing conda envs
    won't fail at load time; jobs that reference an unknown logical env will
    fail at assignment.
    """

    if path is None:
        path = _default_config_path()

    if not path.exists():
        return NodeConfig()

    data = yaml.safe_load(path.read_text()) or {}
    return NodeConfig.model_validate(data)


def write_node_config(cfg: NodeConfig, path: Path | None = None) -> Path:
    """Persist a config to disk. Returns the path written."""

    if path is None:
        path = _default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    dump = cfg.model_dump(mode="json")
    # ``Path`` fields serialize as strings via ``mode="json"``; that's fine.
    path.write_text(yaml.safe_dump(dump, sort_keys=False))
    return path
