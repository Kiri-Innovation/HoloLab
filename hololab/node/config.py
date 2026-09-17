"""Node configuration — machine-specific settings.

The manifest is machine-independent (``env: kiri``); the node config supplies
the *real* prefix for every logical env name, plus gateway URL, token, ports,
workspace root, and packs directory. See docs/architecture.md#manifest-and-packs.
"""

from __future__ import annotations

import socket
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

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

    # Where packs live
    packs_dir: Path = Field(default_factory=lambda: node_data_dir() / "packs")

    # Where to put job workspaces. Precedence when starting a node:
    #   CLI --workspace-root > config file value > default (~/.hololab/node/workspace).
    # Change this to relocate future artifacts (e.g. onto a data disk).
    # Existing artifacts under the old root are NOT migrated; add the old
    # path to ``legacy_workspace_roots`` below to keep those handles
    # resolvable via the preview proxy without a copy.
    workspace_root: Path = Field(default_factory=default_workspace_root)

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
