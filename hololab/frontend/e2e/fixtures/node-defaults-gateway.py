"""Isolated real gateway for node-defaults browser acceptance; never executes jobs."""

import os
import socket
import sys
from pathlib import Path

import uvicorn
import yaml

from hololab.gateway.app import create_app
from hololab.gateway.library_config import LibraryConfig
from hololab.gateway.registry import NodeSession
from hololab.manifest import load_manifest
from hololab.protocol.messages import GpuInfo, PackInventoryEntry

root = Path(sys.argv[1])
os.environ["HOLOLAB_DEV"] = "1"
os.environ["HOLOLAB_RECIPE_AUTHORITY"] = "db"
os.environ["HOLOLAB_WORKFLOW_ROOT"] = str(root / "workflows")
app = create_app(
    db_path=root / "gateway.sqlite", library_config=LibraryConfig(staging_root=root / "staging")
)


@app.on_event("startup")
async def offer_test_packs():
    packs = []
    for suffix, values in (
        ("recommended", {"parallelism": 4, "batch_size": 2}),
        ("omitted", {}),
        ("null", {"parallelism": None, "batch_size": None}),
    ):
        path = root / suffix / "manifest.yaml"
        path.parent.mkdir()
        path.write_text(
            yaml.safe_dump(
                dict(
                    apiVersion="hololab.dev/v1",
                    kind="Algorithm",
                    name=f"community/defaults-{suffix}-linkcheck",
                    version="0.0.1",
                    arrayable=True,
                    outputs={"out": {"tags": ["text"]}},
                    runtime={"env": "test"},
                    exec={"shell": "echo test"},
                    **values,
                )
            )
        )
        manifest, digest = load_manifest(path)
        packs.append(
            PackInventoryEntry(
                name=manifest.name,
                version=manifest.version,
                manifest_hash=digest,
                manifest_path=str(path),
                source_dir=str(path.parent),
            )
        )
    app.state.registry._sessions["fixture"] = NodeSession(
        node_id="fixture",
        session_id="fixture",
        node_name="Fixture",
        ws=None,
        advertised_url=None,
        gpu=GpuInfo(),
        packs=packs,
        protocol_v=1,
        last_heartbeat_ts=float("inf"),
    )


with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    print(f"GATEWAY_PORT={listener.getsockname()[1]}", flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level="error")).run(sockets=[listener])
