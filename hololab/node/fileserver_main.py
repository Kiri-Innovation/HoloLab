"""Standalone entry point for the node file server.

The file server used to share the node's asyncio loop with the gateway
and job scheduler, but ffmpeg-heavy ``/_thumb`` / ``/_preview`` traffic
from a browser refresh (measured: 8 concurrent clients pushed API p50
from 2.2 ms to 16.7 ms; ~6-12% wall regression on a 100-shard fan-out)
made previewing interfere with running pipelines. Splitting the file
server into its own child process moves that CPU tax off the scheduler's
event loop entirely.

Invoked by :meth:`hololab.node.runtime.NodeRuntime._start_file_server` as::

    python -m hololab.node.fileserver_main
        --host 127.0.0.1 --port 8829
        --workspace-root /path/to/primary
        [--legacy-root /path/to/old ...]

Signal contract: parent (``NodeRuntime``) spawns us in a fresh session
(``start_new_session=True``) so terminal SIGINT does NOT reach us
directly — the parent's shutdown path is the single signal source.
Uvicorn's own SIGINT/SIGTERM handlers drive the graceful shutdown, which
releases the listen socket before the parent respawns on the same port
(e.g. when the UI changes ``workspace_root``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from hololab.logging import configure as configure_logging
from hololab.node.fileserver import create_fileserver_app


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="hololab-fileserver",
        description="HoloLab node file server (child process).",
    )
    p.add_argument("--host", required=True, help="Bind host, e.g. 127.0.0.1.")
    p.add_argument("--port", type=int, required=True, help="Bind port.")
    p.add_argument(
        "--workspace-root",
        type=Path,
        required=True,
        help="Primary workspace root the file server serves.",
    )
    p.add_argument(
        "--legacy-root",
        type=Path,
        action="append",
        default=[],
        help="Additional read-only root (repeatable) — legacy artifacts.",
    )
    p.add_argument(
        "--log-level",
        default="warning",
        help="Uvicorn log level (default: warning).",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG on the hololab structured logger.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    configure_logging(level="DEBUG" if args.verbose else "INFO")

    app = create_fileserver_app(
        workspace_root=args.workspace_root,
        legacy_workspace_roots=list(args.legacy_root),
    )
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=False,
        # Match the in-process ServeHandle: give uvicorn 5 s to drain and
        # release the socket so the parent's rebind on the same port after
        # a restart doesn't hit EADDRINUSE.
        timeout_graceful_shutdown=5,
    )
    server = uvicorn.Server(config)
    # ``server.run()`` installs uvicorn's default SIGINT/SIGTERM handlers,
    # which is exactly what we want: the parent sends SIGTERM on shutdown
    # or restart, uvicorn drains, we exit cleanly.
    server.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
