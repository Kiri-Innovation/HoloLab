"""REST coverage for ``GET /api/handles/{handle_id}``.

This endpoint is what the frontend hits after a workflow node reaches
``done`` — it turns the opaque ``handle_id`` from the ``job_update`` frame
into a same-origin proxy URL that the in-canvas preview drawer can stream
from. The critical bit is the workspace_root-relative sub-path calculation:
if the producing node's ``workspace_root`` is known we strip that prefix so
the URL cleanly nests under ``/proxy/{node_id}/…``; if not, we fall back
to the raw absolute path (better a debuggable URL than a 500).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.handles import Handle
from hololab.gateway.registry import NodeSession
from hololab.protocol.messages import GpuInfo


def _fake_session(node_id: str, workspace_root: str | None) -> NodeSession:
    """Inject a minimal NodeSession without opening a real WebSocket.

    ``ws`` is only touched when the gateway *sends* to the node, which the
    handle-lookup path never does — a MagicMock is enough here.
    """

    return NodeSession(
        node_id=node_id,
        session_id="sess",
        node_name="node-a",
        ws=MagicMock(),
        advertised_url=None,
        gpu=GpuInfo(),
        packs=[],
        protocol_v=1,
        workspace_root=workspace_root,
    )


def _register(client: TestClient, handle: Handle) -> None:
    """Insert a handle on the app's event loop via the TestClient portal.

    HandleBook talks to aiosqlite through a single-writer coroutine that
    lives on the ASGI loop; a raw ``asyncio.run`` would create a rival loop
    and race the writer. Going through ``client.portal`` guarantees we hit
    the same loop the app is running on.
    """

    client.portal.call(client.app.state.handles.register, handle)


def test_handle_lookup_strips_workspace_root(tmp_path: Path) -> None:
    """When the node's workspace_root is known, the proxy sub-path is relative."""

    app = create_app(db_path=tmp_path / "test.sqlite")
    with TestClient(app) as client:
        # Seed a fake session so the endpoint can find a workspace_root.
        session = _fake_session("node-a", "/hololab/ws")
        app.state.registry._sessions[session.node_id] = session

        handle = Handle(
            handle_id="h-abc",
            node_id="node-a",
            storage="file",
            tags=["splatv_file"],
            path="/hololab/ws/w/wf1/j/j1/model.splatv",
            size_bytes=1234,
            output_port_name="splatv",
        )
        _register(client, handle)

        r = client.get("/api/handles/h-abc")
        assert r.status_code == 200
        body = r.json()
        assert body["handle_id"] == "h-abc"
        assert body["node_id"] == "node-a"
        assert body["storage"] == "file"
        assert body["tags"] == ["splatv_file"]
        assert body["size_bytes"] == 1234
        assert body["output_port_name"] == "splatv"
        # The workspace_root prefix has been stripped.
        assert body["proxy_url"] == "/proxy/node-a/w/wf1/j/j1/model.splatv"
        # The producing node's local absolute path is included verbatim
        # so the frontend can pass it to ``window.flops.showDocument``
        # (Cobrowser "Open in Cocoder"). Workspace-root prefix stripping
        # is only for the proxy URL — Cobrowser wants the full path.
        assert body["absolute_path"] == "/hololab/ws/w/wf1/j/j1/model.splatv"


def test_handle_lookup_missing_workspace_root_falls_back_to_absolute(
    tmp_path: Path,
) -> None:
    """No session (node offline) means we can't strip a prefix — use the raw path."""

    app = create_app(db_path=tmp_path / "test.sqlite")
    with TestClient(app) as client:
        handle = Handle(
            handle_id="h-orphan",
            node_id="unknown-node",
            storage="dir",
            tags=["log"],
            path="/some/absolute/dir",
        )
        _register(client, handle)

        r = client.get("/api/handles/h-orphan")
        assert r.status_code == 200
        body = r.json()
        # Fallback: the absolute path lands verbatim in the sub-path with the
        # leading ``/`` stripped so the URL is still well-formed.
        assert body["proxy_url"] == "/proxy/unknown-node/some/absolute/dir"
        # ``absolute_path`` is unaffected by the strip fallback — always
        # the raw path from the handle row.
        assert body["absolute_path"] == "/some/absolute/dir"


def test_handle_lookup_returns_404_for_unknown_id(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "test.sqlite")
    with TestClient(app) as client:
        r = client.get("/api/handles/does-not-exist")
        assert r.status_code == 404
