"""Node configuration — machine-specific settings.

The manifest is machine-independent (``env: kiri``); the node config supplies
the *real* prefix for every logical env name, plus gateway URL, token, ports,
workspace root, and packs directory. See docs/architecture.md#manifest-and-packs.
"""

from __future__ import annotations

import socket
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hololab.paths import (
    default_cache_root,
    default_workspace_root,
    former_default_installed_packs_dir,
    former_default_workspace_root,
    node_data_dir,
)


def _is_loopback_host(host: str | None) -> bool:
    """Whether *host* names this machine's loopback interface."""

    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _url_host(host: str) -> str:
    """Format a hostname for the authority portion of an HTTP URL."""

    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _absolute_cache_dir(value: Path | None, field: str) -> Path | None:
    if value is not None:
        value = value.expanduser()
        if not value.is_absolute():
            raise ValueError(f"{field} must be an absolute directory path")
        if value.exists() and not value.is_dir():
            raise ValueError(f"{field} must be a directory")
    return value


class CacheDirs(BaseModel):
    """Per-library overrides of ``NodeConfig.cache_root``."""

    model_config = ConfigDict(extra="forbid")

    torch: Path | None = None
    huggingface: Path | None = None

    @field_validator("torch", "huggingface")
    @classmethod
    def _absolute(cls, value: Path | None, info) -> Path | None:
        return _absolute_cache_dir(value, f"cache_dirs.{info.field_name}")


