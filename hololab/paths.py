"""Filesystem paths — user data, package resources, workspace.

The one rule: **user data never lives inside the installed package**. This
keeps the "upgrade = replace binary artifact" model safe.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def user_data_dir() -> Path:
    """Root directory for all HoloLab user data on this machine.

    Precedence: ``$HOLOLAB_HOME`` > ``$XDG_DATA_HOME/hololab`` > ``~/.hololab``.
    On Windows: ``%APPDATA%/hololab``.
    """

    override = os.environ.get("HOLOLAB_HOME")
    if override:
        return Path(override).expanduser().resolve()

    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "hololab"

    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "hololab"

    return Path.home() / ".hololab"


def gateway_data_dir() -> Path:
    """Where the gateway keeps its SQLite and static caches."""

    p = user_data_dir() / "gateway"
    p.mkdir(parents=True, exist_ok=True)
    return p


def node_data_dir() -> Path:
    """Where the node keeps its config, workspace, and pack scans."""

    p = user_data_dir() / "node"
    p.mkdir(parents=True, exist_ok=True)
    return p


def gateway_sqlite_path() -> Path:
    return gateway_data_dir() / "hololab.sqlite"


def default_cache_root() -> Path:
    """Default root of the node's shared weight caches (torch.hub, Hugging Face).

    Node-owned data, so it lives beside the node's other data; follows
    ``node_data_dir()``. The library subdirectories are created by the node.
    """

    return node_data_dir() / "weights"


def default_workspace_root() -> Path:
    """Default location of job artifacts on this node."""

    return node_data_dir() / "artifacts"


def default_installed_packs_dir() -> Path:
    """Default location of community packages installed on this node."""

    return node_data_dir() / "installed_packs"


# Former defaults. Nodes that never configured these paths keep finding what
# they wrote there (see NodeConfig.effective_legacy_workspace_roots and
# NodeConfig.pack_scan_dirs); nothing is moved automatically.
def former_default_workspace_root() -> Path:
    return node_data_dir() / "workspace"


def former_default_installed_packs_dir() -> Path:
    return node_data_dir() / "packs"


def frontend_dist_dir() -> Path | None:
    """Resolve the built frontend directory shipped with the package.

    Returns ``None`` if the frontend has not been built (dev mode without
    ``npm run build``). The gateway serves a placeholder page in that case.
    """

    pkg_root = Path(__file__).resolve().parent
    candidate = pkg_root / "frontend" / "dist"
    if (candidate / "index.html").exists():
        return candidate
    return None