class NodeConfig(BaseModel):
    """Machine-specific node settings, loaded from ``config.yaml``."""

    model_config = ConfigDict(extra="forbid")
    pack_sources: list[str] = Field(
        default_factory=list,
        description="Additional registry root URLs. The official registry is always included first.",
    )

    @field_validator("pack_sources")
    @classmethod
    def validate_pack_sources(cls, values):
        from hololab.pack_registry import source_url

        return [source_url(value) for value in values]

    @property
    def registry_sources(self) -> list[str]:
        """Effective sources: built-in official registry plus user additions."""
        from hololab.pack_registry import configured_sources

        return configured_sources(self.pack_sources)

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
    # Gateway-reachable origin for this node's file server.  This is not a
    # browser URL: browsers always use the gateway's /proxy endpoint.
    # Leave it unset for the default single-machine deployment; see
    # ``effective_advertised_url`` below.
    advertised_url: str | None = None  # e.g. "http://192.168.0.5:8829"

    def effective_advertised_url(self) -> str | None:
        """Return the file-server origin the gateway should use.

        A node and gateway using loopback URLs are necessarily the usual
        all-in-one deployment, so an omitted origin can safely resolve to the
        local file server.  A remote gateway cannot reach a node's loopback;
        that deployment must set ``advertised_url`` explicitly.

        The value is deliberately derived at registration time instead of
        written to config.yaml, preserving ``null`` as the operator's useful
        signal that the all-in-one default is in effect.
        """

        if self.advertised_url:
            return self.advertised_url
        gateway_host = urlparse(self.gateway_url).hostname
        if not _is_loopback_host(gateway_host):
            return None
        # ``0.0.0.0``/``::`` are bind addresses, not connect targets.
        # The gateway on this same host reaches either through loopback.
        host = self.file_server_host
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        if not _is_loopback_host(host):
            return None
        return f"http://{_url_host(host)}:{self.file_server_port}"

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
    installed_packs: Path | None = Field(
        default=None,
        description=(
            "Where community packages are installed; defaults to "
            "node_data_dir()/installed_packs and is scanned before pack_dirs. "
            "Formerly ``install_dir`` (still accepted)."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _legacy_install_dir(cls, data: object) -> object:
        """Accept the former ``install_dir`` key as ``installed_packs``."""
        if isinstance(data, dict) and "install_dir" in data:
            data = dict(data)
            legacy = data.pop("install_dir")
            current = data.get("installed_packs")
            if current is not None and legacy is not None and Path(current) != Path(legacy):
                raise ValueError("install_dir (legacy) and installed_packs disagree; keep one")
            if current is None:
                data["installed_packs"] = legacy
        return data

    @field_validator("installed_packs")
    @classmethod
    def _absolute_install_dir(cls, value: Path | None) -> Path | None:
        if value is not None:
            value = value.expanduser()
            if not value.is_absolute():
                raise ValueError("installed_packs must be an absolute directory path")
            if value.exists() and not value.is_dir():
                raise ValueError("installed_packs must be a directory")
        return value

    @property
    def install_dir(self) -> Path | None:
        """Former name of ``installed_packs``; kept for callers and the settings API."""
        return self.installed_packs

    @property
    def effective_install_dir(self) -> Path:
        from hololab.gateway.pack_manager import installation_destination

        return installation_destination(self.installed_packs)

    @property
    def former_install_dir_in_use(self) -> Path | None:
        """``node_data_dir()/packs`` when it still holds packages installed there.

        That was the default installation directory before ``installed_packs``;
        it stays scanned (after the current one) for nodes that never set it.
        """
        if self.installed_packs is not None:
            return None
        former = former_default_installed_packs_dir()
        if former == self.effective_install_dir or not former.is_dir():
            return None
        return former if any(former.glob("*/*/.hololab-install.json")) else None

    @property
    def pack_scan_dirs(self) -> list[Path]:
        """Installed packages win; keep operator source configuration unchanged."""
        former = self.former_install_dir_in_use
        roots = [self.effective_install_dir, *([former] if former else []), *self.pack_dirs]
        return list(dict.fromkeys(roots))

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

    # Library content is separate from disposable job workspaces.
    # Materialization/configuration UI is introduced in the next stage.
    library_root: Path = Field(default_factory=lambda: Path.home() / "hololab" / "library")

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

    @property
    def effective_legacy_workspace_roots(self) -> list[Path]:
        """Configured legacy roots, plus the former default ``node/workspace``.

        A node that never configured ``workspace_root`` wrote artifacts to
        ``node_data_dir()/workspace`` before the default became
        ``node_data_dir()/artifacts``; keep those handles resolvable.
        """
        roots = list(self.legacy_workspace_roots)
        former = former_default_workspace_root()
        if (
            self.workspace_root == default_workspace_root()
            and former != self.workspace_root
            and former.is_dir()
            and former not in roots
        ):
            roots.append(former)
        return roots

    # Conda plumbing (explicit — never guessed from PATH)
    code_baselines_dir: Path = Field(default_factory=lambda: node_data_dir() / "code-baselines")

    # Shared download caches for libraries that keep their own local index
    # (torch.hub, Hugging Face Hub). ``pack setup`` hooks and every job spawn
    # receive the same variables (see ``cache_env``), so a model a setup hook
    # prefetches is the one jobs find, and environments and packs share one
    # copy. Precedence: ``cache_dirs.<name>`` > ``cache_root/<name>`` >
    # ``default_cache_root()/<name>`` (``<node data dir>/weights``, resolved
    # at use time so ``HOLOLAB_HOME`` / ``XDG_DATA_HOME`` are honoured).
    cache_root: Path | None = None
    cache_dirs: CacheDirs = Field(default_factory=CacheDirs)

    @field_validator("cache_root")
    @classmethod
    def _absolute_cache_root(cls, value: Path | None) -> Path | None:
        return _absolute_cache_dir(value, "cache_root")

    def cache_env(self) -> dict[str, str]:
        """Environment variables pointing libraries at the configured caches.

        ``HF_HUB_CACHE`` is set alongside ``HF_HOME`` (its default location)
        so that a more specific variable inherited from an environment
        cannot silently split downloads from what setup prefetched.
        """
        root = self.cache_root if self.cache_root is not None else default_cache_root()
        torch = self.cache_dirs.torch or root / "torch"
        hf = self.cache_dirs.huggingface or root / "huggingface"
        return {
            "TORCH_HOME": str(torch),
            "HF_HOME": str(hf),
            "HF_HUB_CACHE": str(hf / "hub"),
        }

    def ensure_cache_dirs(self) -> dict[str, str]:
        """Create the configured cache directories; return ``cache_env()``."""
        env = self.cache_env()
        for key in ("TORCH_HOME", "HF_HOME", "HF_HUB_CACHE"):
            if key in env:
                Path(env[key]).mkdir(parents=True, exist_ok=True)
        return env

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

    # CPU pinning — wrap every subprocess spawn in ``taskset -c <mask>``
    # so each concurrent shard runs on a fixed slice of cores. Meant for
    # I/O-heavy packs with non-OMP threading (COLMAP's libjpeg encode,
    # OpenCV TBB ``parallel_for``) that ``OMP_THREAD_LIMIT`` can't reach
    # — under 8-way fanout the untamed encoders push load-avg to 30+ on
    # a 16-core box and every shard's wall time balloons.
    #
    # Requires ``max_concurrent_jobs > 0`` (we need a fixed slot count
    # to slice the CPU set). When disabled or unmet the executor falls
    # back to its historical unbound spawn — same behavior as before
    # this field existed. Splits contiguously (slot 0 -> cores 0..k-1,
    # slot 1 -> cores k..2k-1, ...) so each shard keeps L2 / L3 cache
    # locality; round-robin splits would risk cross-socket allocation
    # on NUMA hosts.
    #
    # Simplest-viable MVP: static assignment. A shard that under-uses
    # its slice cannot lend cores to a peer that would benefit — the
    # rebalance would need a proper cgroup / dynamic-mask allocator.
    # ``taskset`` must be on PATH inside the daemon's env (util-linux;
    # present on every mainstream distro).
    cpu_pinning: bool = False

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
